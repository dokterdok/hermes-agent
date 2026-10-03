"""Continuing a group by hand across real gateway identities and stores; peers answered by their real handlers."""

from contextlib import closing

import pytest

from gateway import hosted_room_custody as custody
from gateway import hosted_room_fence as fence
from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_move as move
from gateway import hosted_room_succession_status as status_module
from gateway import hosted_rooms as rooms
from tests.gateway.fixtures.succession import (
    ROOM, context, copy_to, events, head, home_room, make_gateways, message)


@pytest.fixture
def gateways(tmp_path):
    values = make_gateways(tmp_path, "h", "s", "p")
    home_room(values, "h", messages=3, successors=("s",))
    copy_to(values["h"], values["s"])
    copy_to(values["h"], values["p"])
    yield values
    for gateway in values.values():
        gateway.close()


def continue_on(gateway, gateways, *, down=("h",), **options):
    with gateway.acting():
        ctx = context(gateway, gateways, down=down, **options)
        preview = move.preview(ctx, ROOM, gateway.install_id)
        return preview, move.continue_here(ctx, ROOM, gateway.install_id, preview_id=preview["preview_id"],
                                           confirm=True)


def test_the_owner_continues_on_a_designated_backup_and_the_other_backups_follow(gateways):
    h, s, p = gateways["h"], gateways["s"], gateways["p"]
    preview, result = continue_on(s, gateways)
    assert preview["target"] == {"install_id": s.install_id, "name": "S", "operator_name": "S Operator"}
    assert preview["owner"] == {"name": "Dana"}
    assert preview["behind_by"] == 0 and preview["at_risk"] == {"count": 0}
    assert preview["work"] == {"completed": 0, "elsewhere": 0, "unknown": 0, "waiting_for_host": 0}
    assert [bot["member_id"] for bot in preview["unavailable_bots"]] == ["writer", "reviewer"]
    assert preview["cautions"] == [{"code": "host_may_be_running"}]
    assert result["state"] == "ok" and result["host"]["install_id"] == s.install_id
    assert result["this_install"]["role"] == "host"
    assert result["previous_host"]["install_id"] == h.install_id
    assert head(s)["authoritative"] and head(s)["authority_epoch"] == 2
    transition = next(event for event in events(s) if event["kind"] == "authority.transition")
    payload = transition["payload"]
    assert payload["proof_kind"] == "attested" and payload["successor_gateway_id"] == s.install_id
    assert (payload["reason"], payload["from_name"], payload["to_name"], payload["at_risk"]) == ("manual", "H", "S", 0)
    assert payload["text"] == "This group now continues on S."
    with s.acting(), closing(rooms._read_connection(s.db)) as conn:
        configured = custody.configuration_locked(conn, ROOM)
    roles = {entry["install_id"]: (entry["role"], entry["successor"]) for entry in configured["custodians"]}
    assert roles[s.install_id] == ("authority", False) and roles[h.install_id] == ("custodian", False)
    # The backup that learned the move fences the old epoch and follows the new host.
    state = fence.room_fence_state(p.runs.path, ROOM)
    assert state["fenced_epoch"] == 1 and state["authority"] == {"epoch": 2, "install_id": s.install_id}
    copy_to(s, p)
    assert head(p)["authority_gateway_id"] == s.install_id and head(p)["authority_epoch"] == 2
    # Continuing again resumes nothing and changes nothing.
    with s.acting():
        again = move.continue_here(context(s, gateways, down=("h",)), ROOM, s.install_id,
                                   preview_id=preview["preview_id"], confirm=True)
    assert again["state"] == "ok" and head(s)["authority_epoch"] == 2


def test_continuing_needs_the_owner_this_computer_and_its_consent(gateways):
    s, p = gateways["s"], gateways["p"]
    with s.acting():
        for ctx, target, reason in (
                (context(s, gateways, down=("h",), subject="uid:999"), s.install_id, "not_owner"),
                (context(s, gateways, down=("h",)), p.install_id, "target_not_local")):
            with pytest.raises(succession.SuccessionError) as refused:
                move.preview(ctx, ROOM, target)
            assert refused.value.reason == reason
        assert refused.value.detail == {"target": {"install_id": p.install_id, "name": "P"}}
        # This computer's operator may act for the owner.
        operator = context(s, gateways, down=("h",), subject=None, operator=True)
        assert move.preview(operator, ROOM, s.install_id)["target"]["install_id"] == s.install_id
        custody.set_local_consent(s.db, room_id=ROOM, allowed=False)
        with pytest.raises(succession.SuccessionError) as refused:
            move.preview(context(s, gateways, down=("h",)), ROOM, s.install_id)
    assert refused.value.reason == "target_not_ready"
    # A backup the owner never designated cannot continue the group.
    with p.acting():
        with pytest.raises(succession.SuccessionError) as refused:
            move.preview(context(p, gateways, down=("h",), operator=True), ROOM, p.install_id)
    assert refused.value.reason == "target_not_ready"


