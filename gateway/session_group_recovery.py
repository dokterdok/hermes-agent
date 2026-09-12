"""Native-owner inert recovery views and exact existing-context custody preparation.

No recovery decision, fence, authority claim, attachment token or executable
route is created here. The accepted tail always remains unverified.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from gateway.hosted_room_recovery_read import literal_id, readonly
from gateway.session_group_delegation import _owner
from hermes_state_runtime import RuntimeStoreError, _epoch, _admission

RECOVERY_METHODS = {'groups.recovery.prepare': 'session:read',
                    'groups.custody.status': 'session:read', 'groups.custody.prepare': 'session:control'}
RECOVERY_FIELDS = {'groups.recovery.prepare': {'room_id'},
    'groups.custody.status': {'room_id', 'member_id'},
    'groups.custody.prepare': {'room_id', 'member_id', 'admission_id'}}


def _native_owner(connection, capability):
    authority, actor = connection.authority, connection.actor
    home = Path(authority.profile_id)
    if (getattr(connection, 'native_owner', False) is not True or capability not in actor.capabilities
            or actor.profile_id != authority.profile_id
            or getattr(authority, '_native_legacy_transports', {}).get(actor.transport_id) != actor):
        raise RuntimeStoreError('permission_denied')
    from gateway.session_authorities import served_profile_name
    if (not home.is_absolute() or home != home.resolve() or home.parent.name == 'profiles'
            or served_profile_name(home) != 'default' or Path(authority.db.db_path).resolve().parent != home):
        raise RuntimeStoreError('profile_mismatch')
    with authority.db._read_ctx() as conn:
        _epoch(conn, authority.epoch)
    from hermes_cli.install_identity import read_install_id
    identity = read_install_id()
    if not identity:
        raise RuntimeStoreError('installation_identity_unavailable')
    return 'install:' + identity


def _creation(conn, authority, actor, session_id, room_id, member_id):
    from hermes_state_local import POLICY_PREFIX
    from hermes_state_local_lineage import validate_local_lineage
    from gateway.hosted_room_local_custody import metadata_chain
    raw = conn.execute('SELECT value FROM state_meta WHERE key=?', (POLICY_PREFIX + session_id,)).fetchone()
    if raw is None:
        raise RuntimeStoreError('original_custody_unavailable')
    saved = json.loads(raw[0])
    creation_id = 'hosted:' + hashlib.sha256(json.dumps([room_id, member_id, 'default'], separators=(',', ':')).encode()).hexdigest()
    if (saved['profile_id'] != authority.profile_id or saved['principal_id'] != actor.subject
            or saved['session_id'] != session_id or saved['request_id'] != creation_id
            or saved['policy']['source'] != 'bot_room' or 'legacy_session_id' in saved):
        raise RuntimeStoreError('original_custody_unavailable')
    tip = validate_local_lineage(conn, saved)
    chain = metadata_chain(conn, session_id)
    if tip != chain[-1][0]:
        raise RuntimeStoreError('original_custody_unavailable')
    return chain


def _prepare(connection, params, gateway):
    from gateway import hosted_room_local_custody as custody
    authority, actor = connection.authority, connection.actor
    room_id, member_id = params['room_id'], params['member_id']
    def write(conn):
        _owner(authority, conn, room_id, actor.subject)
        if getattr(authority, '_native_legacy_transports', {}).get(actor.transport_id) != actor:
            raise RuntimeStoreError('permission_denied')
        room = conn.execute('SELECT * FROM hosted_rooms WHERE room_id=?', (room_id,)).fetchone()
        if room is None or room['authority_gateway_id'] != gateway or room['disbanded_at'] is not None:
            raise RuntimeStoreError('original_custody_unavailable')
        from gateway.hosted_room_authority_history import read_history_locked
        history = read_history_locked(conn, room_id, gateway_id=gateway, epoch=room['authority_epoch'])
        if ((history is None and room['authority_epoch'] > 1)
                or (history and history[0]['gateway_id'] != gateway)):
            raise RuntimeStoreError('original_custody_unavailable')
        locals_ = [m for m in json.loads(room['members_json']) if m['profile'] == 'default'
                   and (m.get('target') or {}).get('kind', 'local') == 'local']
        if len(locals_) != 1 or locals_[0]['member_id'] != member_id:
            raise RuntimeStoreError('original_custody_unavailable')
        row = _admission(conn, params['admission_id'])
        if row['principal_id'] != actor.subject or not row['request_id'].startswith('hosted:'):
            raise RuntimeStoreError('permission_denied')
        task, generation = json.loads(row['request_id'][7:])
        if (set(task) != {'room_id', 'task_id', 'thread_id', 'turn_id'} or task['room_id'] != room_id
                or type(generation) is not int or generation < 1):
            raise RuntimeStoreError('original_custody_unavailable')
        stored = conn.execute('SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?',
                              (room_id, task['task_id'])).fetchone()
        payload = json.loads(stored['payload_json']) if stored is not None else {}
        if (stored is None or stored['thread_id'] != task['thread_id'] or stored['turn_id'] != task['turn_id']
                or stored['execution_generation'] < generation or payload.get('target_profile') != 'default'
                or payload.get('target_member_id', 'default') != member_id
                or json.loads(row['payload_json']).get('text') != payload.get('prompt')):
            raise RuntimeStoreError('original_custody_unavailable')
        sid = row['target_session_id']
        chain = _creation(conn, authority, actor, sid, room_id, member_id)
        custody.initialize(conn)
        prior = conn.execute(f'SELECT * FROM {custody.TABLE} WHERE room_id=? AND member_id=?',
                             (room_id, member_id)).fetchone()
        if prior is None:
            # The first retained proof anchors custody, not completeness of earlier work.
            data = dict(room_id=room_id, member_id=member_id, profile='default', gateway_id=gateway,
                session_id=sid, session_started_at=chain[0][1], first_task_id=task['task_id'],
                first_execution_generation=generation, created_at=time.time())
        else:
            data = {k: prior[k] for k in prior.keys() if k != 'chain_json' and not k.startswith('last_')}
            if (data['profile'], data['gateway_id'], data['session_id'], data['session_started_at']) != (
                    'default', gateway, sid, chain[0][1]):
                raise RuntimeStoreError('original_custody_unavailable')
        data.update(last_session_id=chain[-1][0], last_session_started_at=chain[-1][1])
        custody.save(conn, data, chain)
        return prior is not None
    return authority.db._execute_write(write)


def _status(authority, params, gateway):
    from gateway import hosted_room_local_custody as custody, hosted_room_custody_schema as schema
    from gateway.hosted_rooms_common import table_exists
    view = {'object': 'hermes.original_local.custody', 'room_id': params['room_id'],
            'member_id': params['member_id'], 'home_install_id': gateway, 'profile': 'default',
            'accepted_tail': 'unverified', 'execution_authorized': False, 'old_admission_fenced': False}
    with readonly(authority.db.db_path) as conn:
        _epoch(conn, authority.epoch)
        if not table_exists(conn, custody.TABLE) and not table_exists(conn, schema.MARKER):
            return {**view, 'status': 'not_recorded'}
        conn.row_factory = sqlite3.Row
        verified = custody.verify_locked(conn, room_id=params['room_id'], member_id=params['member_id'],
                                          gateway_id=gateway, profile='default', missing_ok=True)
        if verified is None:
            return {**view, 'status': 'not_recorded'}
        row, digest = verified
    return {**view, 'status': 'verified', 'custody_sha256': digest,
            'session_id': row['session_id'], 'last_session_id': row['last_session_id']}


def dispatch_recovery(connection, method, params):
    if method not in RECOVERY_METHODS or not isinstance(params, dict) or set(params) != RECOVERY_FIELDS[method]:
        raise RuntimeStoreError('invalid_params')
    try:
        for value in params.values():
            literal_id(value)
    except ValueError as exc:
        raise RuntimeStoreError('invalid_params') from exc
    capability = RECOVERY_METHODS[method]
    gateway = _native_owner(connection, capability)
    try:
        if method == 'groups.recovery.prepare':
            from gateway.hosted_room_manual_recovery import prepare_recovery
            from gateway.hosted_rooms import default_db_path
            result = prepare_recovery(default_db_path(), room_id=params['room_id'], target_gateway_id=gateway)
        else:
            idempotent = _prepare(connection, params, gateway) if method == 'groups.custody.prepare' else None
            result = _status(connection.authority, params, gateway)
            if idempotent is not None:
                result['idempotent'] = idempotent
    except RuntimeStoreError:
        raise
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        raise RuntimeStoreError('recovery_evidence_unavailable') from exc
    if _native_owner(connection, capability) != gateway:
        raise RuntimeStoreError('profile_mismatch')
    return result
