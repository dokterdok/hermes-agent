"""Exact inherited task cancellation through real HTTP and canonical admission storage."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_room_discussion as discussion, hosted_rooms
from gateway.platforms.api_server_authority_runs import run_admission, stop_run
from gateway.platforms.api_server_run_scope import room_run_scope_key
from hermes_state_logical_attempts import prepare_logical_attempt_index
from tests.gateway.test_api_group_owner_stop import app_for, participation
from tests.gateway.test_api_group_owner_stop_canonical import canonical as canonical
from tests.gateway.test_api_group_run_fence import HOME, ROOM, SUCCESSOR, bearer, invite, promise, scoped, submit


def _discussion_task(tmp_path):
    path = tmp_path / 'discussion.db'
    room = hosted_rooms.create_room(path, room_id=ROOM, name='Cancel', authority_gateway_id=HOME,
        members=[{'member_id': 'writer', 'profile': 'default', 'handle': 'writer'},
                 {'member_id': 'other', 'profile': 'ops', 'handle': 'other'}])
    event = hosted_rooms.append_event(path, room_id=ROOM, event_id='request', kind='message.user',
        actor={'kind': 'user', 'id': 'owner'}, authority_gateway_id=HOME, authority_epoch=1,
        payload={'text': '@writer inspect', 'thread_id': 'thread'})
    return discussion.plan_next_task(room, [event], local_profiles=('default', 'ops')).task.identity.task_id


async def _cancel(cli, invitation, payload):
    return await cli.post('/v1/runs/stop', json={'input': payload['prompt'], 'hosted_room_dispatch': payload},
        headers=bearer(invitation, **{'Idempotency-Key': f"room:{payload['task_id']}:{payload['execution_generation']}"}))


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['inherited', 'missing_run', 'new_generation_frozen', 'ambiguous'])
async def test_successor_cancels_only_one_proven_exact_attempt(canonical, tmp_path, case):
    adapter, authority, runner = canonical
    authority._schedule = lambda ref: None
    calls = []

    async def service(event):
        calls.append(event.text)
        return 'must not run'
    runner._handle_message = service
    task_id = _discussion_task(tmp_path)
    key = f'room:{task_id}:1'
    store = adapter._run_idempotency_store
    async with TestClient(TestServer(app_for(adapter))) as cli:
        old = await invite(cli)
        if case == 'ambiguous':
            # Imported historical records need not retain their expired invitation.
            second_identity = participation(scoped(old, 'install:second-history', 2, task_id))
            store.reserve(room_run_scope_key(second_identity), key, 'historical', 'run_second_history',
                {'run_id': 'run_second_history', 'status': 'queued'}, identity=second_identity)
        original = scoped(old, task=task_id)
        accepted = await submit(cli, old, original)
        assert accepted.status == 202, await accepted.text()
        run_id = (await accepted.json())['run_id']
        assert run_admission(adapter, run_id)[1]['status'] == 'queued'
        assert prepare_logical_attempt_index(authority.db, batch_size=128, epoch=authority.epoch)['complete']
        epoch = 3 if case == 'ambiguous' else 2
        promise(adapter, epoch=epoch)
        successor = await invite(cli, SUCCESSOR, epoch, previous={
            'home_install_id': HOME, 'authority_gateway_id': HOME, 'authority_epoch': 1})
        from gateway.hosted_room_succession import record_lineage_locked
        authority.db._execute_write(lambda conn: record_lineage_locked(conn, ROOM,
            origin_install_id=HOME, gateway_id=SUCCESSOR, epoch=epoch, role='attested', proof_digest='a' * 64))
        requested = scoped(successor, SUCCESSOR, epoch, task_id)
        new_scope = room_run_scope_key(participation(requested))
        if case == 'missing_run':
            store._conn.execute('DELETE FROM run_idempotency WHERE run_id=?', (run_id,))
            store._conn.commit()
        if case == 'new_generation_frozen':
            requested['execution_generation'] = 2
            store.reserve(new_scope, f'room:{task_id}:known-scope:1', 'historical', 'run_known_successor',
                {'run_id': 'run_known_successor', 'status': 'completed'}, identity=participation(requested))
            store.freeze_room_scope(participation(requested), 'frozen-successor')
        try:
            stopped = await _cancel(cli, successor, requested)
            body = await stopped.json()
            if case in {'missing_run', 'ambiguous'}:
                assert stopped.status == 503, body
                assert run_admission(adapter, run_id)[1]['status'] == 'queued'
                assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency WHERE scope=?', (new_scope,)).fetchone()[0] == 0
            else:
                assert stopped.status == 200 and body['status'] == 'cancelled', body
                if case == 'inherited':
                    assert body['run_id'] == run_id
                    assert run_admission(adapter, run_id)[1]['outcome'] == 'cancelled'
                    assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency WHERE scope=?', (new_scope,)).fetchone()[0] == 0
                    evidence = store.room_run_evidence(ROOM, through_epoch=epoch)
                    assert evidence['runs'][0]['task_id'] == task_id and not evidence['truncated']
                else:
                    assert body['run_id'] != run_id and body['admission_cancelled']
                    assert run_admission(adapter, run_id)[1]['status'] == 'queued'
                    assert store.is_scope_frozen(new_scope)
                    late = await cli.post('/v1/runs', json={'input': requested['prompt'], 'hosted_room_dispatch': requested},
                        headers=bearer(successor, **{'Idempotency-Key': f'room:{task_id}:2'}))
                    late_body = await late.json()
                    assert late.status == 202 and late_body['run_id'] == body['run_id'], late_body
                    assert store.owns_run(new_scope, body['run_id'])
                    refused = await submit(cli, successor, scoped(successor, SUCCESSOR, epoch, task_id + ':other'))
                    assert refused.status == 403 and (await refused.json())['error']['code'] == 'group_work_frozen'
            assert calls == []
        finally:
            await stop_run(adapter, run_id)
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values(), return_exceptions=True), 3)
