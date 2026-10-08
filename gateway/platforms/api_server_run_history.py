"""Bounded history evidence after exact RoomLink receipts have been retired."""
import json
import sqlite3


UNINDEXED = 'unindexed'


def initialize(conn):
    existed = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_room_history'").fetchone()
    conn.execute("""CREATE TABLE IF NOT EXISTS run_room_history (
        lineage_key TEXT PRIMARY KEY, evidence TEXT NOT NULL)""")
    if existed is None:
        # An older installation may already have compacted/pruned receipts. Its
        # surviving authority floors cannot certify that the lost history was empty.
        conn.execute("""INSERT OR IGNORE INTO run_room_history(lineage_key,evidence)
            SELECT authority_key,? FROM run_room_authorities""", (UNINDEXED,))


def _lineage_key(conn, identity):
    from gateway.platforms.api_server_room_origins import retained, target_key
    from gateway.platforms.api_server_run_authority import room_authority, canonical
    origin = retained(conn, identity)
    known_home = conn.execute("""SELECT 1 FROM run_room_origin_homes
        WHERE target_key=? AND home_install_id=?""", (target_key(identity), identity['home_install_id'])).fetchone()
    if origin is not None and known_home is not None:
        return room_authority({**identity, 'home_install_id': origin[0]})[0]
    return canonical(conn, room_authority(identity))[0]


def is_non_admission(status, fingerprint, owner_pid, owner_started):
    return (status.get('admission_cancelled') is True and status.get('status') == 'cancelled'
            and fingerprint == '' and not owner_pid and not owner_started)


def remember(conn, scope, authority_key, status, fingerprint, owner_pid, owner_started, indexed):
    """Deletion may forget a key, never whether that lineage lost possible execution evidence."""
    if is_non_admission(status, fingerprint, owner_pid, owner_started):
        return
    from gateway.platforms.api_server_run_scope import stored_room_scope
    identity = stored_room_scope(conn, scope)
    key = _lineage_key(conn, identity) if identity is not None else authority_key
    if key is None:
        return  # Ordinary API receipts have no authenticated room identity.
    conn.execute("""INSERT INTO run_room_history(lineage_key,evidence) VALUES(?,?)
        ON CONFLICT(lineage_key) DO UPDATE SET evidence=CASE
        WHEN run_room_history.evidence=excluded.evidence THEN excluded.evidence ELSE ? END""",
        (key, indexed or UNINDEXED, UNINDEXED))


def history(conn, identity):
    from gateway.platforms.api_server_run_authority import canonical, room_authority
    keys = (_lineage_key(conn, identity), canonical(conn, room_authority(identity))[0])
    values = {row[0] for row in conn.execute(
        'SELECT evidence FROM run_room_history WHERE lineage_key IN (?,?)', keys)}
    return next(iter(values)) if len(values) == 1 else UNINDEXED if values else None


def canonical_proof(db, run_id, scope):
    """Certify a real immutable projection, never transport status or current runtime mode."""
    from hermes_state_errors import StateDbReplacedError
    from hermes_state_logical_attempts import _schema_identity
    from hermes_state_runtime import RuntimeStoreError
    try:
        db._halt_if_db_generation_changed()
        with db._read_ctx() as conn:
            cookie = _schema_identity(conn)
            rows = conn.execute("""SELECT session_id FROM logical_attempts WHERE principal_id='api'
                AND request_id=? AND owner_scope=? AND task_id IS NOT NULL LIMIT 2""", (run_id, scope)).fetchall()
            if len(rows) == 1:
                return json.dumps([cookie, rows[0][0]], separators=(',', ':'))
    except (sqlite3.Error, StateDbReplacedError, RuntimeStoreError):
        # Missing certification makes later absence unknown; it never invalidates
        # an admission that already committed in the canonical writer.
        return None
    return None


def certified_session(db, evidence):
    from hermes_state_errors import StateDbReplacedError
    from hermes_state_logical_attempts import _schema_identity
    from hermes_state_runtime import RuntimeStoreError
    try:
        values = json.loads(evidence)
        if (not isinstance(values, list) or len(values) != 2
                or any(not isinstance(value, str) or not value for value in values)):
            return None
        db._halt_if_db_generation_changed()
        with db._read_ctx() as conn:
            return values[1] if _schema_identity(conn) == values[0] else None
    except (ValueError, TypeError, sqlite3.Error, StateDbReplacedError, RuntimeStoreError):
        return None
