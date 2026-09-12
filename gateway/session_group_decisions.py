"""Admit owner decisions before their exact canonical approval effect."""
import hashlib
import json
import time

from gateway import hosted_room_controls as controls
from gateway.session_group_messaging_send import _capture
from hermes_state_runtime import RuntimeStoreError

_PREFIX = 'gateway.hosted.decision.v1:'
_FIELDS = {'member_id', 'task_id', 'execution_generation', 'request_id', 'choice'}


def validate_pending_decision(item, room):
    fields = (_FIELDS - {'choice'}) | {'room_id', 'authority_gateway_id', 'authority_epoch', 'command', 'description', 'choices'}
    if (not isinstance(item, dict) or set(item) != fields
            or any(item.get(key) != room.get(key) for key in ('room_id', 'authority_gateway_id', 'authority_epoch'))
            or type(item['authority_epoch']) is not int or item['choices'] != ['once', 'deny']
            or any(not isinstance(item[key], str) or len(item[key]) > 512 for key in ('command', 'description'))):
        raise RuntimeStoreError('invalid_params')
    _decision({key: item[key] for key in _FIELDS - {'choice'}} | {'choice': 'once'})
    return dict(item)


def _decision(params):
    if not isinstance(params, dict) or set(params) != _FIELDS or params['choice'] not in {'once', 'deny'}:
        raise RuntimeStoreError('invalid_params')
    if type(params['execution_generation']) is not int or params['execution_generation'] < 1:
        raise RuntimeStoreError('invalid_params')
    return {**params, **{key: controls._identifier(params[key], label=key)
                        for key in ('member_id', 'task_id', 'request_id')}}


def _accept(proof, command_id, params):
    controls._identifier(command_id, label='command_id')
    scope = {'room_id': proof.room_id, 'owner': proof.owner, 'gateway_id': proof.gateway_id,
             'epoch': proof.room_epoch, 'member': proof.member_id or 'home', 'params': params}
    key = _PREFIX + hashlib.sha256(json.dumps([scope['member'], proof.room_id, command_id]).encode()).hexdigest()
    encoded = json.dumps(scope, sort_keys=True, separators=(',', ':'))
    def write(conn):
        proof.check(conn)
        row = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()
        if row is not None:
            saved = json.loads(row[0])
            if saved.get('scope') != encoded:
                raise RuntimeStoreError('admission_conflict')
            return saved
        task = conn.execute('SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?',
                            (proof.room_id, params['task_id'])).fetchone()
        payload = json.loads(task['payload_json']) if task else {}
        if (task is None or task['execution_generation'] != params['execution_generation']
                or task['status'] not in {'running', 'stopping'}
                or payload.get('target_member_id', payload.get('target_profile')) != params['member_id']):
            raise RuntimeStoreError('stale_generation')
        if not controls._active_room_scope(conn, room_id=proof.room_id, authority_gateway_id=proof.gateway_id,
                                           authority_epoch=proof.room_epoch, member_id=params['member_id']):
            raise RuntimeStoreError('permission_denied')
        cutoff = time.time() - 7 * 86400
        conn.execute("DELETE FROM state_meta WHERE key LIKE ? AND json_extract(value, '$.updated_at') < ?",
                     (_PREFIX + '%', cutoff))
        if conn.execute('SELECT COUNT(*) FROM state_meta WHERE key LIKE ?', (_PREFIX + '%',)).fetchone()[0] >= 4096:
            raise RuntimeStoreError('storage_unavailable')
        saved = {'scope': encoded, 'updated_at': time.time(), 'result': None}
        conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)', (key, json.dumps(saved)))
        return saved
    return key, proof.authority.db._execute_write(write)


def decide(authority, *, room, command_id, params, guard=None, member_id=None, token=None):
    """Consent is checked at durable acceptance; no await or network holds SQLite."""
    from gateway.session_group_delegation import _service
    params = _decision(params)
    proof = _capture(authority, room, guard=guard, member_id=member_id, token=token)
    key, saved = _accept(proof, command_id, params)
    if saved['result'] is not None:
        return saved['result']
    service = _service(authority)
    result = service.approve_room_task(proof.room_id, **params)
    if result.get('status') not in {'resolved', 'already_resolved'}:
        raise RuntimeStoreError('unknown_execution')
    def complete(conn):
        from hermes_state_runtime import _epoch
        _epoch(conn, proof.runtime_epoch)
        row = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()
        if row is None or json.loads(row[0]).get('scope') != saved['scope']:
            raise RuntimeStoreError('storage_unavailable')
        conn.execute('UPDATE state_meta SET value=? WHERE key=?',
                     (json.dumps({**saved, 'result': result, 'updated_at': time.time()}), key))
    authority.db._execute_write(complete)
    return result


