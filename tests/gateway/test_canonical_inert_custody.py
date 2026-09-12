"""Custody preparation anchors existing canonical context; it never resumes it."""
import asyncio
from pathlib import Path
import sqlite3
import time

import pytest

from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_inert_recovery import connection


@pytest.mark.asyncio
@pytest.mark.parametrize('retained_status', ['queued', 'unknown'])
async def test_exact_admission_custody_prepares_and_queries_without_creating_work(tmp_path, monkeypatch, retained_status):
    from gateway import hosted_room_driver as tasks
    from gateway.hosted_room_local_custody import TABLE
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        native = connection(authority)
        service.send(room_id='room', event_id='request', payload={'text': '@writer Inspect', 'thread_id': 'thread'})
        task = tasks.list_tasks(service.db_path, room_id='room', status='queued')[0]
        binding = service.bindings()[0]
        lease = tasks.acquire_lease(service.db_path, room_id='room', gateway_id=binding.gateway_id,
            authority_epoch=1, process_generation='record-test', ttl_seconds=60, clock=time.time)
        attempt = tasks.start_task(service.db_path, task['identity'], lease, expected_cancel_generation=0, clock=time.time)
        rpc = service._resolve_member_transport(binding, task)
        coords = {'profile': 'default', 'source': 'bot_room'}
        sid = (await asyncio.to_thread(rpc.create, **coords, title='Group: room'))['session_id']
        receipt = await asyncio.to_thread(rpc.submit, **coords, session_id=sid, prompt=task['payload']['prompt'],
            task=task['identity'], execution_generation=attempt.execution_generation, on_terminal=lambda _: None)
        if retained_status == 'unknown':
            # Pre-existing retained uncertainty, not a fault/recovery operation.
            authority.db._execute_write(lambda conn: conn.execute(
                "UPDATE session_admissions SET status='unknown',generation=1 WHERE admission_id=?", (receipt['admission_id'],)))
        tables = ('sessions', 'session_admissions', 'hosted_rooms', 'hosted_room_events', 'hosted_room_driver_tasks', 'worker_executions')
        before = {table: authority.db._read_all(f'SELECT * FROM {table}') for table in tables}
        params = {'room_id': 'room', 'member_id': 'writer', 'admission_id': receipt['admission_id']}
        status_request = {'id': 1, 'method': 'groups.custody.status', 'params': {'room_id': 'room', 'member_id': 'writer'}}
        assert (await native.dispatch(status_request))['result']['status'] == 'not_recorded'
        remote = connection(authority, native=False)
        request = {'id': 2, 'method': 'groups.custody.prepare', 'params': params}
        assert (await remote.dispatch(request))['error']['message'] == 'permission_denied'
        with monkeypatch.context() as proof:
            proof.setattr('gateway.hosted_room_authority_history.read_history_locked',
                          lambda *args, **kwargs: [{'gateway_id': 'install:another-original-home'}])
            assert (await native.dispatch(request))['error']['message'] == 'original_custody_unavailable'
        assert {table: authority.db._read_all(f'SELECT * FROM {table}') for table in tables} == before
        result = await native.dispatch(request)
        assert 'error' not in result, result
        prepared = result['result']
        assert prepared['status'] == 'verified' and prepared['session_id'] == sid
        assert prepared['idempotent'] is False
        assert prepared['home_install_id'] == binding.gateway_id
        assert prepared['accepted_tail'] == 'unverified' and prepared['execution_authorized'] is False
        assert prepared['old_admission_fenced'] is False
        repeated = await native.dispatch(request)
        assert repeated['result'] == {**prepared, 'idempotent': True}
        assert {table: authority.db._read_all(f'SELECT * FROM {table}') for table in tables} == before
        authority.sessions.clear()
        assert (await native.dispatch(status_request))['result']['custody_sha256'] == prepared['custody_sha256']
        assert not authority.sessions
        with sqlite3.connect(authority.db.db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError, match='immutable custody'):
                conn.execute(f"UPDATE {TABLE} SET session_id='replacement'")
            with pytest.raises(sqlite3.IntegrityError, match='permanent custody'):
                conn.execute(f'DELETE FROM {TABLE}')
        wrong = {**request, 'params': {**params, 'admission_id': 'missing'}}
        assert 'error' in await native.dispatch(wrong)
        assert {table: authority.db._read_all(f'SELECT * FROM {table}') for table in tables} == before


@pytest.mark.asyncio
async def test_custody_never_uses_a_title_to_create_missing_context(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        native = connection(authority)
        before = authority.db._read_all('SELECT * FROM sessions')
        result = await native.dispatch({'id': 1, 'method': 'groups.custody.prepare',
            'params': {'room_id': 'room', 'member_id': 'writer', 'admission_id': 'not-an-admission'}})
        assert 'error' in result
        assert authority.db._read_all('SELECT * FROM sessions') == before
        assert not authority.db._read_all('SELECT * FROM session_admissions')
        assert not authority.db._read_all("SELECT 1 FROM sqlite_master WHERE name='hosted_room_local_custody'")
