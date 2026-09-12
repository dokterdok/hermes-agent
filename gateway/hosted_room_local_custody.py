"""Private, non-executable context custody independent of canonical retention.

Only admission records or the one-time valid-binding migration write this table.
Observation opens existing SQLite files read-only and selects metadata, not content.
"""
from __future__ import annotations

import json
import math
import sqlite3

from gateway.hosted_room_recovery_read import literal_id, readonly
from gateway.hosted_rooms_common import compact_json, table_exists

TABLE = "hosted_room_local_custody"
MAX_ROWS = 512
MAX_CHAIN = 101


def fail():
    from hermes_state_runtime import RuntimeStoreError
    return RuntimeStoreError('original_custody_unavailable')


def metadata_chain(conn, anchor):
    """Follow only unique compression edges; do not load prompts or messages."""
    chain, seen = [], set()
    key = anchor
    while len(chain) < MAX_CHAIN:
        literal_id(key)
        row = conn.execute("SELECT id,started_at,source,archived,end_reason,profile_name "
                           "FROM sessions WHERE id=?", (key,)).fetchone()
        if row is None or key in seen:
            raise fail()
        key, stamp, source, archived, end, profile = tuple(row)
        if (source != "bot_room" or archived or profile not in (None, "default")
                or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0):
            raise fail()
        seen.add(key)
        chain.append([key, stamp])
        if len(compact_json(chain).encode()) > 32768:
            raise fail()
        children = conn.execute("SELECT id FROM sessions WHERE parent_session_id=? LIMIT 2", (key,)).fetchall()
        if end != "compression":
            # Branches that are not compression descendants are not continuation.
            return chain
        if len(children) != 1:
            raise fail()
        key = children[0][0]
    raise fail()


def initialize(conn):
    from gateway.hosted_room_custody_schema import initialize_schema
    if table_exists(conn, 'hosted_room_local_sessions') and not table_exists(conn, TABLE):
        from hermes_state_runtime import RuntimeStoreError
        raise RuntimeStoreError('legacy_custody_migration_required')
    initialize_schema(conn)


def validate_row(row, chain):
    for field in ('room_id', 'member_id', 'profile', 'gateway_id', 'session_id', 'first_task_id', 'last_session_id'):
        literal_id(row[field])
    if type(chain) is not list or any(type(p) is not list or len(p) != 2 for p in chain):
        raise fail()
    if any(type(row[field]) not in (int, float) or not math.isfinite(row[field]) or row[field] < 0
           for field in ('created_at', 'session_started_at', 'last_session_started_at')):
        raise fail()
    if (type(row['first_execution_generation']) is not int or not 1 <= row['first_execution_generation'] <= 2**63 - 1
            or not chain or len(chain) > MAX_CHAIN or chain[0] != [row['session_id'], row['session_started_at']]
            or chain[-1] != [row['last_session_id'], row['last_session_started_at']]):
        raise fail()
    if len({literal_id(p[0]) for p in chain}) != len(chain):
        raise fail()
    if len(compact_json(chain).encode()) > 32768:
        raise fail()
    if any(type(p[1]) not in (int, float) or not math.isfinite(p[1]) or p[1] < 0 for p in chain):
        raise fail()


def save(conn, row, chain):
    from gateway.hosted_room_custody_schema import validate_schema
    validate_schema(conn)
    chain = [list(pair) for pair in chain]
    validate_row(row, chain)
    prior = conn.execute(f'SELECT * FROM {TABLE} WHERE room_id=? AND member_id=?',
                         (row['room_id'], row['member_id'])).fetchone()
    encoded = compact_json(chain)
    if prior is not None:
        old = dict(prior)
        validate_row(old, json.loads(old["chain_json"]))
        if (any(old[k] != v for k, v in row.items() if not k.startswith('last_'))
                or chain[:len(json.loads(old['chain_json']))] != json.loads(old['chain_json'])):
            raise fail()
        conn.execute(f'UPDATE {TABLE} SET last_session_id=?,last_session_started_at=?,chain_json=? '
                     'WHERE room_id=? AND member_id=?',
                     (*chain[-1], encoded, row['room_id'], row['member_id']))
    else:
        fields = ','.join(row)
        conn.execute(f'INSERT INTO {TABLE} ({fields},chain_json) VALUES ({",".join("?" for _ in row)},?)',
                     (*row.values(), encoded))


def verify_locked(conn, *, room_id, member_id, gateway_id, profile, missing_ok=False):
    """Return the verified row and digest from the caller's pinned transaction."""
    if profile != 'default' or not conn.in_transaction:
        raise fail()
    from gateway.hosted_room_custody_schema import validate_schema
    validate_schema(conn)
    row = conn.execute(f'SELECT * FROM {TABLE} WHERE room_id=? AND member_id=?',
                       (room_id, member_id)).fetchone()
    if row is None and missing_ok:
        return None
    if row is None or row['profile'] != profile or row['gateway_id'] != gateway_id:
        raise fail()
    chain = metadata_chain(conn, row['session_id'])
    old = json.loads(row['chain_json'])
    validate_row(row, old)
    if (not old or chain[:len(old)] != old or old[0] != [row['session_id'], row['session_started_at']]
            or old[-1] != [row['last_session_id'], row['last_session_started_at']]):
        raise fail()
    import hashlib
    verified = dict(row)
    digest = hashlib.sha256(compact_json({'custody': verified, 'chain': chain}).encode()).hexdigest()
    return verified, digest


def verify(path, *, room_id, member_id, gateway_id, profile):
    if profile != 'default':
        raise fail()
    with readonly(path) as conn:
        conn.row_factory = sqlite3.Row
        return verify_locked(conn, room_id=room_id, member_id=member_id,
                             gateway_id=gateway_id, profile=profile)[1]
