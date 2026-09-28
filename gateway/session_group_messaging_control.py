"""Operator-granted private Stop and approval, independently scoped from Send.

Only the Route delegated-control writer can admit these effects. The messaging
principal stays read-only; each callback rechecks persisted consent and lineage
on the writer supplied by Route, including the last pre-commit check.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import uuid

from hermes_state_runtime import RuntimeStoreError
from gateway.session_group_messaging_read import (
    _attest_room_read, _binding, _room_binding, _recipient, _json, _load,
    _room_service,
)
from gateway.session_group_messaging_send import _text, _stable_message_identity

CONTROL_METHODS = {
    'groups.messaging.room.stop.grant': 'session:operator',
    'groups.messaging.room.stop.revoke': 'session:operator',
    'groups.messaging.room.approval.grant': 'session:operator',
    'groups.messaging.room.approval.revoke': 'session:operator',
}
_COMMON = {'request_id', 'recipient', 'room_id', 'room_read_binding_id',
           'room_read_generation', 'expected_generation'}
CONTROL_FIELDS = {name: _COMMON | ({'binding_id'} if name.endswith('.revoke') else set())
                  for name in CONTROL_METHODS}
_PREFIX = 'gateway.messaging.control.v1.'
_MAX = 2**63 - 2


def _key(recipient, room_id, scope):
    digest = hashlib.sha256(_json([recipient, room_id, scope]).encode()).hexdigest()
    return _PREFIX + 'binding.' + digest


def _request_key(owner, request_id):
    return _PREFIX + 'request.' + hashlib.sha256(_json([owner, request_id]).encode()).hexdigest()

def _source_key(identity):
    return _PREFIX + 'source.' + hashlib.sha256(identity.encode()).hexdigest()

def _approval_selector(read, record, action):
    """Opaque identity for one displayed request, not an authorization token."""
    fields = ('member_id', 'task_id', 'request_id')
    if (type(action) is not dict
            or any(type(action.get(k)) is not str or not action[k] or len(action[k]) > 512
                   for k in fields)
            or type(action.get('execution_generation')) is not int
            or not 1 <= action['execution_generation'] <= _MAX):
        raise RuntimeStoreError('permission_denied')
    inventory = read.inventory
    recipient = inventory._require_context()
    coordinates = [recipient, inventory.profile_id,
                   json.loads(inventory.state_json)['binding_id'],
                   read.owner, inventory.service.runtime.process_generation,
                   read.room_id, read.room_ref, read.binding_id, read.generation,
                   record['binding_id'], record['generation'],
                   *(action[k] for k in fields), action['execution_generation']]
    return 'pa-' + hashlib.sha256(_json(coordinates).encode()).hexdigest()


def _scope(method):
    return 'stop' if '.stop.' in method else 'approval'


def _record(conn, recipient, profile_id, room_id, scope):
    record = _load(conn, _key(recipient, room_id, scope))
    if record is None:
        return None
    fields = {'version', 'recipient', 'profile_id', 'owner', 'room_id', 'scope',
              'inventory_binding_id', 'room_read_binding_id', 'room_read_generation',
              'room_ref', 'binding_id', 'generation', 'active'}
    if (type(record) is not dict or set(record) != fields or record['version'] != 1
            or record['recipient'] != recipient or record['profile_id'] != profile_id
            or record['room_id'] != room_id or record['scope'] != scope
            or type(record['owner']) is not str or not record['owner']
            or type(record['binding_id']) is not str
            or re.fullmatch(r'mrc-[0-9a-f]{32}', record['binding_id']) is None
            or type(record['generation']) is not int or not 1 <= record['generation'] <= _MAX
            or type(record['room_read_generation']) is not int
            or type(record['room_ref']) is not int or type(record['active']) is not bool):
        raise RuntimeStoreError('permission_denied')
    return record


def _lineage(conn, recipient, profile_id, room_id, owner, read_id, read_gen, room_ref=None):
    inventory = _binding(conn, recipient, profile_id)
    read = _room_binding(conn, recipient, profile_id, room_id)
    if (inventory is None or not inventory['active'] or inventory['owner'] != owner
            or read is None or not read['active'] or read['owner'] != owner
            or read['inventory_binding_id'] != inventory['binding_id']
            or read['binding_id'] != read_id or read['generation'] != read_gen
            or (room_ref is not None and read['room_ref'] != room_ref)):
        raise RuntimeStoreError('permission_denied')
    return inventory, read


@dataclass(frozen=True)
class _PreparedControlBinding:
    operation: object
    service: object
    checker: object
    intent: dict
    request_key: str


def prepare_native_control_binding(connection, method, params):
    from gateway.session_group_peers import _native_owner
    if method not in CONTROL_METHODS or set(params) != CONTROL_FIELDS[method]:
        raise RuntimeStoreError('invalid_params')
    operation = _native_owner(connection)
    scope = _scope(method)
    capability = 'session:control' if scope == 'stop' else 'session:approve'
    operation.require_current()
    if ('session:operator' not in operation.actor.capabilities
            or capability not in operation.actor.capabilities):
        raise RuntimeStoreError('permission_denied')
    recipient = _recipient(params['recipient'])
    owner = _text(operation.actor.subject, 1024)
    room_id = _text(params['room_id'], 128)
    request_id = _text(params['request_id'], 128)
    from gateway.session_group_messaging_read import _room_binding_id
    read_id = _room_binding_id(params['room_read_binding_id'])
    read_gen, expected = params['room_read_generation'], params['expected_generation']
    if (type(read_gen) is not int or not 1 <= read_gen <= _MAX
            or type(expected) is not int or not 0 <= expected < _MAX):
        raise RuntimeStoreError('invalid_params')
    binding_id = params.get('binding_id')
    if binding_id is not None and (type(binding_id) is not str
            or re.fullmatch(r'mrc-[0-9a-f]{32}', binding_id) is None):
        raise RuntimeStoreError('invalid_params')
    service, checker = _room_service(operation)
    with operation.db._read_ctx() as conn:
        from gateway.session_group_messaging_read import _epoch
        _epoch(conn, operation.epoch)
        row = conn.execute('SELECT instance_id FROM runtime_epoch WHERE singleton=1').fetchone()
        if row is None or row[0] != operation.instance_id:
            raise RuntimeStoreError('stale_epoch')
        _room_service(operation, service, checker)
        _lineage(conn, recipient, operation.profile_id, room_id, owner, read_id, read_gen)
        checker(owner, room_id, conn=conn)
    operation.require_current()
    return _PreparedControlBinding(operation, service, checker, {
        'method': method, 'scope': scope, 'recipient': recipient, 'owner': owner,
        'profile_id': operation.profile_id, 'room_id': room_id,
        'read_id': read_id, 'read_generation': read_gen,
        'expected_generation': expected, 'binding_id': binding_id,
    }, _request_key(owner, request_id))


def commit_native_control_binding(prepared):
    op, intent = prepared.operation, prepared.intent
    recipient, room_id, scope = intent['recipient'], intent['room_id'], intent['scope']
    capability = 'session:control' if scope == 'stop' else 'session:approve'
    key = _key(recipient, room_id, scope)

    def require(conn):
        op.require_current(conn)
        if ('session:operator' not in op.actor.capabilities
                or capability not in op.actor.capabilities):
            raise RuntimeStoreError('permission_denied')
        _room_service(op, prepared.service, prepared.checker)
        inventory, read = _lineage(conn, recipient, op.profile_id, room_id,
                                   intent['owner'], intent['read_id'], intent['read_generation'])
        prepared.checker(intent['owner'], room_id, conn=conn)
        return inventory, read

    def write(conn):
        inventory, read = require(conn)
        current = _record(conn, recipient, op.profile_id, room_id, scope)
        prior = _load(conn, prepared.request_key)
        if prior is not None:
            if (type(prior) is not dict or set(prior) != {'intent', 'state'}
                    or prior['intent'] != intent or current != prior['state']):
                raise RuntimeStoreError('admission_conflict')
            return {field: current[field] for field in ('binding_id', 'generation', 'active')}
        if current is not None and current['owner'] != intent['owner']:
            raise RuntimeStoreError('permission_denied')
        if (current['generation'] if current else 0) != intent['expected_generation']:
            raise RuntimeStoreError('messaging_room_control_stale')
        same_read = (current is not None
                     and current['inventory_binding_id'] == inventory['binding_id']
                     and current['room_read_binding_id'] == read['binding_id']
                     and current['room_read_generation'] == read['generation']
                     and current['room_ref'] == read['room_ref'])
        granting = intent['method'].endswith('.grant')
        if (granting and current is not None and current['active'] and same_read) or (
                not granting and (current is None or not current['active'] or not same_read
                                  or current['binding_id'] != intent['binding_id'])):
            raise RuntimeStoreError('messaging_room_control_stale')
        counts = {kind: conn.execute('SELECT COUNT(*) FROM state_meta WHERE key LIKE ?',
                    (_PREFIX + kind + '.%',)).fetchone()[0]
                  for kind in ('binding', 'request')}
        if ((current is None and counts['binding'] >= 8192)
                or counts['request'] >= 32768
                or (granting and counts['request'] + counts['binding'] + 2 > 32768)):
            raise RuntimeStoreError('messaging_room_control_capacity')
        record = dict(version=1, recipient=recipient, profile_id=op.profile_id,
                      owner=intent['owner'], room_id=room_id, scope=scope,
                      inventory_binding_id=inventory['binding_id'],
                      room_read_binding_id=read['binding_id'],
                      room_read_generation=read['generation'], room_ref=read['room_ref'],
                      binding_id='mrc-' + uuid.uuid4().hex if granting else current['binding_id'],
                      generation=intent['expected_generation'] + 1, active=granting)
        require(conn)
        conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, _json(record)))
        conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)',
                     (prepared.request_key, _json({'intent': intent, 'state': record})))
        require(conn)
        return {field: record[field] for field in ('binding_id', 'generation', 'active')}
    return op.db._execute_write(write)


@dataclass(frozen=True)
class _MessagingRoomControl:
    read: object
    scope: str
    record: dict
    source_identity: str
    source_text: str
    runtime: object
    runtime_generation: str
    params: dict

    @property
    def authority(self):
        return self.read.authority

    @property
    def actor(self):
        return self.read.actor

    @property
    def room_id(self):
        return self.read.room_id

    @property
    def room_ref(self):
        return self.read.room_ref

    def _source_intent(self):
        inventory = self.read.inventory
        return {'version': 1, 'identity': self.source_identity,
                'recipient': inventory.recipient_json, 'profile_id': inventory.profile_id,
                'room_id': self.room_id, 'scope': self.scope,
                'inventory_binding_id': json.loads(inventory.state_json)['binding_id'],
                'room_read_binding_id': self.read.binding_id,
                'room_read_generation': self.read.generation,
                'control_binding_id': self.record['binding_id'],
                'control_generation': self.record['generation'], 'params': self.params}

    def _source_admission(self, conn, *, reserve):
        key = _source_key(self.source_identity)
        row = _load(conn, key)
        intent = self._source_intent()
        if row is not None:
            if row != intent:
                raise RuntimeStoreError('admission_conflict')
        elif reserve:
            count = conn.execute('SELECT COUNT(*) FROM state_meta WHERE key LIKE ?',
                                 (_PREFIX + 'source.%',)).fetchone()[0]
            if count >= 32768:
                raise RuntimeStoreError('messaging_room_control_capacity')
            conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)', (key, _json(intent)))

    def _current(self, conn, *, admission):
        inventory = self.read.inventory
        recipient = inventory._require_context()
        identity, _ = _stable_message_identity(inventory.event, recipient)
        if identity != self.source_identity or inventory.event.text != self.source_text:
            raise RuntimeStoreError('permission_denied')
        if (inventory.service.runtime is not self.runtime
                or self.runtime.process_generation != self.runtime_generation):
            raise RuntimeStoreError('runtime_coordination_required')
        if admission:
            state = inventory._consent(conn, recipient)
            if _json(state) != inventory.state_json:
                raise RuntimeStoreError('permission_denied')
            _lineage(conn, recipient, inventory.profile_id, self.room_id,
                     self.read.owner, self.read.binding_id, self.read.generation,
                     self.room_ref)
        else:
            self.read._require_current_on_connection(conn, recipient, held_writer=False)
        record = _record(conn, recipient, inventory.profile_id, self.room_id, self.scope)
        if (record is None or record['owner'] != self.read.owner
                or record['inventory_binding_id'] != json.loads(inventory.state_json)['binding_id']
                or record['room_read_binding_id'] != self.read.binding_id
                or record['room_read_generation'] != self.read.generation
                or record['room_ref'] != self.room_ref
                or record['binding_id'] != self.record['binding_id']
                or record['generation'] != self.record['generation']
                or not record['active']):
            raise RuntimeStoreError('messaging_room_control_stale')
        inventory.checker(self.read.owner, self.room_id, conn=conn)
        inventory._require_context()

    def require_current(self, method=None, params=None):
        expected = 'groups.stop' if self.scope == 'stop' else 'groups.approve'
        if (type(self) is not _MessagingRoomControl or (method is not None and method != expected)
                or (params is not None and (type(params) is not dict or params != self.params))):
            raise RuntimeStoreError('permission_denied')
        with self.read.inventory.db._read_ctx() as conn:
            self._current(conn, admission=False)
            self._source_admission(conn, reserve=False)
        return True

    def delegated(self):
        from gateway.hosted_room_delegated_control import DelegatedControl
        self.require_current()
        inventory = self.read.inventory
        def check_new(conn):
            self._current(conn, admission=True)
            self._source_admission(conn, reserve=False)
        def check_commit(conn):
            self._current(conn, admission=True)
            self._source_admission(conn, reserve=True)
        return DelegatedControl(inventory.db, self.runtime, self.runtime_generation,
                                check_new, check_commit,
                                _json([inventory.recipient_json, self.scope, self.record['binding_id'],
                                       self.record['generation'], self.source_identity]))


def authorize_room_control(runner, event, room_ref, scope):
    if scope not in {'stop', 'approval'}:
        raise RuntimeStoreError('invalid_params')
    read = _attest_room_read(runner, event, room_ref)
    inventory = read.inventory
    recipient = inventory._require_context()
    if type(inventory.state_json) is not str:
        raise RuntimeStoreError('permission_denied')
    with inventory.db._read_ctx() as conn:
        read._require_current_on_connection(conn, recipient, held_writer=False)
        record = _record(conn, recipient, inventory.profile_id, read.room_id, scope)
        if (record is None or not record['active'] or record['owner'] != read.owner
                or record['inventory_binding_id'] != json.loads(inventory.state_json)['binding_id']
                or record['room_read_binding_id'] != read.binding_id
                or record['room_read_generation'] != read.generation
                or record['room_ref'] != read.room_ref):
            raise RuntimeStoreError('permission_denied')
    return read


def pending_room_approvals(runner, event, room_ref):
    """Consent-gated in-memory selector; Route still admits the exact task in its writer."""
    read = authorize_room_control(runner, event, room_ref, 'approval')
    inventory = read.inventory
    recipient = inventory._require_context()
    with inventory.db._read_ctx() as conn:
        read._require_current_on_connection(conn, recipient, held_writer=False)
        record = _record(conn, recipient, inventory.profile_id, read.room_id, 'approval')
        if record is None or not record['active']:
            raise RuntimeStoreError('permission_denied')
    service = read.inventory.service
    with service._policy_lock:
        read.require_current(method='groups.state', room_id=read.room_id)
        if not service.runtime.status().get('running'):
            raise RuntimeStoreError('runtime_coordination_required')
        actions = []
        for (room_id, member_id), action in tuple(service._pending_actions.items()):
            if room_id != read.room_id or type(action) is not dict or action.get('kind') != 'approval':
                continue
            selected = dict(action, member_id=member_id)
            selected['selector'] = _approval_selector(read, record, selected)
            actions.append(selected)
            if len(actions) > 12:
                raise RuntimeStoreError('permission_denied')
        read.require_current(method='groups.state', room_id=read.room_id)
    return read, actions


def attest_room_control(runner, event, room_ref, scope, params):
    if scope not in {'stop', 'approval'} or type(params) is not dict:
        raise RuntimeStoreError('invalid_params')
    read = authorize_room_control(runner, event, room_ref, scope)
    if params.get('room_id') != read.room_id:
        raise RuntimeStoreError('permission_denied')
    if scope == 'stop':
        if (set(params) != {'room_id', 'cancel_id'}
                or type(params['cancel_id']) is not str
                or re.fullmatch(r'messaging-stop:[0-9a-f]{64}', params['cancel_id']) is None):
            raise RuntimeStoreError('invalid_params')
    elif (set(params) != {'room_id', 'member_id', 'task_id', 'execution_generation',
                          'request_id', 'choice'}
          or params.get('choice') not in {'once', 'deny'}
          or type(params.get('execution_generation')) is not int
          or params['execution_generation'] < 1
          or any(type(params.get(k)) is not str or not params[k]
                 for k in ('member_id', 'task_id', 'request_id'))):
        raise RuntimeStoreError('invalid_params')
    inventory = read.inventory
    recipient = inventory._require_context()
    identity, digest = _stable_message_identity(event, recipient)
    if scope == 'stop' and params['cancel_id'] != 'messaging-stop:' + digest:
        raise RuntimeStoreError('permission_denied')
    with inventory.db._read_ctx() as conn:
        read._require_current_on_connection(conn, recipient, held_writer=False)
        record = _record(conn, recipient, inventory.profile_id, read.room_id, scope)
        if record is None or not record['active']:
            raise RuntimeStoreError('permission_denied')
    context = _MessagingRoomControl(read, scope, record, identity, event.text,
                                    inventory.service.runtime,
                                    inventory.service.runtime.process_generation, params)
    context.require_current()
    return context
