"""Review probes (lane 3b, DB-5/DB-6): the native grant gate in front of /api/profiles/<name>.

Real ``TicketStore`` (gateway/runtime_bootstrap.py), real ``authenticate_native_http`` /
``_check_profile_scope`` / ``_own_profile`` against a temp HERMES_HOME with real profile dirs; only the
gateway runner is a stand-in carrying the attributes ``_native_store`` reads (descriptor, store,
authorities, drain flag). The FastAPI route table is the real dashboard app's.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

INSTANCE = 'inst-review'


@pytest.fixture
def gate(tmp_path, monkeypatch):
    root = tmp_path / '.hermes'
    root.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    (root / 'config.yaml').write_text('{}', encoding='utf-8')
    names = ('beta', 'gamma', 'work', 'active', 'import', 'sessions')
    for name in names:
        (root / 'profiles' / name).mkdir(parents=True)
        (root / 'profiles' / name / 'config.yaml').write_text('{}', encoding='utf-8')

    def home_of(name):
        return str((root if name == 'default' else root / 'profiles' / name).resolve())

    def build(launch='default'):
        from gateway.runtime_bootstrap import TicketStore
        from gateway.session_authorities import SessionAuthorities
        order = [launch] + [n for n in ('default', *names) if n != launch]
        registry = SessionAuthorities(Path(home_of(launch)))
        for name in order:
            registry.add(Path(home_of(name)), SimpleNamespace(profile_id=home_of(name), instance_id=INSTANCE),
                         name=None if name == launch else name)
        ids = [home_of(n) for n in order]
        store = TicketStore(INSTANCE, frozenset(ids))
        runner = SimpleNamespace(
            session_authorities=registry, session_ticket_store=store, _draining=False,
            session_runtime_descriptor={'state': 'ready', 'instance_id': INSTANCE,
                                        'served_profiles': [{'profile_id': p, 'home': p} for p in ids]})
        return runner

    state = SimpleNamespace(runner=build(), home_of=home_of, root=root, build=build)

    def ticket(profile, purpose):
        return state.runner.session_ticket_store.mint(profile_id=home_of(profile), subject='uid:1', purpose=purpose)

    def call(method, path, ticket_value, query=b''):
        from starlette.requests import Request
        from hermes_cli.dashboard_auth.native_http import authenticate_native_http

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        scope = {'type': 'http', 'method': method, 'path': path, 'raw_path': path.encode(), 'root_path': '',
                 'query_string': query, 'scheme': 'http', 'server': ('127.0.0.1', 9119),
                 'client': ('127.0.0.1', 50000), 'hermes.gateway_socket_peer': ('127.0.0.1', 50000),
                 'headers': [(b'host', b'127.0.0.1:9119'), (b'x-hermes-gateway-ticket', ticket_value.encode())],
                 'app': SimpleNamespace(state=SimpleNamespace(gateway_runner=state.runner))}
        request = Request(scope, receive)
        rejection = asyncio.run(authenticate_native_http(request))
        return 200 if rejection is None else rejection.status_code

    state.ticket, state.call = ticket, call
    return state


def _route_for(method, path):
    """Name of the dashboard endpoint the real app dispatches *method path* to."""
    from starlette.routing import Match
    from hermes_cli.web_server import app
    scope = {'type': 'http', 'method': method, 'path': path, 'root_path': '', 'query_string': b'', 'headers': []}
    for route in app.router.routes:
        match, child = route.matches(scope)
        if match == Match.FULL:
            return route.endpoint.__name__, child.get('path_params')
    return None


REGISTRY = 'native-profile-registry'


# G1 ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['active', 'import'])
@pytest.mark.parametrize('method', ['PATCH', 'DELETE'])
def test_g1_profile_named_like_a_collection_route_can_be_renamed_and_deleted(gate, name, method):
    from hermes_cli.profiles import validate_profile_name
    validate_profile_name(name)  # a valid, creatable profile id
    endpoint = {'PATCH': 'rename_profile_endpoint', 'DELETE': 'delete_profile_endpoint'}[method]
    assert _route_for(method, f'/api/profiles/{name}') == (endpoint, {'name': name})
    # Some native grant for that very profile must admit its rename/delete (as for every other name).
    admitted = {}
    for purpose in ('native-http', REGISTRY):
        try:
            ticket = gate.ticket(name, purpose)
        except PermissionError:  # main: no registry purpose exists yet
            continue
        admitted[purpose] = gate.call(method, f'/api/profiles/{name}', ticket)
    assert 200 in admitted.values(), admitted


# Sibling of G1 -------------------------------------------------------------------------------------

@pytest.mark.parametrize('method', ['PATCH', 'DELETE'])
def test_ordinary_grant_for_another_profile_cannot_rename_or_delete_profile_named_sessions(gate, method):
    """PR claim: "an ordinary native-http grant keeps refusing both writes", and a grant for X never acts on Y."""
    endpoint = {'PATCH': 'rename_profile_endpoint', 'DELETE': 'delete_profile_endpoint'}[method]
    assert _route_for(method, '/api/profiles/sessions') == (endpoint, {'name': 'sessions'})
    status = gate.call(method, '/api/profiles/sessions', gate.ticket('default', 'native-http'),
                       query=b'profile=default')
    assert status in (401, 403), status


# What holds up --------------------------------------------------------------------------------------

def test_registry_grant_scope_single_use_and_launch_home(gate):
    call, ticket = gate.call, gate.ticket
    # Bound to the profile it names: path, ?profile= and a cross-profile target all refuse.
    assert call('DELETE', '/api/profiles/gamma', ticket('beta', REGISTRY)) == 403
    assert call('PATCH', '/api/profiles/beta', ticket('beta', REGISTRY), query=b'profile=gamma') == 403
    assert call('PATCH', '/api/profiles/Beta', ticket('beta', REGISTRY)) == 403
    # Bound to the two registry writes: any other route or method is 401 (not redeemable there).
    for method, path in (('GET', '/api/profiles/beta'), ('GET', '/api/profiles/beta/soul'),
                         ('PUT', '/api/profiles/beta/soul'), ('POST', '/api/profiles'),
                         ('POST', '/api/profiles/beta/export'), ('PATCH', '/api/profiles/beta/model'),
                         ('GET', '/api/config')):
        assert call(method, path, ticket('beta', REGISTRY)) == 401, (method, path)
    # Single use; a wrong-purpose attempt does not burn it.
    once = ticket('beta', REGISTRY)
    assert call('GET', '/api/config', once) == 401
    assert call('PATCH', '/api/profiles/beta/', once) == 200
    assert call('PATCH', '/api/profiles/beta', once) == 401
    # A native-http grant for the path's own profile stays refused for both writes.
    assert call('PATCH', '/api/profiles/beta', ticket('beta', 'native-http')) == 403
    assert call('DELETE', '/api/profiles/beta', ticket('beta', 'native-http')) == 403
    # Launch (default root) home: display-name rename only, never delete.
    assert call('PATCH', '/api/profiles/default', ticket('default', REGISTRY)) == 200
    assert call('DELETE', '/api/profiles/default', ticket('default', REGISTRY)) == 403


def test_registry_grant_never_moves_or_deletes_a_named_launch_home(gate):
    gate.runner = gate.build(launch='work')
    assert gate.call('PATCH', '/api/profiles/work', gate.ticket('work', REGISTRY)) == 403
    assert gate.call('DELETE', '/api/profiles/work', gate.ticket('work', REGISTRY)) == 403
    assert gate.call('DELETE', '/api/profiles/beta', gate.ticket('beta', REGISTRY)) == 200
