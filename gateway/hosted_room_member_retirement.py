"""Durable imported-member retirement, with network I/O outside store/policy locks."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from gateway import hosted_room_links as links, hosted_rooms as rooms
from gateway.hosted_rooms_common import table_exists
from hermes_state_runtime import RuntimeStoreError


def _targets(payload, member):
    member_id = payload.get('target_member_id')
    return (member_id == member['member_id'] if member_id else
            payload.get('target_profile') in {member['member_id'], member['profile']})


def require_member_work_open(conn, room_id, payload, *, error):
    """The admission writer refuses a stale plan for a retiring/former imported member."""
    if conn.execute('SELECT 1 FROM hosted_room_history_imports WHERE room_id=?', (room_id,)).fetchone() is None:
        return
    row = conn.execute('SELECT members_json FROM hosted_rooms WHERE room_id=?', (room_id,)).fetchone()
    for member in json.loads(row['members_json']):
        if member.get('membership', {}).get('state') in {'retiring', 'former'} and _targets(payload, member):
            raise error('imported member is retiring or former')


def _require_no_work(conn, room_id, member):
    if not table_exists(conn, 'hosted_room_driver_tasks'):
        return
    rows = conn.execute("SELECT payload_json FROM hosted_room_driver_tasks WHERE room_id=? "
                        "AND status IN ('queued','running','stopping','indeterminate','deferred')", (room_id,))
    if any(_targets(json.loads(row['payload_json']), member) for row in rows):
        raise RuntimeStoreError('member_work_active')


def _identity(member):
    value = {k: v for k, v in member.items() if k not in {'membership', 'availability', 'target'}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _load(conn, room_id, member_id):
    row = rooms._room_row(conn, rooms._SELECT_ROOM_WITH_BYTES, (room_id,), room_id)
    if row['disbanded_at'] is not None:
        raise RuntimeStoreError('room_unavailable')
    if conn.execute('SELECT 1 FROM hosted_room_history_imports WHERE room_id=?', (room_id,)).fetchone() is None:
        raise RuntimeStoreError('invalid_params')
    members = json.loads(row['members_json'])
    member = next((m for m in members if m['member_id'] == member_id), None)
    if member is None:
        raise RuntimeStoreError('invalid_params')
    record = conn.execute('SELECT * FROM hosted_room_links WHERE room_id=? AND member_id=?',
                          (room_id, member_id)).fetchone()
    link = links.StoredRoomLink.from_record(record) if record is not None else None
    if link and not callable(getattr(links, 'route_security_digest', None)):
        raise RuntimeStoreError('peer_setup_unavailable')
    digest = links.route_security_digest(link.as_record()) if link else None
    return row, members, member, link, digest


def begin(db_path, *, room_id, member_id, authorize_write):
    """Reserve or resume the same durable non-executing member operation."""
    room_id = rooms._room_id(room_id)
    member_id = rooms._actor_id(member_id, 'member_id')
    with rooms._transaction(db_path, immediate=True) as conn:
        authorize_write(conn)
        row, members, member, link, digest = _load(conn, room_id, member_id)
        _require_no_work(conn, room_id, member)
        membership = member.get('membership', {})
        if membership.get('state') == 'former':
            return {'result': {'room': rooms._room_from_row(row), 'member': member,
                               'action': 'retire', 'changed': False}}
        expected = {'route_digest': digest, 'authority_gateway_id': row['authority_gateway_id'],
                    'authority_epoch': row['authority_epoch'], 'identity_digest': _identity(member)}
        if membership.get('state') == 'retiring':
            if any(membership.get(k) != v for k, v in expected.items()):
                raise RuntimeStoreError('peer_setup_conflict')
            fence = dict(membership)
        else:
            fence = {'state': 'retiring', 'operation_id': uuid.uuid4().hex, **expected}
            member['membership'] = fence
            member.pop('target', None)
            member['availability'] = {'state': 'authorization_required', 'reason': 'member_retirement_pending'}
            conn.execute('UPDATE hosted_rooms SET members_json=?,revision=revision+1,updated_at=? WHERE room_id=?',
                         (rooms._canonical_json(members, label='members', max_bytes=rooms.MAX_MEMBERS_JSON_BYTES),
                          time.time(), room_id))
        return {'fence': fence, 'link': link}


def finish(db_path, *, room_id, member_id, snapshot, local_profiles, authorize_write):
    """Publish removal only if the exact owner/member/route survived the revoke."""
    with rooms._transaction(db_path, immediate=True) as conn:
        authorize_write(conn)
        row, _, member, link, digest = _load(conn, room_id, member_id)
        fence = snapshot['fence']
        if (member.get('membership') != fence or digest != fence['route_digest']
                or row['authority_gateway_id'] != fence['authority_gateway_id']
                or row['authority_epoch'] != fence['authority_epoch']
                or _identity(member) != fence['identity_digest']):
            raise RuntimeStoreError('peer_setup_conflict')
        _require_no_work(conn, room_id, member)
        row, member, changed = rooms._refresh_imported_members_locked(
            conn, row, local_profiles=local_profiles, now=time.time(), member_id=member_id, action='retire',
            retired_peer_grant_sha256=hashlib.sha256(link.grant.encode()).hexdigest() if link else None)
        return {'room': rooms._room_from_row(row), 'member': member, 'action': 'retire', 'changed': changed}
