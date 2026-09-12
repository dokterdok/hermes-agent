"""Target evidence invalidation and retirement guards on disposable stores."""

import sqlite3

import pytest

from gateway import hosted_room_replicas as replicas
from gateway import hosted_room_replica_retirement as retirement
from gateway import hosted_room_replica_retention as retention
from gateway import hosted_room_work_records as work
from gateway import hosted_room_work_storage as storage
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair, TARGET  # noqa: F401


@pytest.mark.parametrize("opaque", [None, b"\x00\xffmetadata"])
@pytest.mark.parametrize("column,value", [
    ("room_id", "other"), ("producer_epoch", 3), ("record_json", "{}"),
    ("opaque_metadata", b"changed"), ("rowid", 99),
])
def test_quarantine_invalidation_is_one_way_and_preserves_every_column(pair, opaque, column, value):
    pair.copy()
    pair.ingest_work()
    with rooms._transaction(pair.target, immediate=True) as conn:
        conn.execute(f"ALTER TABLE {work.TARGET_TABLE} ADD COLUMN opaque_metadata")
        conn.execute(f"UPDATE {work.TARGET_TABLE} SET opaque_metadata=?", (opaque,))
        conn.execute("UPDATE hosted_room_replica_events SET payload_json='not-json' WHERE seq=1")
    assert replicas.replica_state(pair.target, room_id="room")["safety_status"] == "quarantined"
    with rooms._transaction(pair.target, immediate=True) as conn:
        before = dict(conn.execute(f"SELECT * FROM {work.TARGET_TABLE}").fetchone())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE {work.TARGET_TABLE} SET disposition='invalid',{column}=?", (value,))
        assert dict(conn.execute(f"SELECT * FROM {work.TARGET_TABLE}").fetchone()) == before
        conn.execute(f"UPDATE {work.TARGET_TABLE} SET disposition='invalid'")
        assert dict(conn.execute(f"SELECT * FROM {work.TARGET_TABLE}").fetchone()) == {**before, "disposition": "invalid"}
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE {work.TARGET_TABLE} SET disposition='current'")


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("partial", [False, True])
def test_retirement_cleans_work_and_fences_old_and_new_destinations(pair, version, partial):
    if version == 2:
        pair.successor_fixture()
    public, closing_value = pair.enroll()
    pair.copy(limit=1 if partial else 100)
    if not partial:
        pair.ingest_work()
    with rooms._transaction(pair.target, immediate=True) as conn:
        work.initialize(conn)
    notice = {k: v for k, v in public.items() if k not in {"roster_sha256", "commitment"}}
    retired = retirement.retire_copy(pair.target, payload=notice, value=closing_value, local_gateway_id=TARGET)
    assert retired["stored_seq"] == (1 if partial else pair.record()["history"]["seq"])
    assert retirement.retire_copy(pair.target, payload=notice, value=closing_value, local_gateway_id=TARGET) == retired
    rooms.create_room(pair.source, room_id="active", name="Active", members=pair.members, authority_gateway_id=pair.gateway)
    record = work.capture(pair.source, room_id="active", local_gateway_id=pair.gateway)
    replicas.ingest_page(pair.target, room_id="active", room_name="Active", members=pair.members,
                         page=rooms.read_events(pair.source, room_id="active"))
    with rooms._transaction(pair.target, immediate=True) as conn:
        work.initialize(conn)
        assert conn.execute(f"SELECT * FROM {work.TARGET_TABLE} WHERE room_id='room'").fetchone() is None
        storage.save_locked(conn, work.TARGET_TABLE, record)
        for table in (work.TARGET_TABLE, "hosted_room_replicas"):
            with pytest.raises(sqlite3.IntegrityError, match="retired"):
                conn.execute(f"UPDATE {table} SET room_id='room' WHERE room_id='active'")
        with pytest.raises(sqlite3.IntegrityError, match="retired"):
            conn.execute("UPDATE hosted_room_replicas SET room_id='other' WHERE room_id='room'")
        with pytest.raises(sqlite3.IntegrityError, match="retired"):
            conn.execute("UPDATE hosted_room_replica_events SET room_id='active' WHERE room_id='room'")
        with pytest.raises(sqlite3.IntegrityError, match="retired"):
            storage.save_locked(conn, work.TARGET_TABLE, {**record, "room_id": "room"})


@pytest.mark.parametrize("mode", ["age", "rooms", "bytes"])
@pytest.mark.parametrize("protected", [False, True])
def test_retired_partial_v2_pruning_keeps_identity_and_quarantine(pair, mode, protected):
    pair.successor_fixture()
    public, closing_value = pair.enroll()
    pair.copy(limit=1)
    notice = {k: v for k, v in public.items() if k not in {"roster_sha256", "commitment"}}
    retired = retirement.retire_copy(pair.target, payload=notice, value=closing_value, local_gateway_id=TARGET)
    assert retired["lineage_status"] == "pending"
    with retirement._transaction(pair.target) as conn:
        if protected:
            conn.execute("UPDATE hosted_room_replicas SET quarantine_reason='preserve',quarantined_at=1 WHERE room_id='room'")
        kwargs = {"now": None}
        if mode == "age":
            kwargs["now"] = retired["retired_at"] + rooms.DISBANDED_REPLICA_RETENTION_SECONDS + 1
        else:
            kwargs["max_replica_rooms" if mode == "rooms" else "max_replica_event_bytes"] = 0
        assert retention._prune_disbanded_replicas_locked(conn, **kwargs) == (0 if protected else 1)
        assert conn.execute("SELECT owner_kind FROM hosted_room_id_reservations WHERE room_id='room'").fetchone()[0] == "replica"
    assert retirement.retire_copy(pair.target, payload=notice, value=closing_value, local_gateway_id=TARGET) == retired
