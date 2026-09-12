"""Future passive descriptor contracts exercised without runtime ownership."""

import json
import sqlite3
from contextlib import closing

import pytest

from gateway import hosted_room_passive_lineage as lineage
from gateway import hosted_room_work_lineage as provenance
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms
from gateway.hosted_room_replica_retirement import RetirementCapacityError


def source(path):
    rooms.create_room(path, room_id="room", name="Room", members=[{"profile": "ops", "handle": "ops"}],
                      authority_gateway_id="owner-a", now=10)
    rooms.append_event(path, room_id="room", event_id="input", kind="message.user",
        actor={"kind": "user", "id": "alice"}, payload={"text": "hello"},
        authority_gateway_id="owner-a", authority_epoch=1, now=11)
    # Fixture is already-retained history, not a promotion or takeover operation.
    with rooms._transaction(path, immediate=True) as conn:
        conn.execute("UPDATE hosted_rooms SET authority_gateway_id='owner-b',authority_epoch=2,next_seq=3 WHERE room_id='room'")
        conn.execute("INSERT INTO hosted_room_events VALUES ('room',2,'claim','authority.claimed',?,2,?,12)",
            (json.dumps({"kind": "system", "id": "authority-control"}), json.dumps({
                "previous_gateway_id": "owner-a", "authority_gateway_id": "owner-b", "authority_epoch": 2})))


@pytest.mark.parametrize("prefix", [0, 1, 2, 3])
def test_work_prefix_requires_retained_claim_and_preserves_current_capture(tmp_path, prefix):
    path = tmp_path / "source.db"
    source(path)
    with rooms._transaction(path) as conn:
        if prefix != 2:
            with pytest.raises(work.WorkRecordPrefixError):
                provenance.source_prefix_locked(conn, "room", {"gateway_id": "owner-b", "epoch": 2}, prefix)
        else:
            spans, digest = provenance.source_prefix_locked(conn, "room", {"gateway_id": "owner-b", "epoch": 2}, prefix)
            assert lineage.status(spans, prefix) == "verified"
            assert digest == lineage.source_locked(conn, "room", {"gateway_id": "owner-b", "epoch": 2})[1]
    record = work.capture(path, room_id="room", local_gateway_id="owner-b")
    assert record["version"] == 2
    assert record["incompleteness"] == ["prior_authority_work_unknown"]
    assert work.capture(path, room_id="room", local_gateway_id="owner-b") == record


@pytest.mark.parametrize("bound", ["count", "bytes", "single"])
def test_descriptor_capacity_is_shared_transactional_and_utf8_bounded(tmp_path, monkeypatch, bound):
    descriptor = [{"gateway_id": "owner-a", "epoch": 1, "from_seq": 0}]
    _, encoded, digest = lineage.descriptor(descriptor, gateway_id="owner-a", epoch=1)
    size = len(encoded.encode("utf-8"))
    monkeypatch.setattr(lineage, "MAX_STORED_DESCRIPTORS", 1 if bound == "count" else 512)
    monkeypatch.setattr(lineage, "MAX_STORED_DESCRIPTOR_BYTES", size if bound == "bytes" else 4 * 1024 * 1024)
    monkeypatch.setattr(lineage, "MAX_DESCRIPTOR_BYTES", size if bound == "single" else 8 * 1024)
    with closing(sqlite3.connect(tmp_path / "retention.db")) as conn:
        conn.row_factory = sqlite3.Row
        # Only legacy columns consumed by this additive schema migration.
        for table in (lineage.HOME, lineage.ENROLLMENTS):
            conn.execute(f"CREATE TABLE {table} (enrollment_id TEXT PRIMARY KEY)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        lineage.initialize(conn)
        assert conn.in_transaction
        conn.rollback()
        assert [r["name"] for r in conn.execute(f"PRAGMA table_info({lineage.HOME})")] == ["enrollment_id"]
        lineage.initialize(conn)
        lineage.ensure_descriptor_capacity(conn, encoded)
        conn.execute(f"INSERT INTO {lineage.HOME} VALUES ('first',2,?,?)", (digest, encoded))
        too_large = encoded + " " if bound == "single" else encoded
        if bound != "single":
            with pytest.raises(RetirementCapacityError):
                lineage.ensure_descriptor_capacity(conn, encoded)
        # Direct SQLite writers cannot exceed caps across either ledger.
        with pytest.raises(sqlite3.IntegrityError, match="capacity"):
            conn.execute(f"INSERT INTO {lineage.ENROLLMENTS} VALUES ('second',2,?,?)", (digest, too_large))
        if bound != "count":
            with pytest.raises(sqlite3.IntegrityError, match="capacity"):
                conn.execute(f"UPDATE {lineage.HOME} SET authority_history_json=? WHERE enrollment_id='first'", (encoded * 2,))
        conn.execute(f"UPDATE {lineage.HOME} SET authority_history_json=? WHERE enrollment_id='first'", (encoded,))
        lineage.initialize(conn)
        assert conn.execute(f"SELECT authority_history_json FROM {lineage.HOME}").fetchone()[0] == encoded


def test_source_descriptor_bound_preserves_retained_work_revision(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    source(path)
    original = work.capture(path, room_id="room", local_gateway_id="owner-b")
    with rooms._transaction(path) as conn:
        history, _ = lineage.source_locked(conn, "room", original["authority"])
        before = conn.execute(f"SELECT record_json FROM {work.SOURCE_TABLE}").fetchone()[0]
    monkeypatch.setattr(lineage, "MAX_DESCRIPTOR_BYTES", len(lineage.canonical(history).encode("utf-8")) - 1)
    with pytest.raises(work.WorkRecordPrefixError):
        work.capture(path, room_id="room", local_gateway_id="owner-b")
    with rooms._transaction(path) as conn:
        assert conn.execute(f"SELECT record_json FROM {work.SOURCE_TABLE}").fetchone()[0] == before
