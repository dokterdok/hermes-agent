"""Accepted API decision work remains counted after its HTTP observer leaves."""
import asyncio
import json
from threading import Event

import pytest
from aiohttp.test_utils import make_mocked_request
from gateway import session_group_decisions as decisions
from gateway.config import PlatformConfig
from gateway.platforms import api_server
from gateway.session_contract import Principal
from gateway.session_group_delegation import dispatch_owner_delegation
from gateway.session_group_home_access import dispatch_home_access
from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_group_decisions import pending


def arguments(service, task, choice='once'):
    return dict(room=service._room('room'), command_id='independent-decision',
        params=dict(member_id='writer', task_id=task['identity'].task_id,
                    execution_generation=1, request_id='first-prompt', choice=choice))


def grant(authority, kind):
    actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
    if kind == 'home':
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        def revoke():
            dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': False})
        return {'guard': lambda: None}, revoke
    token = dispatch_owner_delegation(authority, actor, 'issue',
        {'room_id': 'room', 'member_id': 'writer', 'request_id': 'review-peer'})['control_token']
    def revoke():
        dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'writer'})
    return {'member_id': 'writer', 'token': token}, revoke


@pytest.mark.asyncio
async def test_cancelled_http_handler_keeps_accepted_worker_visible_until_completion(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        task, row, live, answers = await pending(authority, service)
        auth, _ = grant(authority, 'peer')
        args = arguments(service, task)
        adapter = api_server.APIServerAdapter(PlatformConfig(enabled=True))
        adapter.gateway_runner = runner
        body = {'command_id': args['command_id'], 'decision': args['params']}
        async def read_body(request):
            return body, None
        monkeypatch.setattr(adapter, '_read_json_body', read_body)
        handler = next(handler for method, path, handler in adapter._http_route_table()
                       if method == 'POST' and path == '/v1/room-controls/{room_id}/approvals')
        request = make_mocked_request('POST', '/v1/room-controls/room/approvals',
            headers={'Authorization': 'HermesRoomControl ' + auth['token'], 'X-Hermes-Room-Member': 'writer'},
            match_info={'room_id': 'room'})
        entered, release, finished = Event(), Event(), Event()
        original = service.approve_room_task
        def delayed(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'approve_room_task', delayed)
        decide = decisions.decide
        def observed(*args, **kwargs):
            try:
                return decide(*args, **kwargs)
            finally:
                finished.set()
        monkeypatch.setattr('gateway.platforms.api_server_room_decisions.decide', observed)
        operation = asyncio.create_task(handler(request))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with authority.db._read_ctx() as conn:
                receipt, = conn.execute('SELECT value FROM state_meta WHERE key LIKE ?', (decisions._PREFIX + '%',)).fetchall()
            assert json.loads(receipt[0])['result'] is None
            assert adapter.active_agent_work_count() == 1
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            remaining = adapter.active_agent_work_count()
            still_running = not finished.is_set()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
            adapter._run_idempotency_store.close()
            adapter._response_store.close()
        assert answers == [('approval', 'first-prompt', 'once')]
        assert still_running and remaining == 1, {'unfinished_worker': still_running, 'reported_active': remaining}
