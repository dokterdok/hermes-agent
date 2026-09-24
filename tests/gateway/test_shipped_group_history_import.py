"""Released Desktop Group Chat records upgrade into inert hosted history."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import time
from types import SimpleNamespace

import pytest

from gateway import hosted_room_driver as driver
from gateway import hosted_rooms as rooms
from tui_gateway.hosted_room_service import HostedRoomService



def released_group() -> dict:
    """Shape produced from the shipped ``hermes-bots`` local-storage GroupChat record."""
    return {
        "room_id": "release-room",
        "name": "Release",
        "source_id": "hermes.plugin.hermes-bots.group-chats:release-room",
        "members": [
            {
                "source_member_id": "local-sentinel",
                "name": "Sentinel",
                "profile": "default",
                "handle": "sentinel",
                "connection_id": "desktop-local",
                "remote_source": False,
            },
            {
                "source_member_id": "local-reviewer",
                "name": "Reviewer",
                "profile": "reviewer",
                "handle": "reviewer",
                "connection_id": "desktop-local",
                "remote_source": False,
            },
            {
                "source_member_id": "remote-builder",
                "name": "Remote Builder",
                "profile": "builder",
                "handle": "builder",
                "connection_id": "older-gateway",
                "connection_label": "Workshop Mac",
                "remote_source": True,
            },
        ],
        "history": [
            {
                "source_entry_id": "shipped-history-1",
                "at_ms": 1_700_000_000_000,
                "author_kind": "user",
                "author_name": "You",
                "text": "Keep this shipped history",
                "thread_id": "thread-1",
            },
            {
                "source_entry_id": "shipped-history-2",
                "at_ms": 1_700_000_001_000,
                "author_kind": "member",
                "author_name": "Sentinel",
                "member_source_id": "local-sentinel",
                "text": "Earlier shipped result",
                "thread_id": "thread-1",
            },
        ],
        "held_work": [
            {
                "source_work_id": "remote-builder:session-before-upgrade",
                "at_ms": 1_700_000_002_000,
                "member_source_id": "remote-builder",
                "state": "uncertain",
                "description": "The old room did not prove whether this work finished.",
            }
        ],
    }

def import_group(db, *, authorize_write=None):
    return rooms.import_shipped_group_history(
        db,
        **released_group(),
        local_profiles=("default", "reviewer"),
        authority_gateway_id="gateway-a",
        authorize_write=authorize_write,
        now=1_800_000_000,
    )

def test_released_history_members_and_uncertain_work_import_once_without_execution(tmp_path):
    db = tmp_path / "shared-state.db"
    writes: list[bool] = []

    result = import_group(db, authorize_write=lambda conn: writes.append(conn.in_transaction))
    repeated = import_group(db, authorize_write=lambda conn: writes.append(conn.in_transaction))
    after_profile_change = rooms.import_shipped_group_history(
        db,
        **released_group(),
        local_profiles=("default",),
        authority_gateway_id="gateway-a",
        authorize_write=lambda conn: writes.append(conn.in_transaction),
        now=1_800_000_001,
    )

    assert result["idempotent"] is False
    assert repeated == {**result, "room": {**result["room"], "idempotent": True}, "idempotent": True}
    assert writes == [True, True, True]
    assert result["room"]["room_id"] == "release-room"
    assert result["imported_history"] == 2
    assert result["held_work"] == 1
    assert result["held_members"] == 1
    assert [member["display_name"] for member in result["room"]["members"]] == [
        "Sentinel", "Reviewer", "Remote Builder"
    ]
    remote = result["room"]["members"][2]
    assert remote["availability"] == {
        "state": "authorization_required",
        "reason": "remote_execution_not_authorized",
    }
    assert remote["source"]["connection_id"] == "older-gateway"
    assert after_profile_change["idempotent"] is True
    assert after_profile_change["held_members"] == 2
    reviewer = after_profile_change["room"]["members"][1]
    assert reviewer["availability"] == {
        "state": "authorization_required",
        "reason": "local_profile_unavailable",
    }
    assert "target" not in reviewer

    restored = rooms.import_shipped_group_history(
        db,
        **released_group(),
        local_profiles=("default", "reviewer"),
        authority_gateway_id="gateway-a",
        authorize_write=lambda conn: writes.append(conn.in_transaction),
        now=1_800_000_002,
    )
    assert restored["room"]["members"][1]["availability"] == {"state": "ready"}
    assert restored["room"]["members"][1]["target"] == {"kind": "local", "profile": "reviewer"}

    page = rooms.read_events(db, room_id="release-room", since_seq=0, limit=20)
    assert [(event["seq"], event["kind"], event["payload"].get("text")) for event in page["events"]] == [
        (1, "history.imported", "Keep this shipped history"),
        (2, "history.imported", "Earlier shipped result"),
        (3, "history.held", None),
    ]
    assert page["events"][1]["payload"]["author"] == {
        "kind": "member", "name": "Sentinel", "member_id": result["room"]["members"][0]["member_id"]
    }
    assert page["events"][2]["payload"]["state"] == "uncertain"
    assert rooms.imported_history_context(db, room_id="release-room", thread_id="thread-1").find(
        "Earlier shipped result"
    ) >= 0

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM hosted_rooms").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM hosted_room_events").fetchone() == (3,)
        assert conn.execute("SELECT COUNT(*) FROM hosted_room_history_imports").fetchone() == (1,)
        for table in ("hosted_room_driver_tasks", "hosted_room_remote_runs", "hosted_room_links"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)

def test_import_is_atomic_on_denial_interruption_and_conflicting_retries_fail_closed(tmp_path, monkeypatch):
    db = tmp_path / "shared-state.db"

    def denied(_conn):
        raise PermissionError("permission_denied")

    with pytest.raises(PermissionError, match="permission_denied"):
        import_group(db, authorize_write=denied)
    assert rooms.list_rooms(db) == []

    finish = rooms._history_import_result
    monkeypatch.setattr(
        rooms, "_history_import_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(InterruptedError("simulated interruption")))
    with pytest.raises(InterruptedError, match="simulated interruption"):
        import_group(db)
    monkeypatch.setattr(rooms, "_history_import_result", finish)
    assert rooms.list_rooms(db) == []

    import_group(db)
    changed = released_group()
    changed["history"] = [{**changed["history"][0], "text": "different old bytes"}, *changed["history"][1:]]
    with pytest.raises(rooms.RoomConflictError, match="different import content"):
        rooms.import_shipped_group_history(
            db,
            **changed,
            local_profiles=("default", "reviewer"),
            authority_gateway_id="gateway-a",
            now=1_800_000_001,
        )
    assert [event["payload"].get("text") for event in rooms.read_events(
        db, room_id="release-room", since_seq=0, limit=20
    )["events"][:2]] == ["Keep this shipped history", "Earlier shipped result"]

def test_history_is_inert_until_new_user_work_and_then_becomes_bounded_context(tmp_path, monkeypatch):
    db = tmp_path / "shared-state.db"
    monkeypatch.setattr(rooms, "local_authority_gateway_id", lambda: "gateway-a")
    service = HostedRoomService(SimpleNamespace(), db_path=db)
    service.local_profiles = lambda: ("default", "reviewer")
    import_group(db)
    binding = service.bindings()[0]

    service.prepare_room(binding)
    assert driver.list_tasks(db, room_id="release-room") == []

    service.send(
        room_id="release-room",
        event_id="user:new-work",
        payload={"text": "@sentinel Continue from the earlier result", "thread_id": "thread-1"},
    )
    queued = driver.list_tasks(db, room_id="release-room", status="queued")
    assert len(queued) == 1
    assert queued[0]["payload"]["target_profile"] == "default"
    assert "Earlier shipped result" in queued[0]["payload"]["prompt"]
    assert "read-only historical context" in queued[0]["payload"]["prompt"].lower()
    assert not any(task["payload"]["target_profile"] == "builder" for task in queued)
