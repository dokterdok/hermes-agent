"""Automatic upgrade of one shipped Group Chat, then one authorized turn.

The path is ``groups.import_history`` on the canonical owner service. It does
not create a second room, and it does not call replica promotion.
"""

from __future__ import annotations

import asyncio
import base64
import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway.session_contract import Principal
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch

PNG = b"\x89PNG\r\n\x1a\n" + b"shipped-image"
ROOM_ID = "release-room"


def _data_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def shipped_group() -> dict:
    """Disposable stand-in for a hermes-bots group-chats record."""
    return {
        "room_id": ROOM_ID,
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


def _open(db, monkeypatch):
    from gateway import hosted_rooms
    from gateway.session_hosted_service import CanonicalHostedRoomService

    monkeypatch.setattr(hosted_rooms, "local_authority_gateway_id", lambda: "gateway-a")
    authority = SimpleNamespace(
        profile_id=str(db.db_path.parent),
        db=db,
        instance_id="group-continuity",
        epoch=begin_runtime_epoch(db, instance_id="group-continuity"),
    )
    service = CanonicalHostedRoomService(authority, None)
    service.local_profiles = lambda: ("default", "reviewer")
    service.runtime.status = lambda: {"running": True, "stopping": False}
    authority.hosted_room_service = service
    actor = Principal(
        "alice",
        authority.profile_id,
        frozenset({"session:read", "session:control", "session:submit"}),
        "owner",
    )
    connection = object.__new__(AuthorityConnection)
    connection.authority = authority
    connection.actor = actor
    connection._group_state_owner = None
    return authority, service, connection


def _events(db_path):
    from gateway import hosted_rooms

    return hosted_rooms.read_events(db_path, room_id=ROOM_ID, since_seq=0, limit=20)["events"]


def _tasks(db_path, status=None):
    from gateway import hosted_room_driver as driver

    return driver.list_tasks(db_path, room_id=ROOM_ID, status=status)


@pytest.mark.asyncio
async def test_shipped_group_upgrades_in_place_and_one_new_turn_survives_restart(tmp_path, monkeypatch):
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db_path = tmp_path / "state.db"
    group = shipped_group()

    with SessionDB(db_path) as db:
        _authority, _service, connection = _open(db, monkeypatch)
        first = await connection.dispatch({"id": 1, "method": "groups.import_history", "params": group})
        lost_ack = await connection.dispatch({"id": 2, "method": "groups.import_history", "params": group})
        assert "result" in first and "result" in lost_ack, (first, lost_ack)
        assert first["result"]["idempotent"] is False
        assert lost_ack["result"]["idempotent"] is True
        assert first["result"]["room"]["room_id"] == ROOM_ID
        assert lost_ack["result"]["room"]["room_id"] == ROOM_ID
        assert [member["display_name"] for member in first["result"]["room"]["members"]] == [
            "Sentinel", "Reviewer", "Remote Builder",
        ]
        remote = first["result"]["room"]["members"][2]
        assert remote["availability"]["state"] == "authorization_required"
        before = _events(db_path)
        assert [(event["kind"], event["payload"].get("text")) for event in before] == [
            ("history.imported", "Keep this shipped history"),
            ("history.imported", "Earlier shipped result"),
            ("history.held", None),
        ]
        assert _tasks(db_path) == []
        manifest = before[0]["payload"]["attachments"][0]
        member_id = first["result"]["room"]["members"][0]["member_id"]
        stored = HostedRoomAttachmentStore(db_path).read(
            room_id=ROOM_ID,
            attachment_id=manifest["attachment_id"],
            recipient_member_id=member_id,
            event_id=before[0]["event_id"],
        )
        assert stored.data == PNG

    with SessionDB(db_path) as db:
        _authority, service, connection = _open(db, monkeypatch)
        restored = _events(db_path)
        assert [(event["kind"], event["payload"].get("text")) for event in restored] == [
            (event["kind"], event["payload"].get("text")) for event in before
        ]
        manifest = restored[0]["payload"]["attachments"][0]
        member_id = service._room(ROOM_ID)["members"][0]["member_id"]
        assert HostedRoomAttachmentStore(db_path).read(
            room_id=ROOM_ID,
            attachment_id=manifest["attachment_id"],
            recipient_member_id=member_id,
            event_id=restored[0]["event_id"],
        ).data == PNG
        assert _tasks(db_path) == []
        send = {
            "room_id": ROOM_ID,
            "event_id": "continue-1",
            "payload": {"text": "@sentinel Continue from the earlier result", "thread_id": "thread-1"},
        }
        admitted = await connection.dispatch({"id": 3, "method": "groups.send", "params": send})
        retried = await connection.dispatch({"id": 4, "method": "groups.send", "params": send})
        assert "result" in admitted and "result" in retried, (admitted, retried)
        assert admitted["result"]["accepted"] is True
        assert retried["result"]["event"]["event_id"] == admitted["result"]["event"]["event_id"]
        assert retried["result"]["event"].get("idempotent") is True
        queued = _tasks(db_path, status="queued")
        assert len(queued) == 1
        assert queued[0]["payload"]["target_profile"] == "default"
        assert "Earlier shipped result" in queued[0]["payload"]["prompt"]
        assert "read-only historical context" in queued[0]["payload"]["prompt"].lower()
        assert not any(task["payload"]["target_profile"] == "builder" for task in queued)
        user_events = [event for event in _events(db_path) if event["kind"] == "message.user"]
        assert len(user_events) == 1
        assert [event["kind"] for event in _events(db_path)[:3]] == [
            "history.imported", "history.imported", "history.held",
        ]

        def revoke(conn):
            updated = conn.execute(
                "UPDATE state_meta SET value=? WHERE key=?",
                ("revoked-grant", f"gateway.hosted.owner.v1:{ROOM_ID}"),
            ).rowcount
            assert updated == 1

        db._execute_write(revoke)
        denied = await connection.dispatch({
            "id": 5,
            "method": "groups.send",
            "params": {
                "room_id": ROOM_ID,
                "event_id": "continue-2",
                "payload": {"text": "@sentinel Do not run this", "thread_id": "thread-1"},
            },
        })
        assert denied["error"]["message"] == "permission_denied"
        assert _tasks(db_path, status="queued") == queued
        assert [event for event in _events(db_path) if event["kind"] == "message.user"] == user_events
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM hosted_rooms").fetchone() == (1,)
            assert conn.execute(
                "SELECT COUNT(*) FROM hosted_room_events WHERE kind='authority.claimed'"
            ).fetchone() == (0,)


def test_revoked_submit_after_preparation_admits_nothing(tmp_path, monkeypatch):
    """Dispatch authorizes before preparation. Revocation during preparation must still block."""
    from gateway.config import GatewayConfig
    from gateway.hosted_room_driver import TaskIdentity
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_hosted_rpc import HostedRoomAuthorityRPC
    from gateway import run, session_policy
    from hermes_state_runtime import list_session_admissions
    import gateway.session_hosted_attachments as attachments

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(run, "_load_gateway_config", lambda: {
        "model": {"default": "fixture"}, "platform_toolsets": {"cli": []}})
    monkeypatch.setattr(run, "_resolve_gateway_model", lambda cfg: "fixture")
    original_restore = session_policy.restore_policy

    def restore(data):
        if data["source"] == "bot_room":
            return replace(original_restore({**data, "source": "gui", "platform": "desktop"}),
                           source="bot_room", platform="bot_room")
        return original_restore(data)

    monkeypatch.setattr(session_policy, "restore_policy", restore)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="continuity-revocation", daemon=True)
    thread.start()
    release = threading.Event()
    entered = threading.Event()
    allowed = {("live-task", 2)}
    checks = []
    scheduled = []
    try:
        authority = asyncio.run_coroutine_threadsafe(
            initialize_session_authority(runner, profile_id="owned", instance_id="first"), loop
        ).result(timeout=10)
        monkeypatch.setattr(authority, "_schedule", scheduled.append)
        principal = Principal(
            "durable-room-owner", "owned",
            frozenset({"session:create", "session:read", "session:submit", "session:control", "session:approve"}),
            "room-worker",
        )

        def authorize(operation, task, generation):
            identity = getattr(task, "task_id", None)
            checks.append((operation, identity, generation))
            if operation != "submit":
                return True
            return (identity, generation) in allowed

        rpc = HostedRoomAuthorityRPC(
            authority, loop, room_id="room", member_id="member", profile="default",
            principal=principal, authorize=authorize)
        coords = dict(profile="default", source="bot_room")
        sid = rpc.create(**coords, title="Group: room")["session_id"]
        coords["session_id"] = sid
        original = attachments.submission_payload
        calls = {"n": 0}

        def blocked(rpc_self, prompt, attachments_arg=None):
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                assert release.wait(5), "preparation was not released"
            return original(rpc_self, prompt, attachments_arg)

        monkeypatch.setattr(attachments, "submission_payload", blocked)
        denied = TaskIdentity("room", "revoked-task", "thread", "turn")
        allowed.add((denied.task_id, 1))
        holder = {}

        def submit_denied():
            try:
                holder["result"] = rpc.submit(
                    **coords, prompt="revoke me", task=denied, execution_generation=1,
                    on_terminal=lambda receipt: None)
            except Exception as exc:
                holder["error"] = exc

        worker = threading.Thread(target=submit_denied, name="revoked-submit")
        worker.start()
        assert entered.wait(5), "preparation did not start"
        allowed.discard((denied.task_id, 1))
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        error = holder.get("error")
        assert isinstance(error, RuntimeStoreError) and error.reason == "permission_denied"
        assert "result" not in holder
        assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
        assert scheduled == []

        live = TaskIdentity("room", "live-task", "thread", "turn")
        before = len(checks)
        receipt = rpc.submit(
            **coords, prompt="keep me", task=live, execution_generation=2,
            on_terminal=lambda receipt: None)
        positive = [item for item in checks[before:] if item[0] == "submit" and item[1] == live.task_id]
        assert positive == [("submit", live.task_id, 2), ("submit", live.task_id, 2)]
        rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        assert len(rows) == 1
        assert rows[0]["admission_id"] == receipt["admission_id"]
        assert rows[0]["payload"]["text"] == "keep me"
        assert scheduled == [rpc.ref]
    finally:
        release.set()
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            asyncio.run_coroutine_threadsafe(
                asyncio.gather(*pending, return_exceptions=True), loop
            ).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        store._db.close()
