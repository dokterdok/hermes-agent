"""Post-settlement caller of secondary retained publication.

A settled invitation→NEW task publishes through the secondary consumer after
primary evidence exists. History, info, and primary publish_terminal do not.
Send-consent is not passed. A missing contract fails closed and writes nothing.
"""
import asyncio
import json

import pytest

from gateway.hosted_room_artifacts import RoomArtifactError
from gateway.session_hosted_output_secondary_caller import (
    SecondaryAwaitingPrimary, call_settled_invitation_secondary)


def _fixtures():
    from tests.gateway.test_secondary_retained_publication import (
        _bind_retention_safety, _ensure_quarantine_table, _primary, _secondary_counts, _settled)
    return _bind_retention_safety, _ensure_quarantine_table, _primary, _secondary_counts, _settled


def _rpc(service):
    from gateway.session_hosted_rpc import HostedRoomAuthorityRPC
    found = [rpc for rpc in service.member_rpcs.values() if type(rpc) is HostedRoomAuthorityRPC]
    assert len(found) == 1
    return found[0]


def _secondary_view(db):
    with db._read_ctx() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'hosted_room_secondary_publications' not in tables:
            return [], 0
        rows = [tuple(row) for row in conn.execute(
            'SELECT operation, reason_code, blocked, attempts FROM hosted_room_secondary_publications '
            'ORDER BY publication_id')]
        completed = conn.execute(
            'SELECT COUNT(*) FROM hosted_room_secondary_publication_completions').fetchone()[0]
    return rows, completed


def _drop(db):
    from tests.gateway.test_secondary_retained_publication import _drop_secondary
    _drop_secondary(db)


@pytest.mark.asyncio
async def test_settled_invitation_publishes_secondary_outside_terminal_and_primary(tmp_path, monkeypatch):
    _bind, _quarantine, _primary, _secondary_counts, _settled = _fixtures()
    del _bind, _quarantine
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        assert task['status'] == 'settled'
        assert _secondary_view(authority.db) == ([('publish', 'pending', 0, 1)], 0)
        assert _secondary_counts(authority.db) == (1, 0)
        before = _primary(authority.db)
        pointer = runner.session_authority
        rpc = _rpc(service)
        binding = service.bindings()[0]
        calls = []
        original = service.publish_settled_invitation_secondary

        def spy(bound, settled):
            calls.append(settled['status'])
            return original(bound, settled)

        service.publish_settled_invitation_secondary = spy
        service.runtime.publish_settled_secondary = spy
        service.publish_terminal(binding, task)
        await asyncio.to_thread(
            rpc.history, profile='default', session_id=rpc.ref.session_id, source='bot_room')
        await asyncio.to_thread(
            rpc.info, profile='default', session_id=rpc.ref.session_id, source='bot_room')
        assert calls == []
        assert _primary(authority.db) == before
        assert _secondary_counts(authority.db) == (1, 0)
        assert runner.session_authority is pointer is authority
        seen = []
        publish = rpc.publish_secondary_retained

        def wrapped(settled, **kwargs):
            seen.append(kwargs)
            return publish(settled, **kwargs)

        rpc.publish_secondary_retained = wrapped
        _drop(authority.db)
        again = service.publish_settled_invitation_secondary(binding, task)
        assert seen == [{}]
        assert again['published'] is True and again['completed'] is False
        assert 'consent' not in seen[0]
        consent_changes = authority.db._conn.total_changes
        with pytest.raises(RoomArtifactError, match='send consent is not publication authority'):
            rpc.publish_secondary_retained(task, consent={'permissions': ['publish']})
        assert authority.db._conn.total_changes == consent_changes
        assert _secondary_counts(authority.db) == (1, 0)
        assert _primary(authority.db) == before


@pytest.mark.asyncio
async def test_deferred_primary_does_not_publish_secondary(tmp_path, monkeypatch):
    _bind, _quarantine, _primary, _secondary_counts, _settled = _fixtures()
    del _primary, _settled
    from tests.gateway.test_canonical_hosted_outputs import execute_group_turn, owner
    from gateway import hosted_room_driver as tasks
    _bind()
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        _quarantine(authority.db)
        output = tmp_path / 'cache' / 'report.txt'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'explicit secondary bytes')

        async def handle(event):
            from tools.registry import registry
            result = json.loads(await asyncio.to_thread(
                registry.dispatch, 'share_group_file', {'path': str(output)}))
            assert result.get('ok') is True, result
            return 'Shared report.'

        runner._handle_message = handle
        from tools import hosted_room_artifact  # registry discovery
        del hosted_room_artifact
        await execute_group_turn(authority, service, defer_publication=True)
        stored = tasks.get_task(service.db_path, tasks.list_tasks(service.db_path, room_id='room')[0]['identity'])
        assert stored['status'] == 'settled'
        assert _secondary_counts(authority.db) == (0, 0)
        binding = service.bindings()[0]
        assert type(call_settled_invitation_secondary(service, binding, stored)) is SecondaryAwaitingPrimary
        assert (stored['identity'], stored['execution_generation']) in service.runtime._secondary_awaiting_primary
        assert _secondary_counts(authority.db) == (0, 0)
        assert runner.session_authority is authority


