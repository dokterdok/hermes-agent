"""Owner-scoped RPC contract for inert shipped Group Chat history import."""

from __future__ import annotations

from dataclasses import replace
import sqlite3
from types import SimpleNamespace

import pytest

from gateway.session_contract import Principal
from gateway.session_controls import AuthorityConnection
from gateway.session_group_controls import GROUP_METHODS, dispatch_group_control
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError


def params() -> dict:
    return {
        "room_id": "release-room",
        "name": "Release",
        "source_id": "hermes.plugin.hermes-bots.group-chats:release-room",
        "members": [
            {
                "source_member_id": "sentinel",
                "name": "Sentinel",
                "profile": "default",
                "handle": "sentinel",
                "connection_id": "local",
                "remote_source": False,
            },
            {
                "source_member_id": "reviewer",
                "name": "Reviewer",
                "profile": "reviewer",
                "handle": "reviewer",
                "connection_id": "local",
                "remote_source": False,
            },
            {
                "source_member_id": "remote",
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
                "source_entry_id": "history-1",
                "at_ms": 1,
                "author_kind": "user",
                "author_name": "You",
                "text": "old context",
                "thread_id": "thread",
            }
        ],
        "held_work": [
            {
                "source_work_id": "old-unknown",
                "at_ms": 2,
                "member_source_id": "remote",
                "state": "uncertain",
                "description": "No completion evidence",
            }
        ],
    }


class ImportService:
    def __init__(self, db_path):
        self.db_path = db_path
        self.runtime = SimpleNamespace(status=lambda: {"running": False, "stopping": False})
        self.calls: list[tuple[str, dict]] = []

    def authorize_room(self, *_args, **_kwargs):
        raise AssertionError("import authorization belongs inside the writer transaction")

    def import_shipped_group_history(self, *, actor_subject, **request):
        self.calls.append((actor_subject, request))
        return {
            "room": {
                "room_id": request["room_id"],
                "name": request["name"],
                "members": [],
                "authority_gateway_id": "gateway-a",
                "authority_epoch": 1,
                "revision": 1,
                "created_at": 1.0,
                "updated_at": 1.0,
                "idempotent": False,
            },
            "source_id": request["source_id"],
            "imported_history": len(request["history"]),
            "held_work": len(request["held_work"]),
            "held_members": 1,
            "retired_members": 0,
            "idempotent": False,
        }


