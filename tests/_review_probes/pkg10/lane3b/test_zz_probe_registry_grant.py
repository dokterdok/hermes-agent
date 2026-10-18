"""Native HTTP grant review using real request authentication and real profile resolution."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from gateway.runtime_bootstrap import TicketStore
    from gateway.session_authorities import SessionAuthorities
    from hermes_cli import profiles
    from hermes_constants import get_hermes_home
    home = get_hermes_home().resolve()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(profiles, '_get_default_hermes_home', lambda: home)
    monkeypatch.setattr(profiles, '_get_profiles_root', lambda: home / 'profiles')
    authorities = SessionAuthorities(home, multiplexed=True)
    for name in ('default', 'beta', 'gamma', 'active', 'import', 'sessions'):
        path = home if name == 'default' else home / 'profiles' / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'config.yaml').write_text('{}')
        authorities.add(path, SimpleNamespace(profile_id=str(path), instance_id='review'))
    store = TicketStore('review', authorities.profile_ids())
    app = FastAPI()
    app.state.gateway_runner = SimpleNamespace(
        session_authorities=authorities, session_ticket_store=store, _draining=False,
        session_runtime_descriptor={'state': 'ready', 'instance_id': 'review',
                                    'served_profiles': authorities.served_profiles()})
    return home, store, app


def grant(registry, profile, purpose=None):
    from gateway.runtime_bootstrap import _PURPOSE_CAPABILITIES
    home, store, app = registry
    # Main predates the registry purpose: compare its supported Desktop native-http path.
    purpose = purpose or ('native-profile-registry' if 'native-profile-registry' in _PURPOSE_CAPABILITIES
                          else 'native-http')
    target = home if profile == 'default' else home / 'profiles' / profile
    return store.mint(profile_id=str(target), subject='review', purpose=purpose)


async def authenticate(registry, ticket, method, path, query=b'', extra_headers=()):
    from hermes_cli.dashboard_auth.native_http import authenticate_native_http
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    request = Request({'type': 'http', 'method': method, 'path': path, 'query_string': query,
                       'headers': [(b'x-hermes-gateway-ticket', ticket.encode()), *extra_headers],
                       'app': registry[2], 'hermes.gateway_socket_peer': ('127.0.0.1', 5000)}, receive)
    response = await authenticate_native_http(request)
    return 200 if response is None else response.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['active', 'import'])
@pytest.mark.parametrize('method', ['PATCH', 'DELETE'])
@pytest.mark.parametrize('purpose', ['native-http', None], ids=['desktop', 'registry'])
async def test_valid_collection_like_profile_is_mutable(registry, name, method, purpose):
    status = await authenticate(registry, grant(registry, name, purpose), method, f'/api/profiles/{name}')
    assert status == 200, (name, method, purpose, status)


@pytest.mark.asyncio
async def test_registry_grant_scope_method_replay_and_launch_home(registry):
    from gateway.runtime_bootstrap import _PURPOSE_CAPABILITIES
    if 'native-profile-registry' not in _PURPOSE_CAPABILITIES:
        pytest.skip('Registry purpose does not exist on main')
    assert await authenticate(registry, grant(registry, 'beta'), 'DELETE', '/api/profiles/gamma') == 403
    assert await authenticate(registry, grant(registry, 'beta'), 'PATCH', '/api/profiles/beta', b'profile=gamma') == 403
    assert await authenticate(registry, grant(registry, 'beta'), 'GET', '/api/profiles/beta/soul') == 401
    assert await authenticate(registry, grant(registry, 'beta'), 'PUT', '/api/profiles/beta') == 401
    assert await authenticate(registry, grant(registry, 'beta'), 'PATCH', '/api/profiles/beta/soul') == 401
    assert await authenticate(registry, grant(registry, 'beta', 'native-http'), 'DELETE', '/api/profiles/beta') == 403
    ticket = grant(registry, 'beta')
    assert await authenticate(registry, ticket, 'PATCH', '/api/profiles/beta') == 200
    assert await authenticate(registry, ticket, 'DELETE', '/api/profiles/beta') == 401
    assert await authenticate(registry, grant(registry, 'default'), 'DELETE', '/api/profiles/default') == 403
    assert await authenticate(registry, grant(registry, 'default'), 'PATCH', '/api/profiles/default') == 200
    assert await authenticate(registry, grant(registry, 'beta'), 'PATCH', '/api/profiles/beta',
                              extra_headers=[(b'origin', b'http://localhost')]) == 401
    authorities = registry[2].state.gateway_runner.session_authorities
    authorities.launch_key = next(key for key, item in authorities._by_key.items()
                                  if item.profile_id == str(registry[0] / 'profiles' / 'beta'))
    assert await authenticate(registry, grant(registry, 'beta'), 'PATCH', '/api/profiles/beta') == 403
