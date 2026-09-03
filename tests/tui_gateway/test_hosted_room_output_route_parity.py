"""Integration preserves source terminal and durable-route fencing boundaries."""

import json
import sqlite3
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway import hosted_room_driver as driver
from gateway import hosted_room_discussion as discussion
from gateway import hosted_room_links, hosted_rooms
from tests.tui_gateway.test_hosted_room_artifact_service import _ArtifactPeerClient
from tests.tui_gateway.test_hosted_room_grant_fingerprint import peers as peers
from tests.tui_gateway.test_hosted_room_publication_consistency import (
    _send,
    _task,
    _tick,
)
from tests.tui_gateway.test_hosted_room_refresh_observation import _context
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError


def _settled_peer_output(peers, legacy_ack):
    first, worker, register = peers
    tokens, _dispatch, _catalog = _context(first)
    old, winner = tokens["winner"], tokens["stale"]
    register(first, old)
    worker._hydrate_persisted_peer_route("room-1", "member-peer")
    room = hosted_rooms.room_state(worker.db_path, room_id="room-1")
    hosted_rooms.append_event(
        worker.db_path,
        room_id="room-1",
        event_id="output",
        kind="message.user",
        actor={"kind": "user", "id": "local"},
        authority_gateway_id=room["authority_gateway_id"],
        authority_epoch=room["authority_epoch"],
        payload={"text": "@reviewer produce a file", "thread_id": "work"},
    )
    plan = discussion.plan_next_task(
        room, worker._events("room-1"), local_profiles=("default",)
    ).task
    assert plan is not None
    if legacy_ack:
        payload = dict(plan.payload)
        payload.pop("recipient_member_ids")
        plan = replace(plan, payload=payload)
    driver.admit_task(
        worker.db_path, plan.identity, payload=plan.payload, clock=time.time
    )
    lease = driver.acquire_lease(
        worker.db_path,
        room_id="room-1",
        gateway_id=room["authority_gateway_id"],
        authority_epoch=room["authority_epoch"],
        process_generation="output-route",
        ttl_seconds=300,
        clock=time.time,
    )
    attempt = driver.start_task(
        worker.db_path,
        plan.identity,
        lease,
        expected_cancel_generation=0,
        clock=time.time,
    )
    driver.settle_task(
        worker.db_path,
        attempt,
        settlement_id="output-result",
        status="settled",
        clock=time.time,
        result={
            "text": "File",
            "run_id": "remote-run",
            "artifacts": _ArtifactPeerClient().manifest,
        },
    )
    return room, driver.get_task(worker.db_path, plan.identity), plan, old, winner


@pytest.mark.parametrize("hydrate", [False, True])
@pytest.mark.parametrize("legacy_ack", [False, True])
def test_output_error_status_keeps_the_operation_grant_observation(
    peers, monkeypatch, hydrate, legacy_ack
):
    first, worker, register = peers
    room, task, plan, old, winner = _settled_peer_output(peers, legacy_ack)

    def old_request_fails(self, **kwargs):
        assert kwargs["grant"] == old
        register(first, winner)
        if hydrate:
            worker.status_with_grant_fingerprints("room-1")
        raise PeerRunsHTTPError(
            "old request failed", status_code=401, error_code="invalid_room_grant"
        )

    monkeypatch.setattr(
        PeerRunsHTTPClient,
        "acknowledge_artifacts" if legacy_ack else "discard_artifacts",
        old_request_fails,
    )
    with pytest.raises(RuntimeError, match="repaired route"):
        if legacy_ack:
            worker._import_terminal_artifacts(
                room=room, task=task, plan=plan, events=worker._events("room-1")
            )
        else:
            worker._retire_failed_terminal_artifacts(room=room, task=task, plan=plan)
    stored = hosted_rooms.room_link_record(
        first.db_path, room_id="room-1", member_id="member-peer"
    )
    assert stored["grant"] == winner and stored["status"] == "ready"
    assert driver.get_task(worker.db_path, plan.identity)["result"] == task["result"]


@pytest.mark.parametrize("hydrate", [False, True])
def test_unchanged_file_catalog_cannot_return_a_stale_route(peers, hydrate):
    first, worker, register = peers
    route = worker.peer_routes[("room-1", "member-peer")]
    stored = hosted_room_links.load_room_link(
        worker.db_path, room_id="room-1", member_id="member-peer"
    )
    catalog = json.loads(stored.as_record()["catalog_json"])

    def probe(**kwargs):
        assert kwargs["grant"] == route.grant
        register(first, "winner.room.grant")
        if hydrate:
            worker.status_with_grant_fingerprints("room-1")
        return {"catalog": catalog}

    with pytest.raises(hosted_rooms.HostedRoomError, match="grant changed"):
        worker._refresh_peer_attachment_catalog(
            "room-1", "member-peer", route, SimpleNamespace(probe=probe)
        )
    current = hosted_room_links.load_room_link(
        worker.db_path, room_id="room-1", member_id="member-peer"
    )
    assert current.grant == "winner.room.grant"
    assert current.catalog == stored.catalog and current.status == "ready"


