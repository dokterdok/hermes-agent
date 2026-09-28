"""A withdrawn work-record bearer must not start replica writer maintenance (F1)."""

from contextlib import contextmanager
import sqlite3
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_room_replicas as replicas
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms
from gateway.config import PlatformConfig
from gateway.hosted_room_peer import HostedRoomGrantError, decode_room_grant, issue_room_grant
from gateway import hosted_room_replica_ingress as ingress
from gateway.platforms import api_server_room_grants as grants
from gateway.platforms.api_server import APIServerAdapter

HOME = "install:home"
TARGET = "install:target"
SECRET = b"disposable-work-ingress-secret" * 2
MEMBERS = [{"member_id": "reviewer", "profile": "default", "handle": "reviewer",
            "target": {"kind": "peer", "peer_id": "peer-reviewer", "installation_id": TARGET,
                       "profile": "default", "capability_digest": "a" * 64}}]


def _record():
    content = {
        "version": 1, "room_id": "room", "home_install_id": HOME,
        "authority": {"gateway_id": HOME, "epoch": 1},
        "roster_sha256": work.roster_digest(MEMBERS),
        "history": {"seq": 0, "event_sha256": work.digest([])},
        "availability": "unavailable", "reason": "task_store_missing",
        "tasks": [], "receipts": [], "limitations": work.LIMITATIONS,
        "stop": {"closing": False, "revocation_complete": False, "seq": 0, "cancel_id": None},
    }
    return {**content, "revision": 1, "digest": work.digest(content)}


@pytest.mark.asyncio
@pytest.mark.parametrize("withdraw", [False, True], ids=["valid-control", "withdrawn-after-body"])
async def test_current_grant_is_checked_before_replica_writer_schema_and_audit(tmp_path, monkeypatch, withdraw):
    # All databases and grant state are disposable; no gateway or model is started.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    rooms.create_room(source, room_id="room", name="Workshop", members=MEMBERS, authority_gateway_id=HOME)
    replicas.ingest_page(target, room_id="room", room_name="Workshop", members=MEMBERS,
                         page=rooms.read_events(source, room_id="room"))
    token = issue_room_grant(
        SECRET, grant_id="work-admission", room_id="room", home_install_id=HOME,
        authority_gateway_id=HOME, authority_epoch=1, member_id="reviewer",
        target_install_id=TARGET, target_profile="default",
        execution_policy_digest="b" * 64, permissions=("status", "replicate", "work_records"),
        issued_at=time.time(), ttl_seconds=300,
    )
    claims = decode_room_grant(SECRET, token, permission="work_records")
    rooms.reserve_peer_room(target, claims=claims, expires_at=float(claims["status_expires_at"]))
    monkeypatch.setattr(rooms, "default_db_path", lambda: target)
    monkeypatch.setattr(rooms, "local_authority_gateway_id", lambda: TARGET)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "disposable-key"}))
    monkeypatch.setattr(adapter, "_room_grant_secret", lambda: SECRET)

    events = []
    original_claims = adapter._room_grant_claims
    original_authorize = ingress.authorize_granted_room

    def observe_http_claims(request, *, permission):
        result = original_claims(request, permission=permission)
        events.append("http-grant-current")
        return result

    def observe_authorize(**kwargs):
        callback = original_authorize(**kwargs)

        def observed_callback(conn):
            events.append("current-grant-callback")
            return callback(conn)

        return observed_callback

    monkeypatch.setattr(adapter, "_room_grant_claims", observe_http_claims)
    monkeypatch.setattr(ingress, "authorize_granted_room", observe_authorize)
    original_read = adapter._read_json_body

    async def read_then_withdraw(request):
        body, error = await original_read(request)
        events.append("body-read")
        if withdraw:
            # First HTTP current-grant check already succeeded. Commit the exact
            # bearer revocation before returning the parsed body to the route.
            rooms.revoke_room_grant_id(target, claims=claims,
                                       expires_at=float(claims["status_expires_at"]))
            assert rooms.room_grant_is_revoked(target, claims=claims)
            events.append("withdrawn")
        return body, error

    monkeypatch.setattr(adapter, "_read_json_body", read_then_withdraw)
    original_transaction = replicas._transaction
    original_schema = replicas._initialize_replica_schema
    original_audit = replicas._audit_existing_replicas_locked

    @contextmanager
    def observe_writer(*args, **kwargs):
        with original_transaction(*args, **kwargs) as conn:
            events.append("writer-entered")
            yield conn

    def observe_schema(conn):
        events.append("replica-schema")
        return original_schema(conn)

    def observe_audit(conn):
        events.append("replica-audit")
        return original_audit(conn)

    monkeypatch.setattr(replicas, "_transaction", observe_writer)
    monkeypatch.setattr(replicas, "_initialize_replica_schema", observe_schema)
    monkeypatch.setattr(replicas, "_audit_existing_replicas_locked", observe_audit)
    app = web.Application()
    for method, path, handler in grants._http_routes(adapter):
        app.router.add_route(method, path, handler)
    async with TestClient(TestServer(app)) as http:
        response = await http.post("/v1/room-members/work-records", json={"record": _record()},
                                   headers={"Authorization": f"HermesRoom {token}"})
        payload = await response.json()

    with rooms._connect(target) as conn:
        rows = conn.execute("SELECT count(*) FROM hosted_room_work_records_target").fetchone()[0] if (
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hosted_room_work_records_target'").fetchone()
        ) else 0
    if withdraw:
        assert response.status in (401, 403), (response.status, payload, events)
        assert rows == 0
        assert events[:3] == ["http-grant-current", "body-read", "withdrawn"]
        # No rollback-only proof: neither writer lock nor schema/audit work is
        # allowed to start on a bearer revoked after the HTTP precheck.
        assert not ({"writer-entered", "replica-schema", "replica-audit"} & set(events)), events
    else:
        assert response.status == 200, (response.status, payload, events)
        assert payload["passive"] is True
        assert rows == 1
        # The positive control requires the real admission path, not today's
        # defective callback ordering. A correction may recheck before entry
        # and again under the writer without breaking this control.
        assert events[:2] == ["http-grant-current", "body-read"]
        assert {"writer-entered", "replica-schema", "replica-audit",
                "current-grant-callback"}.issubset(events)


