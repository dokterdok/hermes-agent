"""Revoked or denied work-record requests stay out of writer, schema, and audit."""
import sqlite3
from contextlib import closing

import pytest

from gateway import hosted_rooms as rooms


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def create(path):
    rooms.create_room(path, room_id="room", name="Evidence", members=[{"profile": "ops", "handle": "ops"}],
                      authority_gateway_id="owner-a", now=10)


def _phases(monkeypatch):
    from gateway import hosted_room_work_storage as storage
    phases = []

    def initialize(conn):
        phases.append("schema")
        return real_initialize(conn)

    def validate_stored_locked(conn, table, row):
        phases.append("audit")
        return real_validate(conn, table, row)

    def save_locked(conn, table, record, **kwargs):
        phases.append("writer")
        return real_save(conn, table, record, **kwargs)

    real_initialize = storage.initialize
    real_validate = storage.validate_stored_locked
    real_save = storage.save_locked
    monkeypatch.setattr(storage, "initialize", initialize)
    monkeypatch.setattr(storage, "validate_stored_locked", validate_stored_locked)
    monkeypatch.setattr(storage, "save_locked", save_locked)
    return phases


def test_denied_capture_does_not_enter_writer_schema_or_audit(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    phases = _phases(monkeypatch)
    opened = []
    real_transaction = rooms._transaction

    def transaction(db_path, immediate=False):
        opened.append(bool(immediate))
        return real_transaction(db_path, immediate=immediate)

    monkeypatch.setattr(rooms, "_transaction", transaction)
    with pytest.raises(work.WorkRecordError, match="unavailable"):
        work.capture(path, room_id="room", local_gateway_id="intruder")
    assert phases == []
    assert True not in opened
    with closing(connect(path)) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (work.SOURCE_TABLE,)).fetchone() is None


def test_revoked_authority_is_rechecked_before_pending_ack(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    record = work.capture(path, room_id="room", local_gateway_id="owner-a")
    with closing(connect(path)) as conn, conn:
        pending = work.prepare_delivery_locked(conn, room_id="room", local_gateway_id="owner-a",
            target_install_id="peer", route_generation="route-1", through_seq=100)
        conn.execute("DROP TRIGGER IF EXISTS trg_work_authority_transition_v2")
        conn.execute(
            "UPDATE hosted_rooms SET authority_gateway_id='owner-b', authority_epoch=2 WHERE room_id='room'")
    phases = _phases(monkeypatch)
    with closing(connect(path)) as conn, conn:
        assert work.acknowledge_locked(conn, room_id="room", target_install_id="peer",
            route_generation="route-1", record=pending, ack=work.acknowledgement(pending)) is False
        row = conn.execute(
            f"SELECT status, disposition FROM {work.PENDING_TABLE} WHERE room_id='room'").fetchone()
    assert phases == []
    assert row["status"] == "pending"
    assert row["disposition"] == "current"
    assert record["authority"] == {"gateway_id": "owner-a", "epoch": 1}


def test_denied_prepare_does_not_enter_writer_schema_or_audit(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    phases = _phases(monkeypatch)
    with closing(connect(path)) as conn, conn:
        with pytest.raises(work.WorkRecordError, match="unavailable"):
            work.prepare_delivery_locked(conn, room_id="room", local_gateway_id="intruder",
                target_install_id="peer", route_generation="route-1", through_seq=100)
    assert phases == []
    with closing(connect(path)) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (work.SOURCE_TABLE,)).fetchone() is None


def test_revocation_after_deferred_recheck_stops_before_schema(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    phases = _phases(monkeypatch)
    opened = []
    real_transaction = rooms._transaction

    def transaction(db_path, immediate=False):
        opened.append(bool(immediate))
        if immediate:
            with closing(connect(path)) as conn, conn:
                conn.execute(
                    "UPDATE hosted_rooms SET authority_gateway_id='owner-b', authority_epoch=2 "
                    "WHERE room_id='room'")
        return real_transaction(db_path, immediate=immediate)

    monkeypatch.setattr(rooms, "_transaction", transaction)
    with pytest.raises(work.WorkRecordError, match="unavailable"):
        work.capture(path, room_id="room", local_gateway_id="owner-a")
    assert phases == []
    assert opened == [False, True]
    with closing(connect(path)) as conn:
        assert conn.execute(
            "SELECT authority_gateway_id FROM hosted_rooms WHERE room_id='room'").fetchone()[0] == "owner-b"
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (work.SOURCE_TABLE,)).fetchone() is None


def test_revocation_between_delivery_checks_stops_before_schema(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    with closing(connect(path)) as conn, conn:
        work.prepare_delivery_locked(conn, room_id="room", local_gateway_id="owner-a",
            target_install_id="peer", route_generation="route-1", through_seq=100)
    phases = _phases(monkeypatch)
    real = work._require_delivery_source
    seen = {"n": 0}

    def require(conn, room_id, local_gateway_id):
        seen["n"] += 1
        if seen["n"] == 1:
            current = real(conn, room_id, local_gateway_id)
            with closing(connect(path)) as other, other:
                other.execute(
                    "UPDATE hosted_rooms SET authority_gateway_id='owner-b', authority_epoch=2 "
                    "WHERE room_id='room'")
            return current
        return real(conn, room_id, local_gateway_id)

    monkeypatch.setattr(work, "_require_delivery_source", require)
    with closing(connect(path)) as conn, conn:
        with pytest.raises(work.WorkRecordError, match="unavailable"):
            work.prepare_delivery_locked(conn, room_id="room", local_gateway_id="owner-a",
                target_install_id="peer", route_generation="route-2", through_seq=100)
    assert seen["n"] >= 2
    assert phases == []


def test_revocation_between_ack_checks_stops_before_schema(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    with closing(connect(path)) as conn, conn:
        pending = work.prepare_delivery_locked(conn, room_id="room", local_gateway_id="owner-a",
            target_install_id="peer", route_generation="route-1", through_seq=100)
    phases = _phases(monkeypatch)
    real = work._record_authority_current
    seen = {"n": 0}

    def current(conn, room_id, record):
        seen["n"] += 1
        allowed = real(conn, room_id, record)
        if seen["n"] == 1:
            with closing(connect(path)) as other, other:
                other.execute("DROP TRIGGER IF EXISTS trg_work_authority_transition_v2")
                other.execute(
                    "UPDATE hosted_rooms SET authority_gateway_id='owner-b', authority_epoch=2 "
                    "WHERE room_id='room'")
        return allowed

    monkeypatch.setattr(work, "_record_authority_current", current)
    with closing(connect(path)) as conn, conn:
        assert work.acknowledge_locked(conn, room_id="room", target_install_id="peer",
            route_generation="route-1", record=pending, ack=work.acknowledgement(pending)) is False
        row = conn.execute(
            f"SELECT status, disposition FROM {work.PENDING_TABLE} WHERE room_id='room'").fetchone()
    assert seen["n"] >= 2
    assert phases == []
    assert row["status"] == "pending"
    assert row["disposition"] == "current"


def test_current_owner_capture_still_writes_after_recheck(tmp_path):
    from gateway import hosted_room_work_records as work
    path = tmp_path / "state.db"
    create(path)
    record = work.capture(path, room_id="room", local_gateway_id="owner-a")
    assert record["authority"]["gateway_id"] == "owner-a"
    with closing(connect(path)) as conn:
        stored = conn.execute(
            f"SELECT producer_gateway_id FROM {work.SOURCE_TABLE} WHERE room_id='room'").fetchone()
    assert stored["producer_gateway_id"] == "owner-a"