def test_a_reachable_host_refuses_and_a_stale_preview_is_refused(gateways):
    h, s = gateways["h"], gateways["s"]
    with s.acting():
        with pytest.raises(succession.SuccessionError) as refused:
            move.preview(context(s, gateways, down=()), ROOM, s.install_id)
        assert refused.value.reason == "host_reachable"
        ctx = context(s, gateways, down=("h",))
        preview = move.preview(ctx, ROOM, s.install_id)
    with h.acting():
        message(h.db, "user:late", gateway=h.install_id)
    copy_to(h, gateways["p"])
    with s.acting():
        with pytest.raises(succession.SuccessionError) as refused:
            move.continue_here(ctx, ROOM, s.install_id, preview_id=preview["preview_id"], confirm=True)
        assert refused.value.reason == "preview_stale"
        assert status_module.status(ctx, ROOM)["last_attempt"]["error"] == "preview_stale"
        assert move.preview(ctx, ROOM, s.install_id)["behind_by"] == 1


def test_a_successor_adopts_the_most_complete_reachable_copy(gateways):
    h, s, p = gateways["h"], gateways["s"], gateways["p"]
    with h.acting():
        for number in range(2):
            message(h.db, f"user:extra:{number}", gateway=h.install_id)
    copy_to(h, p)
    preview, result = continue_on(s, gateways)
    assert preview["behind_by"] == 2
    assert result["state"] == "ok"
    texts = [event["payload"].get("text") for event in events(s) if event["kind"] == "message.user"]
    assert texts[-2:] == ["user:extra:0", "user:extra:1"]


def test_the_returning_host_steps_down_keeps_its_own_messages_apart_and_reports(gateways):
    from gateway import hosted_room_succession_return as returning
    h, s, p = gateways["h"], gateways["s"], gateways["p"]
    continue_on(s, gateways)
    # Unaware, the old host kept going while it was cut off.
    with h.acting():
        for number in range(2):
            message(h.db, f"user:alone:{number}", gateway=h.install_id)
        demoted = returning.check(context(h, gateways, down=()), ROOM)
    assert demoted["state"] == "stepped_down" and demoted["separate_events"] == 2
    assert head(h)["authoritative"] is False and head(h)["authority_gateway_id"] == s.install_id
    with h.acting():
        page = returning.branch_log(h.db, ROOM, demoted["branch_id"])
        status = status_module.status(context(h, gateways, down=()), ROOM)
    assert [event["payload"]["text"] for event in page["events"]] == ["user:alone:0", "user:alone:1"]
    assert status["state"] == "moved_away" and status["moved"]["separate_events"] == 2
    assert status["moved"]["to"] == {"install_id": s.install_id, "name": "S"}
    assert status["actions"] == [{"action": "open_on", "target": s.install_id}]
    assert fence.room_fence_state(h.runs.path, ROOM)["authority"] == {"epoch": 2, "install_id": s.install_id}
    with s.acting(), closing(rooms._read_connection(s.db)) as conn:
        reports = succession.load_record_locked(conn, ROOM, "move")["reports"]
    assert reports[0]["reporter_install_id"] == h.install_id and reports[0]["divergent"] == {"events": 2}
    # The old host's copy caught up with the new host and verified its transition itself.
    assert [event["kind"] for event in events(h, "hosted_room_replica_events")][-3:] == [
        "authority.transition", "custody.configured", "succession.state"]


def test_a_continuation_another_computer_holds_is_named_and_a_retry_moves_past_it(tmp_path):
    gateways = make_gateways(tmp_path, "h", "s", "t", "p")
    try:
        home_room(gateways, "h", successors=("s", "t"))
        for name in ("s", "t", "p"):
            copy_to(gateways["h"], gateways[name])
        s, t, p = gateways["s"], gateways["t"], gateways["p"]
        # t already fenced the host's epoch at p for itself.
        fence.fence_and_promise(p.runs.path, room_id=ROOM, fence_epoch=1, promise_epoch=2,
                                candidate_install_id=t.install_id)
        with s.acting():
            ctx = context(s, gateways, down=("h", "t"))
            preview = move.preview(ctx, ROOM, s.install_id)
            with pytest.raises(succession.SuccessionError) as refused:
                move.continue_here(ctx, ROOM, s.install_id, preview_id=preview["preview_id"], confirm=True)
            assert refused.value.reason == "room_authority_promised"
            assert refused.value.detail["other"] == {"install_id": t.install_id, "name": "T"}
            assert status_module.status(ctx, ROOM)["last_attempt"]["error"] == "room_authority_promised"
            preview = move.preview(ctx, ROOM, s.install_id)
            result = move.continue_here(ctx, ROOM, s.install_id, preview_id=preview["preview_id"], confirm=True)
        assert result["state"] == "ok" and head(s)["authority_epoch"] == 3
        assert fence.room_fence_state(p.runs.path, ROOM)["authority"] == {"epoch": 3, "install_id": s.install_id}
    finally:
        for gateway in gateways.values():
            gateway.close()


def test_stepping_down_is_one_transaction(gateways, monkeypatch):
    from gateway import hosted_room_succession_return as returning
    h, s = gateways["h"], gateways["s"]
    continue_on(s, gateways)
    with h.acting():
        message(h.db, "user:alone", gateway=h.install_id)
    before = events(h)

    def crash(*args, **kwargs):
        raise RuntimeError("power cut")

    monkeypatch.setattr(succession, "record_lineage_locked", crash)
    with h.acting(), pytest.raises(RuntimeError):
        transition = next(event for event in events(s) if event["kind"] == "authority.transition")
        fork = next(event for event in events(s) if event["seq"] == transition["seq"] - 1)
        returning.demote_to_custody(h.db, room_id=ROOM, transition=transition, fork_event=fork)
    assert head(h)["authoritative"] and events(h) == before
    assert returning.branches(h.db, ROOM) == []