def _prepared_target(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    rooms.create_room(source, room_id="room", name="Workshop", members=MEMBERS, authority_gateway_id=HOME)
    replicas.ingest_page(target, room_id="room", room_name="Workshop", members=MEMBERS,
                         page=rooms.read_events(source, room_id="room"))
    token = issue_room_grant(
        SECRET, grant_id="work-direct", room_id="room", home_install_id=HOME,
        authority_gateway_id=HOME, authority_epoch=1, member_id="reviewer",
        target_install_id=TARGET, target_profile="default",
        execution_policy_digest="b" * 64, permissions=("status", "replicate", "work_records"),
        issued_at=time.time(), ttl_seconds=300,
    )
    claims = decode_room_grant(SECRET, token, permission="work_records")
    rooms.reserve_peer_room(target, claims=claims, expires_at=float(claims["status_expires_at"]))
    return target, token, claims


def _direct_ingest(target, token):
    return work.ingest(target, record=_record(), token=token, secret=SECRET,
                       target_install_id=TARGET, target_profile="default")


def test_direct_ingress_refuses_withdrawal_without_writer_maintenance(tmp_path, monkeypatch):
    target, token, claims = _prepared_target(tmp_path)
    rooms.revoke_room_grant_id(target, claims=claims, expires_at=float(claims["status_expires_at"]))
    events = []
    original = replicas._replica_transaction

    @contextmanager
    def observe_writer(*args, **kwargs):
        events.append("writer")
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(replicas, "_replica_transaction", observe_writer)
    with pytest.raises(HostedRoomGrantError, match="revoked|no longer current"):
        _direct_ingest(target, token)
    assert events == []
    with sqlite3.connect(target) as conn:
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_work_records_target'").fetchone()
        assert table is None or conn.execute("SELECT count(*) FROM hosted_room_work_records_target").fetchone()[0] == 0


def test_withdrawal_between_preflight_and_writer_refuses_before_audit(tmp_path, monkeypatch):
    target, token, claims = _prepared_target(tmp_path)
    events = []
    original = replicas._replica_transaction
    original_authorize = ingress.authorize_granted_room

    def observe_authorize(**kwargs):
        callback = original_authorize(**kwargs)

        def observed(conn):
            events.append("grant-check")
            return callback(conn)

        return observed

    @contextmanager
    def withdraw_before_writer(*args, **kwargs):
        assert events == ["grant-check"]  # Real preflight, before writer entry.
        rooms.revoke_room_grant_id(target, claims=claims, expires_at=float(claims["status_expires_at"]))
        events.append("withdrawn")
        with original(*args, **kwargs) as conn:
            yield conn

    def observe_schema(conn):
        events.append("schema")
        return original_schema(conn)

    original_schema = replicas._initialize_replica_schema
    monkeypatch.setattr(ingress, "authorize_granted_room", observe_authorize)
    monkeypatch.setattr(replicas, "_replica_transaction", withdraw_before_writer)
    monkeypatch.setattr(replicas, "_initialize_replica_schema", observe_schema)
    with pytest.raises(HostedRoomGrantError, match="revoked|no longer current"):
        _direct_ingest(target, token)
    assert events == ["grant-check", "withdrawn", "grant-check"]


@pytest.mark.parametrize("stale", [False, True], ids=["missing-database", "stale-schema"])
def test_direct_ingress_does_not_initialize_missing_grant_state(tmp_path, stale):
    target = tmp_path / "target.db"
    if stale:
        with sqlite3.connect(target) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
    token = issue_room_grant(
        SECRET, grant_id="work-unavailable", room_id="room", home_install_id=HOME,
        authority_gateway_id=HOME, authority_epoch=1, member_id="reviewer",
        target_install_id=TARGET, target_profile="default",
        execution_policy_digest="b" * 64, permissions=("work_records",),
        issued_at=time.time(), ttl_seconds=300,
    )
    with pytest.raises(HostedRoomGrantError, match="grant state is unavailable"):
        _direct_ingest(target, token)
    if stale:
        with sqlite3.connect(target) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("unrelated",)]
    else:
        assert not target.exists()


