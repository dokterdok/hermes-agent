"""Canonical adaptation of #98073 owner-to-participant return-control setup."""
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from gateway import hosted_room_controls as controls
from gateway.hosted_room_control_client import RoomControlHTTPClient
from gateway.session_authorities import served_profile_name
from gateway.session_group_delegation import _schema, _service, dispatch_owner_delegation
from hermes_state_runtime import RuntimeStoreError, _epoch


CONTROL_SETUP_METHODS = {method: 'session:control' for method in (
    'groups.control.invite', 'groups.control.register', 'groups.control.revoke')}
CONTROL_SETUP_FIELDS = {
    'groups.control.invite': {'room_id', 'member_id', 'caller_install_id', 'request_id', 'reuse_existing'},
    'groups.control.register': {'room_id', 'member_id', 'home_url', 'authority_gateway_id',
                               'authority_epoch', 'room_name', 'member_count', 'control_token', 'expires_at'},
    'groups.control.revoke': {'room_id', 'member_id'},
}


def _home_url(authority):
    from gateway.hosted_room_peer import local_room_link_endpoint, validate_room_link_url
    endpoint = local_room_link_endpoint()
    if endpoint.get('available') is not True:
        raise RuntimeStoreError('room_control_endpoint_unavailable')
    profile = served_profile_name(authority.profile_id)
    parsed = urlsplit(endpoint['url'])
    parts = parsed.path.rstrip('/').split('/')
    if len(parts) >= 2 and parts[-2] == 'p':
        if unquote(parts[-1]) != profile:
            raise RuntimeStoreError('profile_mismatch')
        return endpoint['url']
    path = parsed.path.rstrip('/') + '/p/' + quote(profile, safe='')
    return validate_room_link_url(urlunsplit(parsed._replace(path=path)))[0]


def _invite(authority, actor, params):
    service = _service(authority)
    room_id = controls._identifier(params.get('room_id'), label='room_id')
    service.authorize_room(actor.subject, room_id)
    room = service._room(room_id)
    caller = controls._identifier(params.get('caller_install_id'), label='caller_install_id')
    home_url = _home_url(authority)
    issued = dispatch_owner_delegation(authority, actor, 'issue',
        {key: value for key, value in params.items() if key != 'caller_install_id'}, peer_install_id=caller)
    return {key: issued[key] for key in ('room_id', 'member_id', 'authority_gateway_id',
                                        'authority_epoch', 'control_token', 'expires_at')} | {
        'home_url': home_url, 'room_name': room['name'], 'member_count': len(room['members'])}


def _reservation(authority, params):
    from gateway.hosted_room_grant_state import grant_state_db_paths
    profile = served_profile_name(authority.profile_id)
    if not all(controls.peer_reservation_matches(path,
            room_id=params.get('room_id'), member_id=params.get('member_id'), target_profile=profile,
            authority_gateway_id=params.get('authority_gateway_id'), authority_epoch=params.get('authority_epoch'))
            for path in grant_state_db_paths(authority.profile_id)):
        raise RuntimeStoreError('room_control_reservation_required')


def _register(authority, actor, params):
    _reservation(authority, params)
    def write(conn):
        _epoch(conn, authority.epoch)
        _schema(conn)
        return controls.save_peer_control_link(authority.db.db_path, **params, allow_rotation=True, _conn=conn)
    saved = authority.db._execute_write(write)
    try:
        summary = RoomControlHTTPClient(saved.link).summary()
        room = summary.get('room') if isinstance(summary, dict) else None
        if (not isinstance(room, dict) or type(room.get('authority_epoch')) is not int or
                any(room.get(field) != getattr(saved.link, field)
                    for field in ('room_id', 'authority_gateway_id', 'authority_epoch'))):
            raise RuntimeStoreError('room_control_scope_changed')
        _reservation(authority, params)
        with authority.db._read_ctx() as conn:
            _epoch(conn, authority.epoch)
    except Exception:
        if not saved.idempotent:
            retired = controls.revoke_peer_control_link_value(authority.db.db_path, expected=saved.link)
            if retired is not None:
                controls.delete_peer_control_link_value(authority.db.db_path,
                    expected=retired, required_status='revoked')
        raise
    return {'registered': True, 'idempotent': saved.idempotent,
            'room_id': saved.link.room_id, 'member_id': saved.link.member_id}


def _revoke(authority, actor, params):
    room_id = controls._identifier(params.get('room_id'), label='room_id')
    member_id = controls._identifier(params.get('member_id'), label='member_id')
    with authority.db._read_ctx() as conn:
        _epoch(conn, authority.epoch)
    link = next((link for link in controls.load_peer_control_links(authority.db.db_path,
        include_inactive=True).links if link.room_id == room_id and link.member_id == member_id), None)
    if link is None:
        return {'revoked': 0}
    # Keep exact bearer material until remote revocation succeeds. Never erase a
    # replacement route registered while this HTTP request was outstanding.
    retired = controls.revoke_peer_control_link_value(authority.db.db_path, expected=link)
    if retired is None:
        raise RuntimeStoreError('room_control_scope_changed')
    RoomControlHTTPClient(retired).revoke()
    deleted = controls.delete_peer_control_link_value(authority.db.db_path,
        expected=retired, required_status='revoked')
    return {'revoked': int(deleted)}


def dispatch_control_setup(authority, actor, method, params):
    if (method not in CONTROL_SETUP_METHODS or not isinstance(params, dict)
            or set(params) - CONTROL_SETUP_FIELDS[method]):
        raise RuntimeStoreError('invalid_params')
    if actor.profile_id != authority.profile_id or 'session:control' not in actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    _service(authority)
    return {'groups.control.invite': _invite, 'groups.control.register': _register,
            'groups.control.revoke': _revoke}[method](authority, actor, params)
