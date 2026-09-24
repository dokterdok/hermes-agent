"""Lower viewer helper: real SQLite, no Files, service startup or migrations."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from gateway.hosted_rooms import HostedRoomError, local_authority_gateway_id
from gateway.hosted_room_viewer_state import owned_viewer_room, viewer_snapshot


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    owner = local_authority_gateway_id()
    db = tmp_path / "room.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE hosted_rooms(room_id TEXT PRIMARY KEY, authority_gateway_id TEXT NOT NULL, authority_epoch INTEGER NOT NULL, disbanded_at REAL)")
        conn.execute("INSERT INTO hosted_rooms VALUES ('r', ?, 1, NULL)", (owner,))
        conn.commit()
    return db, owner


@pytest.mark.parametrize("state", ["live", "missing-db", "missing-room", "foreign", "epoch", "disbanded", "quarantine", "disband-fence", "malformed-fence", "view-room"])
def test_existing_authority_without_schema_or_identity_creation(tmp_path, monkeypatch, state):
    db, owner = _seed(tmp_path, monkeypatch)
    if state == "missing-db":
        db = tmp_path / "absent" / "room.db"
    else:
        with closing(sqlite3.connect(db)) as conn:
            if state == "missing-room":
                conn.execute("DELETE FROM hosted_rooms")
            elif state == "foreign":
                conn.execute("UPDATE hosted_rooms SET authority_gateway_id='foreign'")
            elif state == "epoch":
                conn.execute("UPDATE hosted_rooms SET authority_epoch=0")
            elif state == "disbanded":
                conn.execute("UPDATE hosted_rooms SET disbanded_at=1")
            elif state in {"quarantine", "disband-fence"}:
                table = "hosted_room_quarantine" if state == "quarantine" else "hosted_room_disband_fences"
                conn.execute(f"CREATE TABLE {table}(room_id TEXT PRIMARY KEY)")
                conn.execute(f"INSERT INTO {table} VALUES ('r')")
            elif state == "malformed-fence":
                conn.execute("CREATE TABLE hosted_room_quarantine(wrong TEXT)")
            elif state == "view-room":
                conn.execute("ALTER TABLE hosted_rooms RENAME TO not_rooms")
                conn.execute("CREATE VIEW hosted_rooms AS SELECT * FROM not_rooms")
            conn.commit()
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if state == "live":
        room = owned_viewer_room(db, room_id="r")
        assert (room["room_id"], room["authority_gateway_id"], room["authority_epoch"]) == ("r", owner, 1)
        with pytest.raises(HostedRoomError):
            with viewer_snapshot(db) as conn:
                assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
                conn.execute("INSERT INTO hosted_rooms VALUES ('not-allowed', 'foreign', 1, NULL)")
    else:
        with pytest.raises(HostedRoomError):
            owned_viewer_room(db, room_id="r")
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("lock", ["IMMEDIATE", "EXCLUSIVE"])
def test_contention_never_initializes_or_waits_for_writer(tmp_path, monkeypatch, lock):
    db, owner = _seed(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db)) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute("BEGIN " + lock)
        reading = pool.submit(owned_viewer_room, db, room_id="r")
        try:
            if lock == "IMMEDIATE":
                assert reading.result(timeout=2)["authority_gateway_id"] == owner
            else:
                with pytest.raises(HostedRoomError):
                    reading.result(timeout=2)
        finally:
            writer.rollback()