@pytest.mark.parametrize("stale", [False, True], ids=["current-root", "grant-readable-stale-root"])
def test_direct_ingress_requires_current_root_without_migration(tmp_path, monkeypatch, stale):
    target, token, _ = _prepared_target(tmp_path)
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        if stale:
            conn.execute("DROP INDEX idx_hosted_room_events_cursor")
        assert rooms._schema_is_current(conn) is not stale
        # The real grant remains valid and readable even though root readiness
        # can fail. Missing grant tables would mask this migration boundary.
        ingress.authorize_granted_room(
            token=token, secret=SECRET, target_install_id=TARGET, target_profile="default",
            room_id="room", members=MEMBERS, authority={"gateway_id": HOME, "epoch": 1},
            permission="work_records",
        )(conn)
        before = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()

    events = []
    original = replicas._replica_transaction

    @contextmanager
    def observe_writer(*args, **kwargs):
        events.append("writer")
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(replicas, "_replica_transaction", observe_writer)
    error, result = None, None
    try:
        result = _direct_ingest(target, token)
    except HostedRoomGrantError as exc:
        error = exc
    if stale:
        # Inspect with plain SQLite: the normal room connector would itself
        # repair the missing index and conceal the forbidden migration.
        with sqlite3.connect(target) as conn:
            conn.row_factory = sqlite3.Row
            repaired = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='idx_hosted_room_events_cursor'"
            ).fetchone()
            assert repaired is None, "ingress repaired stale root schema"
            assert conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == before
        assert events == []
        assert error is not None and "grant state is unavailable" in str(error)
    else:
        assert error is None
        assert result is not None
        assert result["passive"] is True
        assert events == ["writer"]
