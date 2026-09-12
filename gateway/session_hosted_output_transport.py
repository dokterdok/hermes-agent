"""Root custody for explicit named-profile output over the private owner API.

Uses the #99159 descriptor snapshot/outbox contract. Bytes are borrowed from an
active producer, never from a caller-supplied foreign filename or database.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading

from gateway.hosted_room_artifacts import (
    MAX_ATTACHMENT_BYTES, RoomArtifactError, RoomArtifactOutbox, RoomArtifactScope,
    terminal_artifact_manifest,
)
from hermes_state_runtime import RuntimeStoreError, _epoch

OUTPUT_OPERATIONS = frozenset({'output-scope', 'output-put', 'output-manifest'})


def root_named_route(source, target):
    source, target = Path(source), Path(target)
    return (source.is_absolute() and target.is_absolute() and source == source.resolve()
            and target == target.resolve() and source.parent.name != 'profiles'
            and target.parent.name == 'profiles' and target.parent.parent == source)


class NamedOutputBinding:
    # Reuse the authored bounded descriptor reader verbatim, with remote put_bytes.
    put_open_file = RoomArtifactOutbox.put_open_file

    def __init__(self, authority, ref, row, transport, scope, cancel_generation):
        self.authority, self.ref, self.row = authority, ref, dict(row)
        self.transport = transport
        self.scope, self.cancel_generation = scope, cancel_generation
        self.owner_pid, self.active, self.used = os.getpid(), True, False
        self.token = secrets.token_hex(32)
        self.snapshots = {}
        self.lock = threading.RLock()

    def check_live(self):
        from gateway.session_hosted_transport import _BINDING, _principal
        if not self.active or os.getpid() != self.owner_pid:
            raise RoomArtifactError('Group Chat output producer is no longer active')
        with self.authority.db._read_ctx() as conn:
            _epoch(conn, self.authority.epoch)
            row = conn.execute('SELECT * FROM session_admissions WHERE admission_id=?',
                               (self.row['admission_id'],)).fetchone()
            retained = conn.execute('SELECT value FROM state_meta WHERE key=?',
                                    (_BINDING + self.ref.session_id,)).fetchone()
        if (row is None or row['status'] != 'started' or row['owner_epoch'] != self.authority.epoch
                or row['generation'] != self.row['generation']
                or row['request_id'] != self.row['request_id']
                or row['principal_id'] != _principal(self.authority, self.transport).subject
                or row['target_session_id'] != self.ref.session_id
                or self.ref.profile_id != self.authority.profile_id
                or retained is None or json.loads(retained[0]) != self.transport):
            raise RoomArtifactError('Group Chat output admission changed')

    def request(self, operation, **extra):
        from gateway.session_hosted_transport import _attest
        self.check_live()
        identity, generation = json.loads(self.row['request_id'][7:])
        result = _attest(self.transport, operation, dict(
            task=identity, execution_generation=generation, token=self.token, **extra))
        if (result['owner'] != self.transport['owner'] or result.get('scope') != self.scope.as_mapping()
                or result.get('cancel_generation') != self.cancel_generation):
            raise RuntimeStoreError('permission_denied')
        return result

    def outbox(self):
        self.check_live()
        self.used = True
        return self

    def put_bytes(self, *, scope, data, source_name, name=None):
        if scope != self.scope or not isinstance(data, bytes) or not 0 < len(data) <= MAX_ATTACHMENT_BYTES:
            raise RoomArtifactError('artifact must be a bounded regular file in the active scope')
        # Only this explicit promotion exposes a snapshot, and only until its
        # synchronous custody request returns. No second durable staging store.
        snapshot = secrets.token_hex(32)
        with self.lock:
            self.snapshots[snapshot] = (data, source_name, name, hashlib.sha256(data).hexdigest())
        try:
            return self.request('output-put', snapshot=snapshot)['artifact']
        finally:
            with self.lock:
                self.snapshots.pop(snapshot, None)

    def manifest(self):
        return self.request('output-manifest')['manifest']

    @contextmanager
    def registered(self):
        registry = self.authority.__dict__.setdefault('_hosted_output_sources', {})
        registry[self.token] = self
        try:
            yield
        finally:
            self.active = False
            registry.pop(self.token, None)
            with self.lock:
                self.snapshots.clear()


def named_output_binding(authority, ref, row):
    from gateway.session_hosted_transport import _BINDING, _attest
    with authority.db._read_ctx() as conn:
        stored = conn.execute('SELECT value FROM state_meta WHERE key=?', (_BINDING + ref.session_id,)).fetchone()
    transport = json.loads(stored[0])
    if not root_named_route(transport['source_home'], authority.profile_id):
        return None
    identity, generation = json.loads(row['request_id'][7:])
    result = _attest(transport, 'output-scope', dict(task=identity, execution_generation=generation))
    if result['owner'] != transport['owner']:
        raise RuntimeStoreError('permission_denied')
    binding = NamedOutputBinding(authority, ref, row, transport,
        RoomArtifactScope.from_mapping(result['scope']), result['cancel_generation'])
    binding.check_live()
    return binding


def read_output_source(authority, params):
    """Selected target owner lends only its explicitly registered byte snapshot."""
    if set(params) not in ({'source_home', 'token'}, {'source_home', 'token', 'snapshot', 'offset'}):
        raise RuntimeStoreError('invalid_params')
    binding = getattr(authority, '_hosted_output_sources', {}).get(params['token'])
    if binding is None or params['source_home'] != binding.transport['source_home']:
        raise RuntimeStoreError('permission_denied')
    binding.check_live()
    identity, generation = json.loads(binding.row['request_id'][7:])
    result = dict(scope=binding.scope.as_mapping(), cancel_generation=binding.cancel_generation,
        owner=binding.transport['owner'], task=identity, execution_generation=generation,
        target_home=authority.profile_id, admission_id=binding.row['admission_id'],
        canonical_generation=binding.row['generation'], owner_epoch=authority.epoch,
        session_id=binding.ref.session_id)
    if 'snapshot' in params:
        from gateway.session_hosted_transport import _CHUNK_BYTES
        with binding.lock:
            snapshot = binding.snapshots.get(params['snapshot'])
            if snapshot is None:
                raise RuntimeStoreError('permission_denied')
            data, source_name, name, digest = snapshot
            offset = params['offset']
            if type(offset) is not int or not 0 <= offset < len(data):
                raise RuntimeStoreError('invalid_params')
            result.update(size=len(data), sha256=digest,
                source_name=source_name, name=name,
                data_base64=base64.b64encode(data[offset:offset + _CHUNK_BYTES]).decode('ascii'))
    return result


def source_output(service, selector, operation, params):
    """Only the room's root owner writes its existing installation quota store."""
    from gateway.hosted_room_output_fence import require_output_task
    from gateway.session_hosted_service import _OWNER
    from gateway.session_hosted_transport import owner_request, _CHUNK_BYTES
    common = {'task', 'execution_generation', '_target_home'}
    expected = common if operation == 'output-scope' else common | {'token'}
    if operation == 'output-put':
        expected |= {'snapshot'}
    if set(params) != expected or not root_named_route(service.root, params['_target_home']):
        raise RuntimeStoreError('permission_denied')
    if Path(service.authority.db.db_path).resolve().parent != service.root:
        raise RuntimeStoreError('permission_denied')
    result = service.attest(selector, 'execute', params)
    task = next((t for t in service._list_tasks(selector['room_id'], ('running',))
                 if asdict(t['identity']) == params['task']
                 and t['execution_generation'] == params['execution_generation']), None)
    if task is None:
        raise RuntimeStoreError('permission_denied')
    gateway, epoch = service._owned_authority(selector['room_id'])
    scope = RoomArtifactScope.from_mapping(dict(room_id=selector['room_id'], task_id=task['identity'].task_id,
        execution_generation=task['execution_generation'], member_id=selector['member_id'],
        target_profile=selector['profile'], home_install_id=gateway, target_install_id=gateway,
        authority_gateway_id=gateway, authority_epoch=epoch))
    result = dict(owner=result['owner'], target_home=result['target_home'], scope=scope.as_mapping(),
                  cancel_generation=task['cancel_generation'])

    def check_source(conn, received_scope):
        _epoch(conn, service.authority.epoch)
        if received_scope != scope or service.profile_homes().get(scope.target_profile) != Path(params['_target_home']):
            raise RuntimeStoreError('permission_denied')
        require_output_task(conn, scope, task['cancel_generation'], status='running')
        owner = conn.execute('SELECT value FROM state_meta WHERE key=?', (_OWNER + scope.room_id,)).fetchone()
        if owner is None or owner[0] != result['owner']:
            raise RuntimeStoreError('permission_denied')

    with service.authority.db._read_ctx() as conn:
        check_source(conn, scope)
    if operation == 'output-scope':
        return result

    expected_proof = {**result, 'task': params['task'], 'execution_generation': params['execution_generation']}

    def borrow(**extra):
        proof = owner_request(params['_target_home'], 'hosted-output-source', dict(
            source_home=str(service.root), token=params['token'], **extra))
        if any(proof.get(k) != v for k, v in expected_proof.items()):
            raise RuntimeStoreError('permission_denied')
        return proof

    admission = borrow()

    def check_write(conn, received_scope):
        check_source(conn, received_scope)
        if borrow() != admission:
            raise RuntimeStoreError('permission_denied')

    if operation == 'output-manifest':
        # No foreign DB handle crosses the API, including during terminal capture.
        result['manifest'] = terminal_artifact_manifest(service.db_path, scope)
        with service.authority.db._read_ctx() as conn:
            check_write(conn, scope)
        return result
    data, metadata = bytearray(), None
    while metadata is None or len(data) < metadata['size']:
        chunk = borrow(snapshot=params['snapshot'], offset=len(data))
        raw = base64.b64decode(chunk.pop('data_base64'), validate=True)
        if (type(chunk.get('size')) is not int or not 0 < chunk['size'] <= MAX_ATTACHMENT_BYTES
                or len(raw) != min(_CHUNK_BYTES, chunk['size'] - len(data))
                or any(chunk.get(k) != v for k, v in admission.items())
                or (metadata is not None and chunk != metadata)):
            raise RuntimeStoreError('permission_denied')
        metadata = chunk
        data.extend(raw)
    if hashlib.sha256(data).hexdigest() != metadata['sha256']:
        raise RuntimeStoreError('permission_denied')
    outbox = RoomArtifactOutbox(service.db_path, authorize_write=check_write)
    result['artifact'] = outbox.put_bytes(scope=scope, data=bytes(data),
        source_name=metadata['source_name'], name=metadata['name'])
    return result
