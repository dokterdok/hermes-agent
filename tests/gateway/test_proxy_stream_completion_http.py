"""The real HTTP client must return before a completed proxy stream closes."""
import asyncio
import json
from unittest.mock import patch

import pytest
from aiohttp import web

from tests.gateway.test_proxy_mode import _make_runner, _make_source


@pytest.mark.asyncio
async def test_done_releases_the_real_http_stream_while_the_peer_keeps_it_open(monkeypatch):
    release = asyncio.Event()
    expected = 'Bonjour Zürich — 🧑🏽‍💻'

    async def respond(request):
        assert (await request.json())['stream'] is True
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        frame = json.dumps({'choices': [{'delta': {'content': expected}}]}, ensure_ascii=False)
        await response.write(('data:' + frame + '\n\ndata:[DONE]\n\n').encode())
        await release.wait()
        return response

    app = web.Application()
    app.router.add_post('/v1/chat/completions', respond)
    server = web.AppRunner(app)
    await server.setup()
    try:
        site = web.TCPSite(server, '127.0.0.1', 0)
        await site.start()
        port = server.addresses[0][1]
        monkeypatch.setenv('GATEWAY_PROXY_URL', f'http://127.0.0.1:{port}')
        monkeypatch.delenv('GATEWAY_PROXY_KEY', raising=False)
        with patch('gateway.run._load_gateway_config', return_value={}):
            result = await asyncio.wait_for(_make_runner()._run_agent_via_proxy(
                message='hi', context_prompt='', history=[], source=_make_source(), session_id='loopback'), 5)
        assert result['final_response'] == expected
        assert not release.is_set()
    finally:
        release.set()
        await server.cleanup()
