"""Hosted submission must deny a task revoked during real input preparation.

The dispatch authorizer runs before preparation. Preparation can outlive that
decision, so submission checks the same task and generation again before any
NEW admission. This is the RPC callback contract, not a durable hosted-driver row.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError, list_session_admissions


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.hosted_room_input_reclamation import initialize_working_copies
    from gateway.runtime_ownership import process_ownership
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from gateway import run, session_policy

    # Same home the hermetic fixture already published. A second ``.hermes``
    # directory makes SessionDB, HERMES_HOME, and process ownership disagree,
    # and owned_home then refuses working-copy init.
    home = tmp_path / 'hermes_test'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(run, '_hermes_home', home)
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {
        'model': {'default': 'fixture'}, 'platform_toolsets': {'cli': []}})
    monkeypatch.setattr(run, '_resolve_gateway_model', lambda cfg: 'fixture')
    original = session_policy.restore_policy

    def restore(data):
        from dataclasses import replace
        if data['source'] == 'bot_room':
            return replace(original({**data, 'source': 'gui', 'platform': 'desktop'}),
                           source='bot_room', platform='bot_room')
        return original(data)

    monkeypatch.setattr(session_policy, 'restore_policy', restore)
    process_ownership.reserve([home])
    release = threading.Event()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name='hosted-revocation', daemon=True)
    thread.start()
    store = None
    try:
        store = SessionStore(home / 'sessions', GatewayConfig())
        runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False)
        authority = asyncio.run_coroutine_threadsafe(
            initialize_session_authority(runner, profile_id=str(home), instance_id='owner'), loop).result(timeout=10)
        initialize_working_copies(authority.db, epoch=authority.epoch)
        scheduled = []
        monkeypatch.setattr(authority, '_schedule', scheduled.append)
        principal = Principal('durable-room-owner', str(home), frozenset({
            'session:create', 'session:read', 'session:submit', 'session:control', 'session:approve'}),
            'room-worker')
        yield SimpleNamespace(authority=authority, loop=loop, principal=principal,
                              scheduled=scheduled, release=release, db=store._db)
    finally:
        release.set()
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            asyncio.run_coroutine_threadsafe(
                asyncio.gather(*pending, return_exceptions=True), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        if store is not None:
            store._db.close()
        process_ownership.release(home)


def test_revoked_during_preparation_admits_nothing_and_authorized_generation_admits_once(hosted, monkeypatch):
    from gateway.hosted_room_driver import TaskIdentity
    from gateway.hosted_room_input_preparation import prepare_hosted_input
    from gateway.session_hosted_rpc import HostedRoomAuthorityRPC
    import gateway.hosted_room_input_preparation as preparation

    authority, scheduled, release = hosted.authority, hosted.scheduled, hosted.release
    allowed = set()
    checks = []

    def authorize(operation, task, generation):
        identity = getattr(task, 'task_id', None)
        checks.append((operation, identity, generation))
        if operation != 'submit':
            return True
        return (identity, generation) in allowed

    rpc = HostedRoomAuthorityRPC(
        authority, hosted.loop, room_id='room', member_id='member', profile='default',
        principal=hosted.principal, authorize=authorize)
    coords = dict(profile='default', source='bot_room')
    sid = rpc.create(**coords, title='Group: room')['session_id']
    coords['session_id'] = sid
    entered = threading.Event()

    def blocked(rpc_self, **kwargs):
        entered.set()
        assert release.wait(5), 'preparation was not released'
        return prepare_hosted_input(rpc_self, **kwargs)

    monkeypatch.setattr(preparation, 'prepare_hosted_input', blocked)
    denied = TaskIdentity('room', 'revoked-task', 'thread', 'turn')
    allowed.add((denied.task_id, 1))
    holder = {}

    def submit_denied():
        try:
            holder['result'] = rpc.submit(
                **coords, prompt='revoke me', task=denied, execution_generation=1,
                on_terminal=lambda receipt: None)
        except Exception as exc:
            holder['error'] = exc

    worker = threading.Thread(target=submit_denied, name='revoked-submit')
    worker.start()
    try:
        assert entered.wait(5), 'preparation did not start'
        allowed.discard((denied.task_id, 1))
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        error = holder.get('error')
        assert isinstance(error, RuntimeStoreError) and error.reason == 'permission_denied'
        assert 'result' not in holder
        assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
        assert scheduled == []

        allowed_task = TaskIdentity('room', 'live-task', 'thread', 'turn')
        allowed.add((allowed_task.task_id, 2))
        before = len(checks)
        receipt = rpc.submit(
            **coords, prompt='keep me', task=allowed_task, execution_generation=2,
            on_terminal=lambda receipt: None)
        positive = [item for item in checks[before:] if item[0] == 'submit' and item[1] == allowed_task.task_id]
        assert positive == [('submit', allowed_task.task_id, 2), ('submit', allowed_task.task_id, 2)]
        rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        assert len(rows) == 1
        assert rows[0]['admission_id'] == receipt['admission_id']
        assert rows[0]['payload']['text'] == 'keep me'
        assert len(scheduled) == 1 and scheduled[0].session_id == sid
    finally:
        release.set()
        worker.join(timeout=5)