@pytest.mark.asyncio
async def test_catch_up_publishes_after_noop_notify_when_primary_events_appear(tmp_path, monkeypatch):
    """Primary evidence after a no-op notify publishes secondary outside prepare and terminal."""
    _bind, _quarantine, _primary, _secondary_counts, _settled = _fixtures()
    del _settled
    from tests.gateway.test_canonical_hosted_outputs import execute_group_turn, owner
    from gateway import hosted_room_driver as tasks
    _bind()
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        _quarantine(authority.db)
        output = tmp_path / 'cache' / 'report.txt'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'explicit secondary bytes')

        async def handle(event):
            from tools.registry import registry
            result = json.loads(await asyncio.to_thread(
                registry.dispatch, 'share_group_file', {'path': str(output)}))
            assert result.get('ok') is True, result
            return 'Shared report.'

        runner._handle_message = handle
        from tools import hosted_room_artifact
        del hosted_room_artifact
        await execute_group_turn(authority, service, defer_publication=True)
        stored = tasks.get_task(service.db_path, tasks.list_tasks(service.db_path, room_id='room')[0]['identity'])
        binding = service.bindings()[0]
        pending_key = (stored['identity'], stored['execution_generation'])
        assert stored['status'] == 'settled'
        assert _secondary_counts(authority.db) == (0, 0)
        assert pending_key in service.runtime._secondary_awaiting_primary
        service.prepare_room(binding)
        service.publish_terminal(binding, stored)
        assert _secondary_counts(authority.db) == (0, 0)
        assert pending_key in service.runtime._secondary_awaiting_primary
        primary_after_events = _primary(authority.db)
        rpc = _rpc(service)
        seen = []
        publish = rpc.publish_secondary_retained

        def wrapped(settled, **kwargs):
            seen.append(kwargs)
            return publish(settled, **kwargs)

        rpc.publish_secondary_retained = wrapped
        pointer = runner.session_authority
        service.runtime._catch_up_secondary_after_primary(binding)
        assert seen == [{}]
        assert 'consent' not in seen[0]
        assert _secondary_view(authority.db) == ([('publish', 'pending', 0, 1)], 0)
        assert _secondary_counts(authority.db) == (1, 0)
        assert _primary(authority.db) == primary_after_events
        assert pending_key not in service.runtime._secondary_awaiting_primary
        assert binding.room_id not in service.runtime._ambiguous_rooms
        assert runner.session_authority is pointer is authority
        service.runtime._catch_up_secondary_after_primary(binding)
        assert seen == [{}]
        assert _secondary_counts(authority.db) == (1, 0)
        assert _primary(authority.db) == primary_after_events


@pytest.mark.asyncio
async def test_catch_up_missing_contract_writes_nothing_and_stays_pending(tmp_path, monkeypatch):
    _bind, _quarantine, _primary, _secondary_counts, _settled = _fixtures()
    del _settled
    from tests.gateway.test_canonical_hosted_outputs import execute_group_turn, owner
    from gateway import hosted_room_driver as tasks
    _bind()
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        _quarantine(authority.db)
        output = tmp_path / 'cache' / 'report.txt'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'explicit secondary bytes')

        async def handle(event):
            from tools.registry import registry
            result = json.loads(await asyncio.to_thread(
                registry.dispatch, 'share_group_file', {'path': str(output)}))
            assert result.get('ok') is True, result
            return 'Shared report.'

        runner._handle_message = handle
        from tools import hosted_room_artifact
        del hosted_room_artifact
        await execute_group_turn(authority, service, defer_publication=True)
        stored = tasks.get_task(service.db_path, tasks.list_tasks(service.db_path, room_id='room')[0]['identity'])
        binding = service.bindings()[0]
        pending_key = (stored['identity'], stored['execution_generation'])
        before = _primary(authority.db)
        service.prepare_room(binding)
        service.publish_terminal(binding, stored)
        events_now = _primary(authority.db)
        assert events_now != before
        assert _secondary_counts(authority.db) == (0, 0)
        for name in (
                'register_secondary_publication', 'publish_secondary_publication',
                'retry_secondary_publication', 'record_secondary_publication_failure',
                'complete_secondary_publication'):
            monkeypatch.setattr(service, name, None)
        service.runtime._catch_up_secondary_after_primary(binding)
        assert _secondary_counts(authority.db) == (0, 0)
        assert pending_key in service.runtime._secondary_awaiting_primary
        fresh = tasks.get_task(service.db_path, stored['identity'])
        assert fresh['status'] == 'settled'
        assert binding.room_id not in service.runtime._ambiguous_rooms
        assert 'not registered' in (service.runtime.status()['last_error'] or '')
        assert _primary(authority.db) == events_now
        service.runtime._catch_up_secondary_after_primary(binding)
        assert _secondary_counts(authority.db) == (0, 0)
        assert pending_key in service.runtime._secondary_awaiting_primary
        assert _primary(authority.db) == events_now
        assert runner.session_authority is authority