@pytest.mark.asyncio
async def test_registered_import_uses_session_control_and_server_owned_principal(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        service = ImportService(db.db_path)
        authority = SimpleNamespace(profile_id=str(tmp_path), db=db, hosted_room_service=service)
        actor = Principal("alice", str(tmp_path), frozenset({"session:read", "session:control"}), "owner")
        connection = SimpleNamespace(authority=authority, actor=actor, _group_state_owner=None)

        assert GROUP_METHODS["groups.import_history"] == "session:control"
        capabilities = await dispatch_group_control(connection, "groups.capabilities", {})
        assert isinstance(capabilities, dict)
        methods = capabilities.get("methods")
        assert isinstance(methods, list) and "groups.import_history" in methods
        result = await dispatch_group_control(connection, "groups.import_history", params())

        assert result["imported_history"] == 1
        assert result["held_members"] == 1
        assert service.calls == [("alice", params())]
        assert service.runtime.status()["running"] is False


@pytest.mark.asyncio
async def test_import_denies_missing_capability_foreign_profile_and_unknown_fields(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        service = ImportService(db.db_path)
        authority = SimpleNamespace(profile_id=str(tmp_path), db=db, hosted_room_service=service)
        actor = Principal("alice", str(tmp_path), frozenset({"session:read", "session:control"}), "owner")

        for denied in (
            replace(actor, capabilities=frozenset()),
            replace(actor, profile_id="foreign"),
        ):
            with pytest.raises(RuntimeStoreError):
                await dispatch_group_control(
                    SimpleNamespace(authority=authority, actor=denied), "groups.import_history", params()
                )
        with pytest.raises(RuntimeStoreError, match="invalid_params"):
            await dispatch_group_control(
                SimpleNamespace(authority=authority, actor=actor),
                "groups.import_history",
                {**params(), "actor_subject": "forged"},
            )
        assert service.calls == []


@pytest.mark.asyncio
async def test_canonical_service_binds_owner_inside_the_import_transaction(tmp_path, monkeypatch):
    from gateway import hosted_rooms
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from hermes_state_runtime import begin_runtime_epoch

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hosted_rooms, "local_authority_gateway_id", lambda: "gateway-a")
    with SessionDB(tmp_path / "state.db") as db:
        authority = SimpleNamespace(
            profile_id=str(tmp_path), db=db, instance_id="group-import-test",
            epoch=begin_runtime_epoch(db, instance_id="group-import-test"))
        service = CanonicalHostedRoomService(authority, None)
        service.local_profiles = lambda: ("default", "reviewer")
        authority.hosted_room_service = service
        actor = Principal("alice", str(tmp_path), frozenset({"session:read", "session:control"}), "owner")
        connection = object.__new__(AuthorityConnection)
        connection.authority = authority
        connection.actor = actor
        connection._group_state_owner = None

        first_reply = await connection.dispatch({"id": 1, "method": "groups.import_history", "params": params()})
        repeated_reply = await connection.dispatch({"id": 2, "method": "groups.import_history", "params": params()})
        assert 'result' in first_reply and 'result' in repeated_reply, (first_reply, repeated_reply)
        first, repeated = first_reply["result"], repeated_reply["result"]
        connection.actor = replace(actor, subject="bob")
        denied = await connection.dispatch({"id": 3, "method": "groups.import_history", "params": params()})

        assert isinstance(first, dict) and isinstance(repeated, dict)
        assert first["idempotent"] is False
        assert repeated["idempotent"] is True
        assert denied["error"]["message"] == "permission_denied"
        assert hosted_rooms.list_rooms(db.db_path)[0]["room_id"] == "release-room"
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute(
                "SELECT value FROM state_meta WHERE key=?", ("gateway.hosted.owner.v1:release-room",)
            ).fetchone()[0] == "alice"


@pytest.mark.asyncio
async def test_owner_member_resolution_refresh_retire_and_reactivate_preserves_identity(tmp_path, monkeypatch):
    from gateway import hosted_rooms
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from hermes_state_runtime import begin_runtime_epoch

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hosted_rooms, "local_authority_gateway_id", lambda: "gateway-a")
    with SessionDB(tmp_path / "state.db") as db:
        authority = SimpleNamespace(
            profile_id=str(tmp_path), db=db, instance_id="group-resolution-test", events={},
            epoch=begin_runtime_epoch(db, instance_id="group-resolution-test"))
        service = CanonicalHostedRoomService(authority, None)
        profiles = ["default"]
        service.local_profiles = lambda: tuple(profiles)
        authority.hosted_room_service = service
        connection = object.__new__(AuthorityConnection)
        connection.authority = authority
        connection.actor = Principal(
            "alice", str(tmp_path), frozenset({"session:read", "session:control"}), "owner")
        connection._group_state_owner = None

        request = params()
        request["members"] = request["members"][:2]
        request["held_work"] = []
        imported = (await connection.dispatch(
            {"id": 1, "method": "groups.import_history", "params": request}))["result"]
        member_id = imported["room"]["members"][1]["member_id"]
        assert imported["room"]["members"][1]["availability"]["state"] == "authorization_required"

        profiles.append("reviewer")
        ready = (await connection.dispatch({
            "id": 2, "method": "groups.member.resolve",
            "params": {"room_id": "release-room", "member_id": member_id, "action": "refresh"},
        }))["result"]
        assert ready["member"]["member_id"] == member_id
        assert ready["member"]["availability"] == {"state": "ready"}

        retired = (await connection.dispatch({
            "id": 3, "method": "groups.member.resolve",
            "params": {"room_id": "release-room", "member_id": member_id, "action": "retire"},
        }))["result"]
        assert retired["member"]["member_id"] == member_id
        assert retired["member"]["membership"] == {"state": "former"}
        assert retired["member"]["availability"]["state"] == "retired"

        profiles.remove("reviewer")
        held = (await connection.dispatch({
            "id": 4, "method": "groups.member.resolve",
            "params": {"room_id": "release-room", "member_id": member_id, "action": "activate"},
        }))["result"]
        assert held["member"]["member_id"] == member_id
        assert held["member"]["membership"] == {"state": "active"}
        assert held["member"]["availability"] == {
            "state": "authorization_required", "reason": "local_profile_unavailable"}

        events = hosted_rooms.read_events(db.db_path, room_id="release-room", limit=20)["events"]
        assert [event["kind"] for event in events] == ["history.imported"]
