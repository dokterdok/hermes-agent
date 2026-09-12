"""Native-owner passive enrollment setup from #104601, without execution grants."""
from pathlib import Path

from gateway import hosted_room_replica_retirement as retirement, hosted_rooms
from gateway.hosted_room_peer import gateway_room_grant_secret
from gateway.session_group_delegation import _owner, _service
from gateway.session_authorities import served_profile_name
from hermes_state_runtime import RuntimeStoreError, _epoch


REPLICATION_METHODS = {
    'groups.replication.prepare': 'session:control',
    'groups.replication.enroll': 'session:control',
    'groups.replication.revoke': 'session:control',
    'groups.replication.status': 'session:read',
}
REPLICATION_FIELDS = {
    'groups.replication.prepare': {'room_id', 'target_install_id', 'endpoint', 'enrollment_id',
                                   'replace_enrollment_id', 'expected_authority'},
    'groups.replication.enroll': {'enrollment', 'expected_enrollment_id', 'expected_state', 'authority_history'},
    'groups.replication.revoke': {'room_id', 'enrollment_id'},
    'groups.replication.status': {'room_id'},
}


def _source(authority, actor, params, *, prepare):
    from gateway.session_passive_replication import passive_source_supported
    if not passive_source_supported(authority):
        raise RuntimeStoreError('passive_source_requires_installation_owner')
    service = _service(authority)
    room_id = params.get('room_id')
    service.authorize_room(actor.subject, room_id)
    gateway, epoch = service._owned_authority(room_id)
    expected = params.get('expected_authority')
    if expected is not None and (not isinstance(expected, dict) or set(expected) != {'gateway_id', 'epoch'}
            or type(expected.get('epoch')) is not int or expected != {'gateway_id': gateway, 'epoch': epoch}):
        raise RuntimeStoreError('stale_generation')
    if not prepare:
        publisher = getattr(authority, 'passive_publisher', None)
        return {'room_id': room_id, 'authority': {'gateway_id': gateway, 'epoch': epoch},
                'publisher': publisher.status(room_id) if publisher else {'running': False, 'source_loss_safe': False},
                'enrollments': retirement.home_status(authority.db.db_path, room_id=room_id),
                'retirement_delivery_enabled': False}
    secret = gateway_room_grant_secret()
    def write(conn):
        _owner(authority, conn, room_id, actor.subject)
        room = conn.execute('SELECT authority_gateway_id,authority_epoch FROM hosted_rooms WHERE room_id=?',
                            (room_id,)).fetchone()
        if room is None or (room[0], room[1]) != (gateway, epoch):
            raise RuntimeStoreError('stale_generation')
        return retirement.prepare_home_enrollment(authority.db.db_path,
            room_id=room_id, target_install_id=params.get('target_install_id'), endpoint=params.get('endpoint'),
            local_gateway_id=gateway, secret=secret, enrollment_id=params.get('enrollment_id'),
            replace_enrollment_id=params.get('replace_enrollment_id'), _conn=conn)
    enrollment = authority.db._execute_write(write)
    return {'enrollment': enrollment,
            **retirement.home_enrollment_history(authority.db.db_path, enrollment_id=enrollment['enrollment_id'])}


def _target(authority, params, *, revoke):
    # Receiver enrollment belongs to the installation root, not a named Home
    # or a room-member bearer. Match the existing installation HTTP boundary.
    home = Path(authority.profile_id)
    if (not home.is_absolute() or home != home.resolve()
            or served_profile_name(home) != 'default' or home.parent.name == 'profiles'):
        raise RuntimeStoreError('installation_endpoint_required')
    db_path = hosted_rooms.default_db_path()
    local_id = hosted_rooms.local_authority_gateway_id()
    def write(conn):
        # Keep the root authority epoch fenced while updating its shared receiver
        # store; named authorities never open or acquire this root writer.
        _epoch(conn, authority.epoch)
        if revoke:
            return retirement.revoke_target_enrollment(db_path,
                room_id=params.get('room_id'), enrollment_id=params.get('enrollment_id'))
        return retirement.enroll_target(db_path, enrollment=params.get('enrollment'), target_install_id=local_id,
            expected_enrollment_id=params.get('expected_enrollment_id'),
            expected_state=params.get('expected_state', 'active'), authority_history=params.get('authority_history'))
    return authority.db._execute_write(write)


def dispatch_replication(authority, actor, method, params):
    if method not in REPLICATION_METHODS or not isinstance(params, dict) or set(params) - REPLICATION_FIELDS[method]:
        raise RuntimeStoreError('invalid_params')
    if actor.profile_id != authority.profile_id or REPLICATION_METHODS[method] not in actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    _service(authority)
    if method in {'groups.replication.prepare', 'groups.replication.status'}:
        return _source(authority, actor, params, prepare=method.endswith('.prepare'))
    return _target(authority, params, revoke=method.endswith('.revoke'))
