"""Owner-issued, room-bound messaging delegation on canonical authorities."""
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

from gateway import hosted_room_controls as controls
from gateway.hosted_room_route_schema import require_room_work_open
from gateway.session_contract import Principal
from hermes_constants import hermes_home_key
from hermes_state_runtime import RuntimeStoreError, _epoch


_OWNER_METHODS = {'issue': 'session:control', 'revoke': 'session:control'}
_OWNER_FIELDS = {
    'issue': {'room_id', 'member_id', 'request_id', 'expires_at', 'reuse_existing'},
    'revoke': {'room_id', 'member_id'},
}
_DELEGATED = {method: 'session:read' for method in (
    'groups.state', 'groups.log', 'groups.attachment.list', 'groups.attachment.download')}


def _service(authority):
    service = getattr(authority, 'hosted_room_service', None)
    if service is None or Path(service.db_path).resolve() != Path(authority.db.db_path).resolve():
        raise RuntimeStoreError('runtime_coordination_required')
    if Path(authority.db.db_path).resolve().parent != Path(authority.profile_id).resolve():
        raise RuntimeStoreError('profile_mismatch')
    return service


def _schema(conn):
    if not controls._schema_is_current(conn):
        controls._migrate_schema(conn)
        controls._initialize_schema(conn)
        if not controls._schema_is_current(conn):
            raise RuntimeStoreError('storage_unavailable')


def _owner(authority, conn, room_id, expected=None):
    from gateway.session_hosted_service import _OWNER
    _epoch(conn, authority.epoch)
    row = conn.execute('SELECT value FROM state_meta WHERE key=?', (_OWNER + room_id,)).fetchone()
    if row is None or not row[0] or (expected is not None and row[0] != expected):
        raise RuntimeStoreError('permission_denied')
    return str(row[0])


def _prefix(authority, subject):
    # A profile is part of the credential realm even when room IDs and the
    # installation signing secret happen to be shared with another profile.
    material = json.dumps([hermes_home_key(authority.profile_id), subject], separators=(',', ':')).encode()
    return 'canonical-owner-v1:' + hashlib.sha256(material).hexdigest() + ':'


def _record(conn, room_id, member_id, gateway, epoch):
    return conn.execute('SELECT request_id FROM hosted_room_control_tokens '
        'WHERE room_id=? AND member_id=? AND authority_gateway_id=? AND authority_epoch=?',
        (room_id, member_id, gateway, epoch)).fetchone()


def dispatch_owner_delegation(authority, actor, method, params, *, peer_install_id=None):
    if (method not in _OWNER_METHODS or not isinstance(params, dict)
            or set(params) - _OWNER_FIELDS[method]):
        raise RuntimeStoreError('invalid_params')
    if actor.profile_id != authority.profile_id or 'session:control' not in actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    service = _service(authority)
    room_id = controls._identifier(params.get('room_id'), label='room_id')
    member_id = controls._identifier(params.get('member_id'), label='member_id')
    service.authorize_room(actor.subject, room_id)
    gateway, epoch = service._owned_authority(room_id)
    if method == 'issue':
        request = controls._identifier(params.get('request_id'), label='request_id')
        reuse = params.get('reuse_existing', False)
        if type(reuse) is not bool:
            raise RuntimeStoreError('invalid_params')
        expiry = params.get('expires_at', controls.ROOM_LIFETIME_EXPIRES_AT)
        request_id = _prefix(authority, actor.subject) + hashlib.sha256(request.encode()).hexdigest()

    def write(conn):
        _owner(authority, conn, room_id, actor.subject)
        _schema(conn)
        if method == 'revoke':
            count = controls.revoke_home_control_tokens(authority.db.db_path,
                room_id=room_id, member_id=member_id, authority_gateway_id=gateway,
                authority_epoch=epoch, _conn=conn)
            return {'revoked': count}
        require_room_work_open(conn, room_id, error=controls.HostedRoomControlError)
        if peer_install_id is not None:
            row = conn.execute('SELECT members_json FROM hosted_rooms WHERE room_id=?', (room_id,)).fetchone()
            members = json.loads(row['members_json']) if row else []
            member = next((m for m in members if m.get('member_id') == member_id), {})
            target = member.get('target') or {}
            if target.get('kind') != 'peer' or target.get('installation_id') != peer_install_id:
                raise RuntimeStoreError('room_control_participant_mismatch')
        existing = _record(conn, room_id, member_id, gateway, epoch)
        if reuse and existing is not None and not str(existing['request_id']).startswith(_prefix(authority, actor.subject)):
            raise RuntimeStoreError('control_reauthorization_required')
        issued = controls.issue_home_control_token(authority.db.db_path, room_id=room_id,
            member_id=member_id, authority_gateway_id=gateway, authority_epoch=epoch,
            expires_at=expiry, request_id=request_id, reuse_existing=reuse, _conn=conn)
        return {**issued.as_status(), 'control_token': issued.control_token}
    return authority.db._execute_write(write)


def _delegate_actor(authority, *, room_id, member_id, token, capability):
    service = _service(authority)
    gateway, epoch = service._owned_authority(room_id)
    with authority.db._read_ctx() as conn:
        subject = _owner(authority, conn, room_id)
        if not controls._schema_is_current(conn):
            raise RuntimeStoreError('permission_denied')
        row = _record(conn, room_id, member_id, gateway, epoch)
        prefix = _prefix(authority, subject)
        request = str(row['request_id']) if row else ''
        issuer_matches = hmac.compare_digest(request[:len(prefix)], prefix)
        valid = controls.verify_home_control_token(authority.db.db_path, room_id=room_id,
            member_id=member_id, authority_gateway_id=gateway, authority_epoch=epoch,
            control_token=token, _conn=conn)
        if not issuer_matches or not valid:
            raise RuntimeStoreError('permission_denied')
    return Principal(subject, authority.profile_id, frozenset({capability}), 'room-control:' + member_id)


async def dispatch_delegated_group_control(authority, *, room_id, member_id, token, method, params):
    """Translate a valid room capability, never a messaging-admin assertion.

    The caller supplies the authenticated endpoint's room/member coordinates.
    This first port exposes observation only. Mutations must retain their grant
    scope through the eventual write, not just authorize before queueing work.
    """
    from gateway.session_group_controls import dispatch_group_control
    if method not in _DELEGATED or not isinstance(params, dict) or params.get('room_id') != room_id:
        raise RuntimeStoreError('permission_denied')
    room_id = controls._identifier(room_id, label='room_id')
    member_id = controls._identifier(member_id, label='member_id')
    actor = _delegate_actor(authority, room_id=room_id, member_id=member_id,
                            token=token, capability=_DELEGATED[method])
    result = await dispatch_group_control(SimpleNamespace(authority=authority, actor=actor), method, params)
    # A read crossing revocation or an authority change must not release its data.
    _delegate_actor(authority, room_id=room_id, member_id=member_id,
                    token=token, capability=_DELEGATED[method])
    return result
