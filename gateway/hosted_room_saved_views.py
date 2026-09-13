"""Bounded metadata discovery for existing passive copies, never recovery authority.

Uses the read-only snapshot contract from the retained recovery implementation.
Selecting a copy still requires the separately audited recovery preview.
"""
import math
from pathlib import Path
import sqlite3

from gateway.hosted_room_recovery_read import literal_id, readonly
from gateway.hosted_rooms import MAX_ACTOR_ID_CHARS, MAX_ROOM_ID_CHARS, MAX_ROOM_NAME_CHARS, _validate_room_name
from gateway.hosted_rooms_common import table_exists

MAX_PAGE = 20


def _text(column, limit):
    return f"CASE WHEN typeof({column})='text' AND length(CAST({column} AS BLOB))<={limit} THEN {column} END"


def page_parameters(params):
    limit, after = params.get('limit', MAX_PAGE), params.get('after_room_id')
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError('Invalid saved-copy page')
    if after is not None:
        literal_id(after)
    return limit, after


def _summary(row):
    room_id = literal_id(row['room_id'])
    gateway = literal_id(row['authority_gateway_id'])
    name = _validate_room_name(row['name'])
    epoch, saved, latest = row['authority_epoch'], row['last_seq'], row['latest_seq']
    received = row['updated_at']
    if (type(epoch) is not int or epoch < 1 or type(saved) is not int or saved < 0
            or type(latest) is not int or latest < saved
            or type(received) not in (int, float) or not math.isfinite(received)):
        raise ValueError('Saved-copy metadata is unavailable')
    return {'room_id': room_id, 'name': name,
            'source_authority': {'gateway_id': gateway, 'epoch': epoch},
            'saved_through_seq': saved, 'advertised_latest_seq': latest,
            'copy_updated_at': received, 'group_ended': bool(row['group_ended']),
            'copy_status': 'needs_review' if row['needs_review'] else
                'retired' if row['retired'] else 'saved'}


def list_saved_copies(db_path, *, limit, after_room_id):
    """List only metadata; do not audit all history, create a store or repair it."""
    result = {'object': 'hermes.group_recovery.copies', 'copies': [], 'next_room_id': None,
              'accepted_tail': 'unverified', 'execution_authorized': False}
    path = Path(db_path)
    try:
        path.stat()
    except FileNotFoundError:
        return result
    with readonly(path) as conn:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, 'hosted_room_replicas'):
            return result
        from gateway.hosted_room_replica_retirement import RETIREMENT_TABLE
        retired = (f'EXISTS(SELECT 1 FROM {RETIREMENT_TABLE} t WHERE t.room_id=r.room_id)'
                   if table_exists(conn, RETIREMENT_TABLE) else '0')
        # Bound damaged on-disk fields before returning values to Python.
        selected = ','.join((
            f"{_text('r.room_id', MAX_ROOM_ID_CHARS)} AS room_id",
            f"{_text('r.name', MAX_ROOM_NAME_CHARS * 4)} AS name",
            f"{_text('r.authority_gateway_id', MAX_ACTOR_ID_CHARS)} AS authority_gateway_id",
            *(f"CASE WHEN typeof(r.{column})='integer' THEN r.{column} END AS {column}"
              for column in ('authority_epoch', 'last_seq', 'latest_seq')),
            "CASE WHEN typeof(r.updated_at) IN ('integer','real') THEN r.updated_at END AS updated_at",
            'r.disbanded_at IS NOT NULL AS group_ended',
            'r.quarantine_reason IS NOT NULL AS needs_review', f'{retired} AS retired'))
        rows = conn.execute(f'''SELECT {selected}
            FROM hosted_room_replicas r WHERE (? IS NULL OR r.room_id>?)
            ORDER BY r.room_id ASC LIMIT ?''', (after_room_id, after_room_id, limit + 1)).fetchall()
        result['copies'] = [_summary(row) for row in rows[:limit]]
        if len(rows) > limit:
            result['next_room_id'] = result['copies'][-1]['room_id']
    return result
