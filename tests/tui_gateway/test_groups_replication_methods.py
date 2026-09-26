"""Tests for the ``groups.replicate`` / ``groups.promote`` / ``groups.demote``
JSON-RPC surface — cross-gateway room durability."""

from __future__ import annotations

import pytest

import tui_gateway.server as srv
from tui_gateway import methods_groups

MEMBERS = [{"kind": "bot", "id": "planner"}]


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / ".hermes"
    path.mkdir()
    (path / "profiles" / "ops").mkdir(parents=True)
    (path / "profiles" / "ops" / "config.yaml").write_text("{}\n")  # identity marker: local roster
    monkeypatch.setenv("HERMES_HOME", str(path))
    methods_groups.stop_hosted_room_service(timeout=1.0)
    methods_groups.start_hosted_room_service()
    yield path
    methods_groups.stop_hosted_room_service(timeout=1.0)


def _result(envelope):
    assert "error" not in envelope, envelope
    return envelope["result"]


def _error(envelope):
    assert "error" in envelope, envelope
    return envelope["error"]


def _authority_page(tmp_path, gateway_id="install:" + "a" * 32, n=3):
    """Build a real room + log on a SEPARATE 'remote authority' DB and return
    its replay page, as a replicating client would fetch via groups.log."""
    from gateway import hosted_rooms as rooms

    db = tmp_path / "remote-authority.db"
    rooms.create_room(
        db,
        room_id="room-1",
        name="Field Room",
        members=MEMBERS,
        authority_gateway_id=gateway_id,
    )
    for index in range(n):
        rooms.append_event(
            db,
            room_id="room-1",
            event_id=f"e{index}",
            kind="message.user",
            actor={"kind": "user", "id": "tek"},
            payload={"text": f"msg {index}"},
            authority_gateway_id=gateway_id,
            authority_epoch=1,
        )
    return rooms.read_events(db, room_id="room-1", since_seq=0, limit=100)




def test_replicate_then_state_roundtrip(home, tmp_path):
    page = _authority_page(tmp_path)
    result = _result(
        srv._methods["groups.replicate"](
            1,
            {
                "room_id": "room-1",
                "room_name": "Field Room",
                "members": MEMBERS,
                "page": page,
            },
        )
    )
    assert result["ingested"] == 3
    state = _result(srv._methods["groups.replica_state"](2, {"room_id": "room-1"}))
    assert state["last_seq"] == 3
    assert state["authority"] == page["authority"]


def test_promote_requires_confirm_and_takes_over(home, tmp_path):
    page = _authority_page(tmp_path)
    _result(
        srv._methods["groups.replicate"](
            1,
            {
                "room_id": "room-1",
                "room_name": "Field Room",
                "members": MEMBERS,
                "page": page,
            },
        )
    )

    refused = _error(srv._methods["groups.promote"](2, {"room_id": "room-1"}))
    assert refused["code"] == 4118

    promoted = _result(
        srv._methods["groups.promote"](3, {"room_id": "room-1", "confirm": True})
    )
    assert promoted["authority_epoch"] == 2
    assert promoted["executable"] is False
    assert promoted["previous_gateway_id"] == page["authority"]["gateway_id"]

    # The room is now hosted locally with full history + claim event.
    # confirm=true is not a fence: the copied log stays, and execution does not.
    log = _result(srv._methods["groups.log"](4, {"room_id": "room-1"}))
    kinds = [event["kind"] for event in log["events"]]
    assert kinds == ["message.user"] * 3 + ["authority.claimed"]
    assert log["authority"]["epoch"] == 2
    claim = log["events"][-1]
    assert claim["payload"]["promoted_from_replica"] is True
    from gateway.hosted_rooms import (
        RoomQuarantinedError, append_event, default_db_path, local_authority_gateway_id)
    with pytest.raises(RoomQuarantinedError):
        append_event(
            default_db_path(),
            room_id="room-1",
            event_id="after-confirm",
            kind="message.user",
            actor={"kind": "user", "id": "tek"},
            payload={"text": "should not run"},
            authority_gateway_id=local_authority_gateway_id(),
            authority_epoch=promoted["authority_epoch"],
        )

    sent = _error(srv._methods["groups.send"](
        5,
        {
            "room_id": "room-1",
            "event_id": "after-promote",
            "payload": {"text": "should not run", "thread_id": "thread-1"},
        },
    ))
    assert sent["code"] == 4111
    assert "unsafe_replica_promotion" in sent["message"]
    assert sent["data"]["reason"] == "room_authority_quarantined"
    service = methods_groups.get_hosted_room_service()
    assert service is not None
    assert all(binding.room_id != "room-1" for binding in service.bindings())
    import sqlite3
    with sqlite3.connect(default_db_path()) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hosted_room_driver_tasks'"
        ).fetchone()
        tasks = 0 if table is None else conn.execute(
            "SELECT COUNT(*) FROM hosted_room_driver_tasks WHERE room_id=?", ("room-1",)
        ).fetchone()[0]
    assert tasks == 0
    again = _result(srv._methods["groups.log"](6, {"room_id": "room-1"}))
    assert [event["kind"] for event in again["events"]] == kinds


def test_demote_fences_local_room_against_newer_epoch(home):
    from gateway.hosted_rooms import local_authority_gateway_id

    _result(
        srv._methods["groups.create"](
            1,
            {
                "room_id": "room-1",
                "name": "Local room",
                "members": [
                    {
                        "member_id": "default",
                        "profile": "default",
                        "handle": "hermes",
                    },
                    {"member_id": "ops", "profile": "ops", "handle": "ops"},
                ],
            },
        )
    )
    observed_gateway = "install:" + "b" * 32
    result = _result(
        srv._methods["groups.demote"](
            2,
            {
                "room_id": "room-1",
                "observed_gateway_id": observed_gateway,
                "observed_epoch": 2,
            },
        )
    )
    assert result["idempotent"] is False
    assert result["authority_gateway_id"] == observed_gateway

    # Local sends at the stale authority now fail.
    envelope = srv._methods["groups.send"](
        3,
        {
            "room_id": "room-1",
            "event_id": "stale-send",
            "actor": {"kind": "user", "id": "tek"},
            "payload": {"text": "should fence"},
        },
    )
    assert "error" in envelope
    assert local_authority_gateway_id() != observed_gateway


def test_replicate_rejects_gapped_page(home, tmp_path):
    from gateway import hosted_rooms as rooms

    _authority_page(tmp_path, n=5)
    db = tmp_path / "remote-authority.db"
    gapped = rooms.read_events(db, room_id="room-1", since_seq=2, limit=100)
    envelope = srv._methods["groups.replicate"](
        1,
        {
            "room_id": "room-1",
            "room_name": "Field Room",
            "members": MEMBERS,
            "page": gapped,
        },
    )
    assert _error(envelope)["code"] == 4116
