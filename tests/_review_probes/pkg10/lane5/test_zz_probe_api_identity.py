"""G3: successful HTTP handoffs must report the chat which actually executed."""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import Platform
from tests.gateway.test_peer_dm_bot_chat_headless import headless, AUTHOR
from tests.waiting import async_wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize('door', ['chat', 'stream', 'run'])
async def test_relocated_delivery_reports_actual_session(headless, door):
    db = headless.authority.db
    old = 'api_legacy_bot'
    db.create_session(old, 'api_server')
    db.set_session_title(old, 'Bot Chat')
    db.append_message(old, 'user', 'old history')
    adapter = headless.authority.runner.adapters[Platform.API_SERVER]
    app = web.Application()
    app.router.add_post('/api/sessions/{session_id}/chat', adapter._handle_session_chat)
    app.router.add_post('/api/sessions/{session_id}/chat/stream', adapter._handle_session_chat_stream)
    app.router.add_post('/v1/runs', adapter._handle_runs)
    app.router.add_get('/v1/runs/{run_id}', adapter._handle_get_run)
    async with TestClient(TestServer(app)) as client:
        if door == 'run':
            resp = await client.post('/v1/runs', json={'input': 'ping', 'session_id': old, 'author': AUTHOR})
            body = await resp.json()
            assert resp.status == 202, body
            run_id = body['run_id']
            await async_wait_until(lambda: adapter._run_statuses[run_id]['status'] in
                                   ('completed', 'failed', 'cancelled'), timeout=10)
            body = await (await client.get('/v1/runs/' + run_id)).json()
            if not headless.executed:
                assert body['status'] == 'failed', body  # main refuses this migration
                return
            assert body['status'] == 'completed', body
            actual = headless.executed[0][0]
            assert body['session_id'] == actual, body
        else:
            path = f'/api/sessions/{old}/chat' + ('/stream' if door == 'stream' else '')
            resp = await client.post(path, json={'message': 'ping', 'author': AUTHOR})
            text = await resp.text()
            if not headless.executed:
                assert 'bot_chat_not_local' in text, text  # main refuses this migration
                return
            assert resp.status == 200, text
            actual = headless.executed[0][0]
            if door == 'chat':
                body = json.loads(text)
                assert body['session_id'] == actual, (body, dict(resp.headers))
            else:
                frames = [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: ')]
                assert {f['session_id'] for f in frames} == {actual}, (frames, dict(resp.headers))
