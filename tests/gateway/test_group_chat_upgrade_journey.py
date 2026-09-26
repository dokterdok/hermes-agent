"""Automatic upgrade of one shipped Group Chat through the canonical session path.

Disposable data stands in for a released desktop group. The importer is the
upgrade. History is not executed. A lost acknowledgement retries the same
import. A new service on the same database is the restart. One new authorized
turn is admitted after that. Revoked membership blocks the next admission,
including the post-preparation writer recheck and the running-task dequeue
recheck.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from gateway import hosted_room_driver as driver
from gateway import hosted_rooms as rooms
from gateway.hosted_room_attachments import HostedRoomAttachmentStore
from gateway.session_contract import Principal
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch
from tui_gateway.hosted_room_driver import HostedRoomBinding

PNG = b"\x89PNG\r\n\x1a\n" + b"shipped-image"
ROOM_ID = "release-room"
SOURCE_ID = "hermes.plugin.hermes-bots.group-chats:release-room"


def _data_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def shipped_group() -> dict:
    return {
        "room_id": ROOM_ID,
        "name": "Release",
        "source_id": SOURCE_ID,
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
                "attachments": [
                    {"kind": "image", "name": "history.png", "data": _data_url(PNG)},
                ],
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


def _open(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(rooms, "local_authority_gateway_id", lambda: "gateway-a")
    db = SessionDB(tmp_path / "state.db")
    authority = SimpleNamespace(
        profile_id=str(tmp_path), db=db, instance_id="group-upgrade-journey",
        epoch=begin_runtime_epoch(db, instance_id="group-upgrade-journey"))
    from gateway.session_hosted_service import CanonicalHostedRoomService

    service = CanonicalHostedRoomService(authority, None)
    service.local_profiles = lambda: ("default", "reviewer")
    authority.hosted_room_service = service
    actor = Principal(
        "alice", str(tmp_path),
        frozenset({"session:read", "session:control", "session:submit"}), "owner")
    connection = object.__new__(AuthorityConnection)
    connection.authority = authority
    connection.actor = actor
    connection._group_state_owner = None
    return db, authority, service, connection


def _events(db_path):
    return rooms.read_events(db_path, room_id=ROOM_ID, since_seq=0, limit=20)["events"]


def _member(room, handle):
    return next(member for member in room["members"] if member["handle"] == handle)


def _attachment_bytes(db_path, member_id, event_id, attachment_id):
    store = HostedRoomAttachmentStore(db_path, clock=lambda: 1_800_000_000)
    return store.read(
        room_id=ROOM_ID, attachment_id=attachment_id,
        recipient_member_id=member_id, event_id=event_id).data


@pytest.mark.asyncio
async def test_shipped_group_upgrades_in_place_without_executing_history(tmp_path, monkeypatch):
    db, authority, service, connection = _open(tmp_path, monkeypatch)
    try:
        ignored = await connection.dispatch(
            {"id": 1, "method": "groups.import_history", "params": shipped_group()})
        assert ignored["result"]["idempotent"] is False
        # The acknowledgement is gone. The retry must not create a second room.
        del ignored
        retried = await connection.dispatch(
            {"id": 2, "method": "groups.import_history", "params": shipped_group()})
        assert retried["result"]["idempotent"] is True
        room = retried["result"]["room"]
        assert room["room_id"] == ROOM_ID
        assert [member["display_name"] for member in room["members"]] == [
            "Sentinel", "Reviewer", "Remote Builder"]
        assert _member(room, "builder")["availability"] == {
            "state": "authorization_required",
            "reason": "remote_execution_not_authorized",
        }
        events = _events(db.db_path)
        assert [event["kind"] for event in events] == [
            "history.imported", "history.imported", "history.held"]
        assert events[0]["payload"]["text"] == "Keep this shipped history"
        assert events[1]["payload"]["text"] == "Earlier shipped result"
        manifest = events[0]["payload"]["attachments"][0]
        assert manifest["name"] == "history.png"
        with sqlite3.connect(db.db_path) as conn:
            stored_sha = conn.execute(
                "SELECT sha256 FROM hosted_room_attachments WHERE event_id=?",
                (events[0]["event_id"],)).fetchone()[0]
        assert stored_sha == hashlib.sha256(PNG).hexdigest()
        sentinel_id = _member(room, "sentinel")["member_id"]
        reviewer_id = _member(room, "reviewer")["member_id"]
        assert _attachment_bytes(
            db.db_path, sentinel_id, events[0]["event_id"], manifest["attachment_id"]) == PNG
        assert driver.list_tasks(db.db_path, room_id=ROOM_ID) == []
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM hosted_rooms").fetchone() == (1,)
            assert conn.execute("SELECT COUNT(*) FROM hosted_room_events").fetchone() == (3,)
            assert conn.execute("SELECT COUNT(*) FROM hosted_room_history_imports").fetchone() == (1,)

        db.close()
        reopened, _authority, restarted, restarted_connection = _open(tmp_path, monkeypatch)
        try:
            state = await restarted_connection.dispatch(
                {"id": 3, "method": "groups.state", "params": {"room_id": ROOM_ID}})
            assert state["result"]["room"]["room_id"] == ROOM_ID
            restored = _events(reopened.db_path)
            assert [event["kind"] for event in restored] == [
                "history.imported", "history.imported", "history.held"]
            assert _attachment_bytes(
                reopened.db_path, sentinel_id, restored[0]["event_id"],
                restored[0]["payload"]["attachments"][0]["attachment_id"]) == PNG
            assert driver.list_tasks(reopened.db_path, room_id=ROOM_ID) == []

            restarted.runtime.status = lambda: {"running": True, "stopping": False}
            accepted = await restarted_connection.dispatch({
                "id": 4, "method": "groups.send",
                "params": {
                    "room_id": ROOM_ID,
                    "event_id": "new-work",
                    "payload": {
                        "text": "@sentinel Continue from the earlier result",
                        "thread_id": "thread-1",
                    },
                },
            })
            assert accepted["result"]["accepted"] is True
            queued = driver.list_tasks(reopened.db_path, room_id=ROOM_ID, status="queued")
            assert len(queued) == 1
            queued_identity = queued[0]["identity"]
            assert queued[0]["payload"]["target_profile"] == "default"
            assert queued[0]["payload"].get("target_member_id") == sentinel_id
            assert "Earlier shipped result" in queued[0]["payload"]["prompt"]
            assert "read-only historical context" in queued[0]["payload"]["prompt"].lower()
            assert not any(
                task["payload"].get("target_profile") == "builder"
                for task in driver.list_tasks(reopened.db_path, room_id=ROOM_ID))
            after_send = _events(reopened.db_path)
            assert [event["kind"] for event in after_send[:3]] == [
                "history.imported", "history.imported", "history.held"]
            assert after_send[-1]["kind"] == "message.user"

            retired = await restarted_connection.dispatch({
                "id": 5, "method": "groups.member.resolve",
                "params": {"room_id": ROOM_ID, "member_id": reviewer_id, "action": "retire"},
            })
            assert retired["result"]["member"]["membership"] == {"state": "former"}
            with pytest.raises(RuntimeStoreError):
                await restarted_connection.dispatch({
                    "id": 6, "method": "groups.send",
                    "params": {
                        "room_id": ROOM_ID,
                        "event_id": "revoked-work",
                        "payload": {"text": "@reviewer Work again", "thread_id": "thread-2"},
                    },
                })
            assert [
                task["identity"] for task in driver.list_tasks(
                    reopened.db_path, room_id=ROOM_ID, status="queued")
            ] == [queued_identity]
            with pytest.raises(driver.RoomUnavailableError, match="imported member is retiring or former"):
                driver.admit_task(
                    reopened.db_path,
                    driver.TaskIdentity(ROOM_ID, "stale-reviewer", "thread-2", "turn-stale"),
                    payload={
                        "target_profile": "reviewer",
                        "target_member_id": reviewer_id,
                        "prompt": "prepared before revocation",
                        "source_event_seq": 1,
                    },
                    clock=time.time)

            gateway = rooms.local_authority_gateway_id()
            lease = driver.acquire_lease(
                reopened.db_path, room_id=ROOM_ID, gateway_id=gateway, authority_epoch=1,
                process_generation="journey", ttl_seconds=300, clock=time.time)
            running = driver.start_task(
                reopened.db_path, queued_identity, lease,
                expected_cancel_generation=0, clock=time.time)
            task = next(
                item for item in driver.list_tasks(reopened.db_path, room_id=ROOM_ID)
                if item["identity"] == queued_identity)
            row = {
                "principal_id": "alice",
                "request_id": "hosted:" + json.dumps(
                    [asdict(task["identity"]), task["execution_generation"]]),
                "payload": {"text": task["payload"]["prompt"]},
            }
            binding = HostedRoomBinding(ROOM_ID, gateway, 1)
            assert restarted.check_admission(
                restarted._resolve_member_transport(binding, task).ref, row) == task
            with reopened._lock:
                members = json.loads(reopened._conn.execute(
                    "SELECT members_json FROM hosted_rooms WHERE room_id=?", (ROOM_ID,)).fetchone()[0])
                kept = [member for member in members if member["member_id"] != sentinel_id]
                reopened._conn.execute(
                    "UPDATE hosted_rooms SET members_json=? WHERE room_id=?",
                    (json.dumps(kept), ROOM_ID))
                reopened._conn.commit()
            with pytest.raises(RuntimeStoreError, match="permission_denied"):
                restarted.check_admission(
                    restarted._resolve_member_transport(binding, task).ref, row)
            assert running.execution_generation == task["execution_generation"]
        finally:
            reopened.close()
    finally:
        if getattr(db, "_conn", None) is not None:
            db.close()
