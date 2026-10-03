"""A backup's side of continuing a Group Chat: fence receipts, announcements, queries and reports.

Every request is signed with the caller's room identity key, which this installation pins from
the room's own custody configuration. ``answer_fence`` fences the host's epoch here and promises
the next one to the requesting successor (one successor per epoch, #105079), then answers with a
signed receipt, sealed to the successor because it carries run evidence and continuation grants.
``answer_learn`` takes a successor's verified transition: this computer follows it, sets aside
whatever it holds beyond the shared history first, or reports a conflict when it follows another
successor at that epoch. ``answer_query`` tells any configured computer who this copy follows,
which also serves as the heartbeat backups use to notice an offline host.
"""

from __future__ import annotations

import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from gateway import hosted_rooms as rooms
from gateway import hosted_room_succession as succession
from gateway.hosted_room_succession import ProofInvalid, SuccessionError


@dataclass(frozen=True)
class BackupContext:
    """What a backup needs to answer: its room store, its Runs store, grant minting and its service."""
    custody_db: Path
    runs_store: Any
    mint_grants: Callable[[str, str, int], list[dict[str, Any]]] | None = None
    replace_grants: Callable[[str, str, int], list[dict[str, Any]]] | None = None
    service: Any = None


def _transaction(db_path: Path):
    return rooms._transaction(db_path, immediate=True)


def check_request_locked(conn, request: Mapping[str, Any], *, domain: bytes, requester_field: str) -> str:
    """The room of a fresh request signed by one of its configured computers, else a refusal."""
    room_id, requester = request.get("room_id"), request.get(requester_field)
    issued_at = request.get("issued_at")
    if (not isinstance(room_id, str) or not isinstance(requester, str) or type(issued_at) not in (int, float)
            or abs(time.time() - issued_at) > succession.REQUEST_FRESHNESS_SECONDS):
        raise SuccessionError("the request is malformed or stale", reason="invalid_succession_request")
    custodians = succession.custodians_by_id(succession.configuration_locked(conn, room_id))
    if requester not in custodians or not succession.verify_locked(
            conn, room_id, requester, domain, succession._unsigned(request), request.get("signature")):
        raise SuccessionError("the requester is not a computer of this group", reason="not_owner")
    return room_id


def serving_locked(conn, room_id: str) -> bool:
    """Whether this computer hosts the room and acts as its host: not paused, not continued on two."""
    from gateway.hosted_room_succession_return import paused_locked
    room = conn.execute("SELECT authority_gateway_id FROM hosted_rooms WHERE room_id=? AND disbanded_at IS NULL",
                        (room_id,)).fetchone()
    conflict = succession.load_record_locked(conn, room_id, "conflict") or {}
    return (room is not None and room[0] == succession.local_install_id() and not paused_locked(conn, room_id)
            and conflict.get("state") != "active")


def copy_head_locked(conn, room_id: str) -> dict[str, Any]:
    """This installation's view of the room: the room it hosts, or the copy it keeps."""
    room = conn.execute("SELECT authority_gateway_id, authority_epoch, next_seq FROM hosted_rooms WHERE room_id=? "
                        "AND disbanded_at IS NULL", (room_id,)).fetchone()
    if room is not None:
        hosts = room[0] == succession.local_install_id()
        return {"authoritative": hosts, "serving": hosts and serving_locked(conn, room_id),
                "authority_gateway_id": room[0], "authority_epoch": int(room[1]), "latest_seq": int(room[2]) - 1}
    copy = conn.execute("""SELECT authority_gateway_id, authority_epoch, quarantine_reason, disbanded_at, last_seq
        FROM hosted_room_replicas WHERE room_id=?""", (room_id,)).fetchone()
    if copy is None or copy[2] is not None or copy[3] is not None:
        raise SuccessionError("no usable copy of this group is kept here", reason="target_not_ready")
    return {"authoritative": False, "serving": False, "authority_gateway_id": copy[0],
            "authority_epoch": int(copy[1]), "latest_seq": int(copy[4])}


