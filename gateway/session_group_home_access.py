"""Explicit per-room consent for this profile's authorized messaging Home.

This is separate from peer-member bearer grants: the owner's Home need not be
a Bot participant. Sender/admin/audience checks remain the messaging handler's
responsibility. This module never treats admin status as native ownership.
"""
import hashlib
import json

from gateway import hosted_room_controls as controls
from gateway.session_group_delegation import _owner, _service
from hermes_constants import hermes_home_key
from hermes_state_runtime import RuntimeStoreError

HOME_ACCESS_METHODS = {'groups.control.home.get': 'session:read', 'groups.control.home.set': 'session:control'}
HOME_ACCESS_FIELDS = {'groups.control.home.get': {'room_id'}, 'groups.control.home.set': {'room_id', 'enabled'}}
_HOME = 'gateway.hosted.home.consent.v1:'


def _issuer(authority, subject, room_id, gateway, epoch):
    data = [hermes_home_key(authority.profile_id), subject, room_id, gateway, epoch]
    return hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()


def home_access_granted(authority, room_id):
    from gateway.hosted_rooms import local_authority_gateway_id
    from gateway.session_hosted_service import _OWNER
    from hermes_state_runtime import _epoch
    _service(authority)
    with authority.db._read_ctx() as conn:
        _epoch(conn, authority.epoch)
        row = conn.execute(
            'SELECT owner.value AS subject, consent.value AS consent, r.authority_gateway_id AS gateway, '
            'r.authority_epoch AS epoch FROM hosted_rooms r '
            'JOIN state_meta owner ON owner.key=? JOIN state_meta consent ON consent.key=? '
            'WHERE r.room_id=? AND r.disbanded_at IS NULL',
            (_OWNER + room_id, _HOME + room_id, room_id)).fetchone()
        if row is None or row['gateway'] != local_authority_gateway_id():
            return False
        try:
            value = json.loads(row['consent'])
        except (ValueError, TypeError):
            return False
        _epoch(conn, authority.epoch)
    return isinstance(value, dict) and value == {
        'enabled': True, 'issuer': _issuer(authority, row['subject'], room_id, row['gateway'], row['epoch'])}


def dispatch_home_access(authority, actor, method, params):
    if (method not in HOME_ACCESS_METHODS or not isinstance(params, dict)
            or set(params) != HOME_ACCESS_FIELDS[method]):
        raise RuntimeStoreError('invalid_params')
    if actor.profile_id != authority.profile_id or HOME_ACCESS_METHODS[method] not in actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    service = _service(authority)
    room_id = controls._identifier(params['room_id'], label='room_id')
    service.authorize_room(actor.subject, room_id)
    if method == 'groups.control.home.get':
        return {'room_id': room_id, 'enabled': home_access_granted(authority, room_id)}
    if type(params['enabled']) is not bool:
        raise RuntimeStoreError('invalid_params')
    gateway, epoch = service._owned_authority(room_id)
    def write(conn):
        _owner(authority, conn, room_id, actor.subject)
        if not controls._active_room_scope(conn, room_id=room_id, authority_gateway_id=gateway,
                                            authority_epoch=epoch):
            raise RuntimeStoreError('stale_generation')
        value = json.dumps({'enabled': params['enabled'],
                            'issuer': _issuer(authority, actor.subject, room_id, gateway, epoch)},
                           separators=(',', ':'))
        conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (_HOME + room_id, value))
        return {'room_id': room_id, 'enabled': params['enabled']}
    return authority.db._execute_write(write)
