"""Proof-kind schema upgrades retain verified history and its transaction fences."""

from contextlib import closing
import sqlite3

import pytest

from gateway import hosted_room_replicas as replicas
from gateway import hosted_room_safety as safety
from gateway import hosted_rooms as rooms
from gateway.hosted_rooms_common import open_sqlite
from tests.gateway.test_hosted_room_verified_transitions import (
    AUTH_B, _copy, _evidence, _hosted, _insert_transition, _mark, _marks, _proof, _transition,
)


def _older_store(tmp_path, monkeypatch, previous_kinds):
    db, _ = _copy(tmp_path, room_id="preserved")
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    replicas.promote_replica(db, room_id="preserved", transition=_transition(_proof(room_id="preserved")))
    _copy(tmp_path)
    # Persist an older version's CHECK while retaining its real mark, use and verified log.
    # Keep current triggers, so opening the store must notice the stale table constraint itself.
    with sqlite3.connect(db) as conn:
        triggers = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql LIKE '%hosted_room_verified_transitions%'"
        ).fetchall()
        for name, _ in triggers:
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute(f"""CREATE TABLE old_marks (
            room_id TEXT NOT NULL,
            from_epoch INTEGER NOT NULL CHECK (from_epoch >= 1),
            to_epoch INTEGER NOT NULL CHECK (to_epoch > from_epoch),
            successor_gateway_id TEXT NOT NULL,
            proof_kind TEXT NOT NULL CHECK (proof_kind IN ({previous_kinds})),
            proof_digest TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (room_id, to_epoch),
            FOREIGN KEY (room_id, to_epoch)
                REFERENCES hosted_room_verified_transition_uses (room_id, to_epoch)
                DEFERRABLE INITIALLY DEFERRED)""")
        conn.execute("INSERT INTO old_marks SELECT * FROM hosted_room_verified_transitions")
        conn.execute("DROP TABLE hosted_room_verified_transitions")
        conn.execute("ALTER TABLE old_marks RENAME TO hosted_room_verified_transitions")
        for _, definition in triggers:
            conn.execute(definition)
    return db


@pytest.mark.parametrize("previous_kinds", ["'certified', 'attested'", "'certified', 'attested', 'handover'"])
def test_old_proof_constraints_upgrade_without_losing_verified_history(tmp_path, monkeypatch, previous_kinds):
    db = _older_store(tmp_path, monkeypatch, previous_kinds)
    preserved = _marks(db)
    promoted = replicas.promote_replica(
        db, room_id="room-1", transition=_transition(_evidence(), "evidence"))
    assert promoted["authority_gateway_id"] == AUTH_B
    assert tuple([row for row in rows if row[0] == "preserved"] for rows in _marks(db)) == preserved
    assert rooms.read_events(db, room_id="preserved")["events"][-1]["payload"]["proof"] == _proof(room_id="preserved")
    assert rooms.room_state(db, room_id="preserved")["authority_gateway_id"] == AUTH_B

    # The rebuilt table still needs a use in the mark's own transaction.
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with rooms._transaction(db, immediate=True) as conn:
            _mark(conn, room_id="unconsumed")
    assert not any(row[0] == "unconsumed" for row in _marks(db)[0])
    _hosted(tmp_path, room_id="unverified")
    with rooms._transaction(db, immediate=True) as conn:
        _insert_transition(conn, "unverified", 3)
    with pytest.raises(rooms.RoomQuarantinedError):
        rooms.room_state(db, room_id="unverified")


def test_aborted_schema_upgrade_keeps_the_old_marks_and_can_retry(tmp_path, monkeypatch):
    db = _older_store(tmp_path, monkeypatch, "'certified', 'attested'")
    preserved = _marks(db)
    with closing(open_sqlite(db)) as conn:
        with pytest.raises(RuntimeError, match="interrupted upgrade"):
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                safety.initialize_safety_schema(conn)
                raise RuntimeError("interrupted upgrade")
        # A new connection still sees the old constraint; no half-upgraded schema escaped.
    with closing(open_sqlite(db)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(rooms.VerifiedTransitionError) as caught:
                _mark(conn, kind="evidence")
            assert "CHECK constraint failed" in str(caught.value.__cause__)
    assert _marks(db) == preserved
    promoted = replicas.promote_replica(
        db, room_id="room-1", transition=_transition(_evidence(), "evidence"))
    assert promoted["authority_gateway_id"] == AUTH_B