def refusal(context: BackupContext, room_id: str, *, head: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """This backup's signed succession record, so a successor can name who it refused for."""
    from gateway import hosted_room_fence as fence
    state = {"room_id": room_id, "custodian_install_id": succession.local_install_id(),
             **fence.room_fence_state(context.runs_store.path, room_id), "at": time.time()}
    if head is not None:
        state["live_host"] = {"install_id": head["authority_gateway_id"], "epoch": head["authority_epoch"]}
    return {**state, "signature": succession.sign(succession.ANSWER, state)}


def _seal(request: Mapping[str, Any], reply: Mapping[str, Any]) -> dict[str, Any]:
    return {"sealed": succession.seal(str(request.get("reply_key") or ""), dict(reply),
                                      aad=succession.digest(dict(request)).encode("ascii"))}


def open_sealed_reply(private_key: Any, reply: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    opened = succession.open_sealed(private_key, reply.get("sealed") if isinstance(reply, dict) else None,
                                    aad=succession.digest(dict(request)).encode("ascii"))
    if not isinstance(opened, dict):
        raise ProofInvalid("a sealed reply has the wrong shape")
    return opened


def answer_fence(context: BackupContext, request: Mapping[str, Any]) -> dict[str, Any]:
    """Fence the host's epoch here, promise the next one, and answer with a sealed, signed receipt.

    The watermark is read under the copy's writer after the fence commits, so no page of a
    fenced epoch can land behind a receipt. A host that is serving its group refuses; a host
    that paused (fenced elsewhere, or continued on two) answers like any backup, so its whole
    history can be adopted.
    """
    from gateway import hosted_room_fence as fence
    request = dict(request)
    with closing(rooms._read_connection(context.custody_db)) as conn:
        room_id = check_request_locked(conn, request, domain=succession.FENCE_REQUEST,
                                       requester_field="candidate_install_id")
        head = copy_head_locked(conn, room_id)
        configuration = succession.configuration_locked(conn, room_id)
    candidate = request["candidate_install_id"]
    fence_epoch, promise_epoch = request.get("fence_epoch"), request.get("promise_epoch")
    keeping = candidate == head["authority_gateway_id"] and head["authority_gateway_id"] == (
        succession.host_entry(configuration) or {}).get("install_id")
    if not succession.is_eligible(configuration, candidate) and not keeping:
        raise SuccessionError("that computer may not continue this group", reason="not_owner")
    if head["serving"]:
        raise SuccessionError("this computer hosts the group and is reachable", reason="host_reachable",
                              detail=refusal(context, room_id, head=head))
    if type(fence_epoch) is not int or fence_epoch < head["authority_epoch"]:
        raise SuccessionError("this copy already follows a later host", reason="room_authority_superseded",
                              detail=refusal(context, room_id))
    try:
        state = fence.fence_and_promise(context.runs_store.path, room_id=room_id, fence_epoch=fence_epoch,
                                        promise_epoch=promise_epoch, candidate_install_id=candidate)
    except fence.RoomFenceError as exc:
        raise SuccessionError(str(exc), reason=exc.code, detail=refusal(context, room_id)) from exc
    with _transaction(context.custody_db) as conn:
        mark = succession.watermark_locked(conn, room_id)
        configuration = succession.configuration_locked(conn, room_id)
        origin = succession.origin_locked(conn, room_id) or succession.log_origin_locked(
            conn, room_id, head["authority_gateway_id"])
        # The host this copy followed and the promised successor both keep the origin's member sessions.
        for gateway_id, epoch, role in ((head["authority_gateway_id"], head["authority_epoch"], "authority"),
                                        (candidate, promise_epoch, "promised")):
            succession.record_lineage_locked(conn, room_id, origin_install_id=origin, gateway_id=gateway_id,
                                             epoch=epoch, role=role)
    grants = context.mint_grants(room_id, candidate, promise_epoch) if context.mint_grants else None
    evidence = context.runs_store.room_run_evidence(room_id, through_epoch=state["fenced_epoch"],
                                                    limit=succession.MAX_RUN_EVIDENCE)
    receipt = succession.fence_receipt(
        room_id=room_id, custodian_install_id=succession.local_install_id(), fence_state=state, watermark=mark,
        configuration_seq=int(configuration.get("configuration_seq") or 0),
        request_digest=succession.digest(request), evidence_digest=succession.digest(evidence))
    return _seal(request, {"receipt": {**receipt, "signature": succession.sign(succession.FENCE_RECEIPT, receipt)},
                           "run_evidence": evidence, "continuation_grants": grants})


def open_fence_reply(conn, room_id: str, reply: Any, *, private_key: Any, request: Mapping[str, Any],
                     to_epoch: int, successor: str) -> dict[str, Any]:
    """The successor checks one sealed answer: receipt signature, request binding and evidence digest."""
    opened = open_sealed_reply(private_key, reply, request)
    custodians = succession.custodians_by_id(succession.configuration_locked(conn, room_id))
    receipt = succession.check_receipt_locked(conn, room_id, opened.get("receipt"), to_epoch=to_epoch,
                                              successor=successor, custodians=custodians)
    if receipt["request_digest"] != succession.digest(dict(request)):
        raise ProofInvalid("a fence receipt answers a different request")
    if receipt["evidence_digest"] != succession.digest(opened.get("run_evidence")):
        raise ProofInvalid("a fence receipt's run evidence was altered")
    return {"receipt": opened["receipt"], "run_evidence": opened.get("run_evidence") or {"runs": []},
            "continuation_grants": opened.get("continuation_grants") or []}


def _announced(request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The announced transition event and the event before it, checked for shape."""
    transition, fork_event = request.get("transition"), request.get("fork_event")
    try:
        payload = transition["payload"]
        int(transition["seq"]), int(payload["from_epoch"]), int(payload["to_epoch"])
        str(payload["successor_gateway_id"]), str(payload["proof_kind"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProofInvalid("the announced transition has the wrong shape") from exc
    if payload["successor_gateway_id"] != request.get("successor_install_id"):
        raise ProofInvalid("the announced transition names another successor")
    return dict(transition), dict(fork_event) if isinstance(fork_event, dict) else None


def answer_learn(context: BackupContext, request: Mapping[str, Any]) -> dict[str, Any]:
    """A successor announces its verified transition; this computer follows it.

    Whatever this computer holds beyond the shared history (an old host's own tail, or events a
    copy got that the successor never adopted) is set aside first. A computer that follows, or
    is, a rival successor at the same epoch records the conflict and answers
    ``room_authority_conflict`` with its own transition, so both pause until the owner chooses;
    with the owner's choice (``decision`` and the kept host's ``lineage``) it steps aside instead.
    """
    from gateway import hosted_room_fence as fence
    from gateway import hosted_room_succession_move as move
    from gateway import hosted_room_succession_return as returning
    request = dict(request)
    transition, fork_event = _announced(request)
    payload = transition["payload"]
    successor, kind = str(payload["successor_gateway_id"]), str(payload["proof_kind"])
    to_epoch, from_epoch = int(payload["to_epoch"]), int(payload["from_epoch"])
    with closing(rooms._read_connection(context.custody_db)) as conn:
        room_id = check_request_locked(conn, request, domain=succession.LEARN, requester_field="successor_install_id")
        head = copy_head_locked(conn, room_id)
        known = succession.same_event(succession.event_at_locked(conn, room_id, int(transition["seq"])), transition)
        rival = succession.own_transition_locked(conn, room_id, from_epoch=from_epoch)
        lineage = request.get("lineage") if isinstance(request.get("lineage"), dict) else None
        if lineage is not None and rival is None:
            rival = succession.own_transition_locked(conn, room_id, from_epoch=int(
                lineage["event"]["payload"]["from_epoch"]))
        mine = succession.latest_transition_locked(conn, room_id)
    if head["authority_epoch"] > to_epoch:
        raise SuccessionError("this copy already follows a later host", reason="room_authority_superseded",
                              detail=refusal(context, room_id))
    ctx = move.MoveContext(db_path=context.custody_db, runs_store=context.runs_store, service=context.service)
    if not known and rival is not None and rival["payload"]["successor_gateway_id"] != successor:
        decision = request.get("decision")
        if decision is None or lineage is None:
            if head["authoritative"]:
                move.record_conflict(context.custody_db, room_id, mine=mine, theirs={
                    "event": transition, "fork_event": fork_event})
            raise SuccessionError("this group was continued on two computers", reason="room_authority_conflict",
                                  detail={"transition": {"event": rival, "fork_event": None} if mine is None
                                          else mine})
        # The owner kept the other lineage: step aside now, and follow its next epoch once caught up.
        stepped = returning.step_down(ctx, room_id, lineage["event"], lineage.get("fork_event"),
                                      decision=decision, follow=False)
        if stepped is None:
            raise ProofInvalid("the owner's choice could not be verified here")
        move.resolve_conflict(context.custody_db, room_id, decision)
        return _seal(request, {"rebased": True, "fence": fence.room_fence_state(context.runs_store.path, room_id),
                               "continuation_grants": None})
    if not known:
        if head["authoritative"] or head["latest_seq"] >= int(transition["seq"]):
            if returning.step_down(ctx, room_id, transition, fork_event, follow=False) is None:
                raise ProofInvalid("the announced transition could not be verified here")
        else:
            with closing(rooms._read_connection(context.custody_db)) as conn:
                try:
                    succession.verify_proof_locked(
                        conn, room_id, proof_kind=kind, proof=payload["proof"], from_epoch=from_epoch,
                        to_epoch=to_epoch, successor=successor, fork_seq=int(transition["seq"]) - 1,
                        configuration=succession.configuration_through_locked(conn, room_id,
                                                                              int(transition["seq"]) - 1))
                except ProofInvalid:
                    if head["latest_seq"] < int(transition["seq"]) - 1:
                        # Verified against the history before it, once this copy holds that history.
                        raise SuccessionError("this copy is still catching up", reason="succession_behind",
                                              detail=refusal(context, room_id)) from None
                    raise
    try:
        state = fence.learn_authority(context.runs_store.path, room_id=room_id, epoch=to_epoch, install_id=successor)
    except fence.RoomAuthorityConflict as exc:
        raise SuccessionError(str(exc), reason=exc.code, detail=refusal(context, room_id)) from exc
    with _transaction(context.custody_db) as conn:
        succession.record_lineage_locked(conn, room_id, origin_install_id=str(payload["proof"].get(
            "origin_install_id") or ""), gateway_id=successor, epoch=to_epoch, role=kind,
            proof_digest=payload["proof_digest"])
    grants = context.replace_grants(room_id, successor, to_epoch) if context.replace_grants else None
    return _seal(request, {"fence": state, "continuation_grants": grants})


def answer_query(context: BackupContext, request: Mapping[str, Any]) -> dict[str, Any]:
    """Who this copy follows and how far it holds; for an eligible successor, also its run evidence."""
    from gateway import hosted_room_fence as fence
    from gateway import hosted_room_succession_move as move
    request = dict(request)
    with closing(rooms._read_connection(context.custody_db)) as conn:
        room_id = check_request_locked(conn, request, domain=succession.QUERY, requester_field="requester_install_id")
        head = copy_head_locked(conn, room_id)
        configuration = succession.configuration_locked(conn, room_id)
        attempt = succession.load_record_locked(conn, room_id, "move")
        conflict = succession.load_record_locked(conn, room_id, "conflict") or {}
        from_epoch = request["from_epoch"] if type(request.get("from_epoch")) is int else None
        answer = {"room_id": room_id, "responder_install_id": succession.local_install_id(),
                  "request_digest": succession.digest(request), "hosting": head["serving"],
                  "authority": {"gateway_id": head["authority_gateway_id"], "epoch": head["authority_epoch"]},
                  "latest_seq": head["latest_seq"], "watermark": succession.watermark_locked(conn, room_id),
                  "transition": succession.latest_transition_locked(conn, room_id, from_epoch),
                  "lineage": succession.latest_transition_locked(conn, room_id),
                  # A continuation in progress is recognizable, so a rival is told who holds the epoch.
                  "move": {key: attempt.get(key) for key in ("to_epoch", "state", "step", "updated_at")}
                  if attempt else None,
                  "restarting_until": move.restarting_until(conn, room_id) if head["authoritative"] else None,
                  "conflict": {"state": conflict.get("state"), "hosts": conflict.get("hosts")} if conflict else None,
                  "decision": conflict.get("decision")}
    answer["fence"] = fence.room_fence_state(context.runs_store.path, room_id)
    if succession.is_eligible(configuration, request["requester_install_id"]):
        answer["run_evidence"] = context.runs_store.room_run_evidence(
            room_id, through_epoch=head["authority_epoch"], limit=succession.MAX_RUN_EVIDENCE)
    return {**answer, "signature": succession.sign(succession.ANSWER, answer)}


def answer_report(custody_db: Path, request: Mapping[str, Any]) -> dict[str, Any]:
    """The successor keeps a returning host's evidence beside its own reconciliation, unmerged."""
    request = dict(request)
    with _transaction(custody_db) as conn:
        room_id = check_request_locked(conn, request, domain=succession.REPORT, requester_field="reporter_install_id")
        record = succession.load_record_locked(conn, room_id, "move")
        if record is None:
            raise SuccessionError("no continuation of this group is recorded here", reason="target_not_ready")
        reports = [item for item in record.get("reports", [])
                   if item.get("reporter_install_id") != request["reporter_install_id"]][-15:]
        record["reports"] = [*reports, {key: request[key] for key in (
            "reporter_install_id", "from_epoch", "fork_seq", "divergent", "runs", "tail", "issued_at")
            if key in request}]
        succession.save_record_locked(conn, room_id, "move", record)
    return {"recorded": True, "room_id": room_id}


def answer_decision(context: BackupContext, request: Mapping[str, Any]) -> dict[str, Any]:
    """The owner's choice after ``continued_on_two`` reaches the other computer."""
    from gateway import hosted_room_succession_move as move
    request = dict(request)
    with closing(rooms._read_connection(context.custody_db)) as conn:
        room_id = check_request_locked(conn, request, domain=succession.DECISION, requester_field="signer_install_id")
        succession.verify_decision_locked(conn, room_id, request)
    return move.apply_decision(context, room_id, request)