@pytest.mark.parametrize("same_thread", [False, True])
@pytest.mark.parametrize("status", ["settled", "failed", "deferred"])
def test_text_terminal_conflict_retries_next_prepare_without_artifact_state(
    tmp_path, monkeypatch, same_thread, status
):
    service, task, attempt = _task(tmp_path, monkeypatch)
    result = (
        {"text": "Text result"} if status == "settled" else {"error": "Text failure"}
    )
    if status == "deferred":
        driver.defer_not_admitted_task(
            service.db_path, attempt, reason="member_unavailable", clock=time.time
        )
    else:
        driver.settle_task(
            service.db_path,
            attempt,
            settlement_id="text-result",
            status=status,
            result=result,
            clock=time.time,
        )
    immutable = driver.get_task(service.db_path, task["identity"])
    append = service._append_plan

    def interleaved(*args, **kwargs):
        with monkeypatch.context() as pause:
            pause.setattr(service, "prepare_room", lambda binding: None)
            _send(service, "later", thread="work" if same_thread else "other")
        return append(*args, **kwargs)

    with monkeypatch.context() as pause:
        pause.setattr(service, "_append_plan", interleaved)
        _tick(service)

    def own():
        return [
            event
            for event in service._events("publication")
            if event["payload"].get("task_id") == task["identity"].task_id
        ]

    assert own() == []
    assert service._artifact_retry_keys("publication") == set()
    _tick(service)
    assert [event["kind"] for event in own()] == (
        ["turn.deferred"]
        if status == "deferred"
        else ["turn.cancelled"]
        if same_thread
        else ["message.member", "turn.settled"]
        if status == "settled"
        else [f"turn.{status}"]
    )
    replay = own()
    _tick(service)
    assert own() == replay
    current = driver.get_task(service.db_path, task["identity"])
    assert current["result"] == immutable["result"]
    assert current["execution_generation"] == immutable["execution_generation"]
    assert service._artifact_retry_keys("publication") == set()


def test_artifact_tombstone_keeps_shared_route_fence(peers, monkeypatch):
    first, _second, register = peers
    room = hosted_rooms.room_state(first.db_path, room_id="room-1")
    monkeypatch.setattr(
        PeerRunsHTTPClient, "revoke_grant", lambda self, **kwargs: {"revoked": True}
    )
    first.begin_room_disband("room-1")
    assert first._room_is_disbanding("room-1")
    first.revoke_room_routes("room-1")
    first.retire_and_disband_room(
        "room-1",
        expected_gateway_id=room["authority_gateway_id"],
        expected_epoch=room["authority_epoch"],
    )
    assert first._room_is_disbanding("room-1") is False
    assert hosted_rooms.room_link_retirement_started(first.db_path, room_id="room-1")
    with pytest.raises(hosted_rooms.HostedRoomError, match="fenced"):
        register(first, "late.registration.grant")
    assert (
        hosted_rooms.room_link_record(
            first.db_path, room_id="room-1", member_id="member-peer"
        )
        is None
    )


@pytest.mark.parametrize(
    "limit,label",
    [("MAX_ROOM_ID_CHARS", "room_id"), ("MAX_ACTOR_ID_CHARS", "target_profile")],
)
def test_existing_reservation_rollback_keeps_public_limit_binding(
    tmp_path, monkeypatch, limit, label
):
    db = tmp_path / "state.db"
    claims = {
        "grant_id": "grant-test",
        "room_id": "room-1",
        "home_install_id": "install-home",
        "authority_gateway_id": "install-home",
        "authority_epoch": 1,
        "member_id": "member-reviewer",
        "target_install_id": "install-target",
        "target_profile": "reviewer",
        "issued_at": 1,
    }
    snapshot = hosted_rooms.reserve_peer_room(
        db, claims=claims, expires_at=10000, now=1
    )
    monkeypatch.setattr(hosted_rooms, limit, 2)
    with pytest.raises(hosted_rooms.HostedRoomError, match=label):
        hosted_rooms.restore_peer_room_reservations(
            db, claims=claims, snapshot=snapshot
        )
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        assert [
            dict(row)
            for row in conn.execute("SELECT * FROM hosted_room_peer_reservations")
        ] == snapshot["expected_rows"]
