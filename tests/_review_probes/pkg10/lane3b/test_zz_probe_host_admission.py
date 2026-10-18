"""Concurrent private IPC requests must not share the route-local admission decision."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace


def test_concurrent_admitted_and_anonymous_requests_keep_separate_guards(tmp_path):
    from hermes_cli.plugin_host import PluginHost
    home, api = tmp_path / 'home', tmp_path / 'api'
    home.mkdir()
    api.mkdir()
    (api / 'plugin_api.py').write_text('''
import asyncio
from fastapi import APIRouter, Request
from hermes_cli.web_deps import late
router = APIRouter()
arrivals = 0
ready = asyncio.Event()
@router.get('/ping')
async def ping():
    return {'ok': True}
@router.put('/config')
async def config(request: Request):
    global arrivals
    arrivals += 1
    if arrivals >= 2:
        ready.set()
    await asyncio.wait_for(ready.wait(), 10)
    late('_require_token')(request)
    return {'saved': True}
''', encoding='utf-8')
    host = PluginHost(SimpleNamespace(home_path=home, _plugins={}))
    def call(authenticated):
        return host.asgi_request('review', str(api), 'plugin_api.py', 'PUT', '/config',
                                 'authenticated=true', [('X-Hosted-Http-Authenticated', 'true')],
                                 b'{"authenticated":true}', timeout=20, authenticated=authenticated)
    try:
        assert host.asgi_request('review', str(api), 'plugin_api.py', 'GET', '/ping', '', [], b'')['status'] == 200
        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted, anonymous = pool.submit(call, True), pool.submit(call, False)
            assert [admitted.result()['status'], anonymous.result()['status']] == [200, 401]
        assert call(False)['status'] == 401
    finally:
        host.shutdown()
