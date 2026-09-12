"""Owner decisions resolve only their exact native approval and preserve receipt replay."""
import asyncio
import time

import pytest

from tests.gateway.test_canonical_hosted_outputs import owner


async def pending(authority, service):
    from gateway import hosted_room_driver as tasks
    from hermes_state_runtime import claim_session_input
    service.send(room_id='room', event_id='decision-request', payload=dict(thread_id='thread', text='@writer Prepare a report.'))
    task, = tasks.list_tasks(service.db_path, room_id='room')
    binding, = service.bindings()
    lease = tasks.acquire_lease(service.db_path, room_id='room', gateway_id=binding.gateway_id,
        authority_epoch=binding.authority_epoch, process_generation='decision-test', ttl_seconds=60, clock=time.time)
    attempt = tasks.start_task(service.db_path, task['identity'], lease, expected_cancel_generation=0, clock=time.time)
    rpc = service._resolve_member_transport(binding, task)
    coords = dict(profile='default', source='bot_room')
    sid = (await asyncio.to_thread(rpc.create, **coords, title='Group: room'))['session_id']
    await asyncio.to_thread(rpc.submit, **coords, session_id=sid, prompt=task['payload']['prompt'], task=task['identity'],
        execution_generation=attempt.execution_generation, on_terminal=lambda receipt: None)
    row = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    live = authority.sessions[sid]
    authority.register_approval(sid, row['generation'], live.route,
        {'request_id': 'first-prompt', 'command': 'fixture confirmation'})
    answers = []
    live.controls.remote_responders['first-prompt'] = lambda *args: answers.append(args)
    return task, row, live, answers


@pytest.mark.asyncio
async def test_local_owner_approval_and_duplicate_receipt_never_target_a_following_prompt(tmp_path, monkeypatch):
    from gateway.session_contract import Principal
    from gateway.session_group_home_access import dispatch_home_access
    from gateway.session_group_decisions import decide
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        args = dict(room=service._room('room'), guard=lambda: None, command_id='decision',
            params=dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                        request_id='first-prompt', choice='once'))
        result = await asyncio.to_thread(decide, authority, **args)
        assert result == {'status': 'resolved', 'prompt_id': 'first-prompt'}
        authority.register_approval(row['target_session_id'], row['generation'], live.route,
            {'request_id': 'next-prompt', 'command': 'different fixture'})
        live.controls.remote_responders['next-prompt'] = lambda *args: answers.append(args)
        assert await asyncio.to_thread(decide, authority, **args) == result
        assert answers == [('approval', 'first-prompt', 'once')]
        assert 'next-prompt' in live.controls.pending
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': False})
        from hermes_state_runtime import RuntimeStoreError
        with pytest.raises(RuntimeStoreError):
            await asyncio.to_thread(decide, authority, **args)
        assert len(answers) == 1


@pytest.mark.asyncio
async def test_home_decision_guard_runs_in_the_acceptance_writer(tmp_path, monkeypatch):
    from gateway.session_contract import Principal
    from gateway.session_group_home_access import dispatch_home_access
    from gateway.session_group_decisions import decide
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        allowed = True
        original = authority.db._execute_write
        def revoke_before_write(operation, *args, **kwargs):
            nonlocal allowed
            allowed = False
            return original(operation, *args, **kwargs)
        monkeypatch.setattr(authority.db, '_execute_write', revoke_before_write)
        def guard():
            if not allowed:
                raise PermissionError('Home changed')
        with pytest.raises(PermissionError):
            await asyncio.to_thread(decide, authority, room=service._room('room'), guard=guard, command_id='late',
                params=dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                            request_id='first-prompt', choice='once'))
        assert answers == [] and 'first-prompt' in live.controls.pending


@pytest.mark.asyncio
async def test_reciprocal_http_approval_uses_canonical_prompt_and_exact_receipt(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platforms.api_server_room_decisions import http_routes
    from gateway.session_contract import Principal
    from gateway.session_group_delegation import dispatch_owner_delegation
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        task, row, live, answers = await pending(authority, service)
        params = dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                      request_id='first-prompt', choice='once')
        service._set_pending_action('room', 'writer', {'kind': 'approval', **{key: value for key, value in params.items() if key != 'choice'}})
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        token = dispatch_owner_delegation(authority, actor, 'issue',
            {'room_id': 'room', 'member_id': 'writer', 'request_id': 'peer-return'})['control_token']
        adapter = APIServerAdapter(PlatformConfig(enabled=True))
        adapter.gateway_runner = runner
        app = web.Application()
        for method, path, handler in http_routes(adapter):
            app.router.add_route(method, path, handler)
        try:
            async with TestClient(TestServer(app)) as http:
                headers = {'Authorization': 'HermesRoomControl ' + token, 'X-Hermes-Room-Member': 'writer'}
                path = '/v1/room-controls/room/approvals'
                read = await http.get(path, headers=headers)
                assert read.status == 200, await read.text()
                item, = (await read.json())['approvals']
                assert item['request_id'] == 'first-prompt' and item['choices'] == ['once', 'deny']
                body = {'command_id': 'decision', 'decision': params}
                response = await http.post(path, headers=headers, json=body)
                assert response.status == 200, await response.text()
                receipt = await response.json()
                assert receipt['result'] == {'status': 'resolved', 'prompt_id': 'first-prompt'}
                assert (await (await http.post(path, headers=headers, json=body)).json()) == receipt
                assert answers == [('approval', 'first-prompt', 'once')]
                dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'writer'})
                assert (await http.post(path, headers=headers, json=body)).status == 409
                assert len(answers) == 1
        finally:
            adapter._run_idempotency_store.close()
            adapter._response_store.close()
