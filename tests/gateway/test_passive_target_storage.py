"""Migration, shared capacity and historical scopes in passive target storage."""

import json
import sqlite3

import pytest

from gateway import hosted_room_replicas as replicas
from gateway import hosted_room_replica_retirement as retirement
from gateway import hosted_room_work_records as work
from gateway import hosted_room_work_storage as storage
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair, TARGET  # noqa: F401


@pytest.mark.parametrize("outer", [False, True])
def test_target_migration_preserves_bytes_and_orphans_without_stealing_transaction(pair, outer):
    pair.copy()
    record = pair.record()
    raw = json.dumps(record, indent=3)
    with rooms._transaction(pair.target, immediate=True) as conn:
        conn.execute(f"CREATE TABLE {work.TARGET_TABLE} (room_id TEXT PRIMARY KEY,revision INTEGER,digest TEXT,record_json TEXT)")
        conn.execute(f"INSERT INTO {work.TARGET_TABLE} VALUES (?,?,?,?)", ("room", record["revision"], record["digest"], raw))
        conn.execute(f"INSERT INTO {work.TARGET_TABLE} VALUES ('orphan',7,'invalid','opaque-original')")
    from gateway.hosted_rooms_common import open_sqlite
    from contextlib import closing
    with closing(open_sqlite(pair.target)) as conn:
        if outer:
            conn.execute("BEGIN IMMEDIATE")
        work.initialize(conn)
        assert conn.in_transaction is outer
        assert conn.execute(f"SELECT record_json FROM {work.TARGET_TABLE}").fetchone()[0] == raw
        assert conn.execute(f"SELECT record_json FROM {storage.INVALID_TABLE}").fetchone()[0] == "opaque-original"
        if outer:
            conn.rollback()
            assert "producer_epoch" not in {r["name"] for r in conn.execute(f"PRAGMA table_info({work.TARGET_TABLE})")}
            work.initialize(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {storage.INVALID_TABLE}")
    assert pair.ingest_work(record) == work.acknowledgement(record)


@pytest.mark.parametrize("table", [work.SOURCE_TABLE, work.TARGET_TABLE, work.PENDING_TABLE])
@pytest.mark.parametrize("bound", ["bytes", "rows"])
def test_source_target_pending_and_opaque_evidence_share_capacity(pair, monkeypatch, table, bound):
    pair.copy()
    with rooms._transaction(pair.target, immediate=True) as conn:
        conn.execute(f"CREATE TABLE {work.TARGET_TABLE} (room_id TEXT PRIMARY KEY,revision INTEGER,digest TEXT,record_json TEXT)")
        conn.execute(f"INSERT INTO {work.TARGET_TABLE} VALUES ('orphan',7,'invalid','opaque-original')")
    pair.ingest_work()
    rooms.create_room(pair.target, room_id="local", name="Local", members=[{"profile": "ops", "handle": "ops"}],
                      authority_gateway_id=TARGET)
    with rooms._transaction(pair.target, immediate=True) as conn:
        work.prepare_delivery_locked(conn, room_id="local", target_install_id="another",
            route_generation="route", local_gateway_id=TARGET, through_seq=0)
        total_sql, rows_sql = storage.usage_sql((work.SOURCE_TABLE, work.TARGET_TABLE, work.PENDING_TABLE, storage.INVALID_TABLE))
        size, count = conn.execute(f"SELECT {total_sql},{rows_sql}").fetchone()
        original = dict(conn.execute(f"SELECT * FROM {table}").fetchone())
    monkeypatch.setattr(work, "MAX_STORE_BYTES", size if bound == "bytes" else work.MAX_STORE_BYTES)
    monkeypatch.setattr(work, "MAX_STORE_ROWS", count if bound == "rows" else work.MAX_STORE_ROWS)
    with rooms._transaction(pair.target, immediate=True) as conn:
        work.initialize(conn)
        work._budget(conn, table, original)  # Only this exact row receives replacement credit.
        other = {**original, "producer_gateway_id": "new-owner"}
        with pytest.raises(work.WorkRecordCapacityError):
            work._budget(conn, table, other)
        with pytest.raises(sqlite3.IntegrityError, match="storage is full"):
            conn.execute(f"INSERT INTO {table} ({','.join(other)}) VALUES ({','.join('?' for _ in other)})", tuple(other.values()))
        assert dict(conn.execute(f"SELECT * FROM {table}").fetchone()) == original


def test_new_enrollment_keeps_old_producer_evidence_historical_and_immutable(pair):
    pair.enroll()
    pair.copy()
    pair.ingest_work()
    with rooms._transaction(pair.target) as conn:
        original = dict(conn.execute(f"SELECT * FROM {work.TARGET_TABLE}").fetchone())
    pair.successor_fixture()
    public, history, _ = pair.enrollment()
    raw = {**public, "enrollment_id": "next-enrollment", "nonce": "next-nonce"}
    value = retirement._closing_value(pair.secret, raw)
    raw["commitment"] = retirement._commitment(value, raw)
    retirement.enroll_target(pair.target, enrollment=retirement._public(raw), authority_history=history,
                             target_install_id=TARGET, expected_enrollment_id="enrollment")
    with rooms._transaction(pair.target, immediate=True) as conn:
        historical = dict(conn.execute(f"SELECT * FROM {work.TARGET_TABLE}").fetchone())
        assert historical == {**original, "disposition": "historical"}
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE {work.TARGET_TABLE} SET record_json='rewritten' WHERE producer_epoch=1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {work.TARGET_TABLE} WHERE producer_epoch=1")
    pair.copy()
    pair.ingest_work()
    summary = replicas.replica_state(pair.target, room_id="room")["work_records"]
    assert {scope["disposition"] for scope in summary["scopes"]} == {"historical", "current"}
    assert summary["producer"]["epoch"] == 2
    assert summary["source_loss_safe"] is False