def approve_task(service, room_id, *, member_id, task_id, execution_generation, request_id, choice):
    if choice not in {'once', 'deny'}:
        raise RuntimeStoreError('invalid_params')
    task, binding = service._control_task(room_id, member_id, task_id, execution_generation)
    if task['status'] not in {'running', 'stopping'}:
        raise RuntimeStoreError('stale_generation')
    if choice != 'deny':
        service._require_work_open(room_id)
    rpc = service._resolve_member_transport(binding, task)
    coords = {'profile': task['payload']['target_profile'], 'source': 'bot_room'}
    session = rpc.resolve_exact(**coords, title='Group: ' + room_id)
    if session is None:
        raise RuntimeStoreError('stale_generation')
    peer = service._member_is_peer(room_id, member_id)
    info = rpc.info(**coords, session_id=session['session_id'], **({'fresh': True} if peer else {}))
    prompt = info.get('pending_approval') or info.get('approval')
    if (info.get('task_id') != task_id or info.get('execution_generation') != execution_generation
            or not isinstance(prompt, dict) or prompt.get('request_id') != request_id):
        raise RuntimeStoreError('stale_generation')
    if peer:
        result = rpc.client.approve_receipt(task_id=task_id, execution_generation=execution_generation,
            request_id=request_id, choice=choice, grant=rpc.route.grant)
        if (not isinstance(result, dict) or result.get('run_id') != info.get('run_id')
                or result.get('prompt_id') != request_id):
            raise RuntimeStoreError('unknown_execution')
    else:
        result = rpc.approve(session_id=session['session_id'], request_id=request_id, choice=choice)
    if not isinstance(result, dict) or result.get('status') not in {'resolved', 'already_resolved'}:
        raise RuntimeStoreError('unknown_execution')
    with service._policy_lock:
        pending = service._pending_actions.get((room_id, member_id))
        if pending is not None and (pending.get('request_id'), pending.get('task_id'), pending.get('execution_generation')) == (
                request_id, task_id, execution_generation):
            service._set_pending_action(room_id, member_id, None)
    service.runtime.wakeup()
    return result


def pending_decisions(service, room_id):
    """Project only currently observed exact canonical requests, never a new queue."""
    gateway, epoch = service._owned_authority(room_id)
    result = []
    for action in service.status(room_id).get('pending_actions', []):
        if action.get('kind') != 'approval':
            continue
        try:
            task, binding = service._control_task(room_id, action['member_id'], action['task_id'], action['execution_generation'])
            rpc = service._resolve_member_transport(binding, task)
            coords = {'profile': task['payload']['target_profile'], 'source': 'bot_room'}
            session = rpc.resolve_exact(**coords, title='Group: ' + room_id)
            if session is None:
                continue
            peer = service._member_is_peer(room_id, action['member_id'])
            info = rpc.info(**coords, session_id=session['session_id'], **({'fresh': True} if peer else {}))
            prompt = info.get('pending_approval') or info.get('approval')
            if (not isinstance(prompt, dict) or prompt.get('request_id') != action.get('request_id')
                    or info.get('task_id') != action['task_id'] or info.get('execution_generation') != action['execution_generation']):
                continue
            result.append({key: action[key] for key in ('member_id', 'task_id', 'execution_generation', 'request_id')} | {
                'room_id': room_id, 'authority_gateway_id': gateway, 'authority_epoch': epoch,
                'command': str(prompt.get('command') or '')[:512],
                'description': str(prompt.get('description') or '')[:512], 'choices': ['once', 'deny']})
        except (KeyError, ValueError, RuntimeStoreError):
            continue
    if service._owned_authority(room_id) != (gateway, epoch):
        raise RuntimeStoreError('stale_generation')
    return result[:8]
