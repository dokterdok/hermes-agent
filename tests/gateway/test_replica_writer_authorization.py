"""Adopt the published retention authorize seam onto the composed replica writer.

Retention #99107 ``c9f0029475f085e3b5e66b77df74cd5470925aef`` runs an optional
admission callback inside ``_replica_transaction`` before replica maintenance.
The public recipe splices the runtime writer's double schema init and does not
include that callback. This test pins the integration: the runtime prelude
stays, and a refusal inside the IMMEDIATE writer rolls back without reaching
the body or the in-transaction schema init.

The retention test also expects audit-on-every-open to rewrite ``event_bytes``.
That rewrite already belongs to safety schema init on this composition, so it
is not re-imposed on the runtime transaction here.
"""

import sqlite3

import pytest

from gateway import hosted_room_replicas as replicas


def _open_schema(db):
    with replicas._replica_transaction(db):
        pass


def _seed_marker(db):
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE admission_marker (value TEXT NOT NULL)")
        conn.execute(
            """INSERT INTO hosted_room_replicas
               (room_id, name, members_json, authority_gateway_id, authority_epoch,
                last_seq, latest_seq, event_bytes, created_at, updated_at)
               VALUES ('replica-room', 'Replica', '[]', ?, 1, 0, 0, 17, 1, 1)""",
            ("install:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",),
        )


def test_refusal_stays_inside_writer_and_rolls_back(tmp_path, monkeypatch):
    db = tmp_path / "shared-state.db"
    _open_schema(db)
    _seed_marker(db)
    original = replicas._initialize_replica_schema
    schema_calls = []

    def schema(conn):
        schema_calls.append("schema")
        original(conn)

    monkeypatch.setattr(replicas, "_initialize_replica_schema", schema)
    order = []

    def refuse(conn):
        order.append("authorized")
        assert conn.in_transaction
        with sqlite3.connect(db, timeout=0) as contender:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO admission_marker VALUES ('transient')")
        raise PermissionError("grant withdrawn")

    with pytest.raises(PermissionError, match="grant withdrawn"):
        with replicas._replica_transaction(db, _authorize=refuse):
            order.append("body")

    assert order == ["authorized"]
    assert schema_calls == ["schema"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM admission_marker").fetchone()[0] == 0
        assert conn.execute(
            "SELECT event_bytes FROM hosted_room_replicas WHERE room_id='replica-room'"
        ).fetchone()[0] == 17


def test_allowed_callback_precedes_in_transaction_schema_and_default_is_unchanged(
    tmp_path, monkeypatch
):
    db = tmp_path / "shared-state.db"
    _open_schema(db)
    _seed_marker(db)
    original = replicas._initialize_replica_schema
    order = []

    def schema(conn):
        order.append("schema")
        original(conn)

    monkeypatch.setattr(replicas, "_initialize_replica_schema", schema)

    def allow(conn):
        order.append("authorized")
        assert conn.in_transaction

    with replicas._replica_transaction(db, _authorize=allow) as conn:
        order.append("body")
        conn.execute("INSERT INTO admission_marker VALUES ('allowed')")

    assert order == ["schema", "authorized", "schema", "body"]
    order.clear()
    with replicas._replica_transaction(db) as conn:
        order.append("default")
        assert conn.execute("SELECT value FROM admission_marker").fetchone()[0] == "allowed"
    assert order == ["schema", "schema", "default"]