@pytest.mark.asyncio
async def test_missing_contract_and_foreign_transport_write_nothing(tmp_path, monkeypatch):
    _bind, _quarantine, _primary, _secondary_counts, _settled = _fixtures()
    del _bind, _quarantine
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        binding = service.bindings()[0]
        _drop(authority.db)
        for name in (
                'register_secondary_publication', 'publish_secondary_publication',
                'retry_secondary_publication', 'record_secondary_publication_failure',
                'complete_secondary_publication'):
            monkeypatch.setattr(service, name, None)
        with pytest.raises(RoomArtifactError, match='not registered'):
            service.publish_settled_invitation_secondary(binding, task)
        assert _secondary_counts(authority.db) == (0, 0)
        assert _primary(authority.db) == before
        assert task['status'] == 'settled'
        monkeypatch.undo()
        service._resolve_member_transport = lambda *_args, **_kwargs: object()
        assert call_settled_invitation_secondary(service, binding, task) is None
        assert _secondary_counts(authority.db) == (0, 0)
        assert runner.session_authority is authority


@pytest.mark.asyncio
async def test_secondary_failure_after_commit_is_not_an_observation_failure(tmp_path, monkeypatch):
    """A secondary error after settle_task leaves the committed task settled."""
    _bind, _quarantine, _primary, _secondary_counts, _settled = _fixtures()
    del _primary, _settled
    from gateway import hosted_room_driver as tasks
    import time
    _bind()
    from tests.gateway.test_canonical_hosted_outputs import owner
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        _quarantine(authority.db)
        service.send(room_id='room', event_id='request', payload={
            'thread_id': 'thread', 'text': '@writer Write the report'})
        task = tasks.list_tasks(service.db_path, room_id='room', status='queued')[0]
        binding = service.bindings()[0]
        lease = tasks.acquire_lease(
            service.db_path, room_id='room', gateway_id=binding.gateway_id,
            authority_epoch=binding.authority_epoch, process_generation='driver',
            ttl_seconds=60, clock=time.time)
        attempt = tasks.start_task(
            service.db_path, task['identity'], lease,
            expected_cancel_generation=0, clock=time.time)

        def boom(_binding, _settled):
            raise RoomArtifactError('Group Chat secondary publication is not registered')

        service.runtime.publish_terminal = lambda *_args, **_kwargs: None
        service.runtime.publish_settled_secondary = boom

        class Transport:
            def submit(self, **kwargs):
                kwargs['on_terminal']({
                    'status': 'settled', 'text': 'done', 'message_id': 'm-secondary'})

        service.runtime._transport_for = lambda *_args, **_kwargs: Transport()
        service.runtime._resolve_or_create = lambda *_args, **_kwargs: {'session_id': 's'}
        with pytest.raises(RoomArtifactError, match='not registered'):
            service.runtime._execute_attempt(binding, task, attempt)
        stored = tasks.get_task(service.db_path, task['identity'])
        assert stored['status'] == 'settled'
        assert binding.room_id not in service.runtime._ambiguous_rooms
        assert _secondary_counts(authority.db) == (0, 0)
        assert runner.session_authority is authority


def test_caller_ignores_unsettled_tasks_and_does_not_require_a_contract():
    class Bare:
        def _output_key(self, task):
            raise AssertionError(task)

    service = Bare()
    assert call_settled_invitation_secondary(service, None, {'status': 'failed', 'result': {'artifacts': {}}}) is None
    assert call_settled_invitation_secondary(service, None, {'status': 'settled', 'result': {}}) is None
    assert call_settled_invitation_secondary(service, None, {'status': 'settled'}) is None
