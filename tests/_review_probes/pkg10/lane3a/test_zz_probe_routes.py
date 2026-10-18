"""Independent lane3a review: real route resolver, CLI boundary, and publication."""
import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_user(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))


def test_direct_alias_once_keeps_its_own_endpoint_and_key(tmp_path, monkeypatch):
    from gateway.session_cli_turn import one_turn_runtime
    from hermes_cli.model_switch import switch_model
    from tests.gateway.test_session_mutation_model_route import _Catalog, MODELS
    from http.server import ThreadingHTTPServer
    import threading
    from agent import models_dev

    servers = [ThreadingHTTPServer(('127.0.0.1', 0), _Catalog) for _ in range(2)]
    for server in servers:
        server.received = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
    old, alias = [f'http://127.0.0.1:{s.server_port}/v1' for s in servers]
    config = {'model': {'provider': 'custom', 'default': 'original', 'base_url': old, 'api_key': 'old-key'},
              'model_aliases': {'review-alias': {'provider': 'custom', 'model': 'picked',
                                               'base_url': alias, 'api_key': 'alias-key'}}}
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'config.yaml').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(models_dev, 'fetch_models_dev', lambda *a, **k: {
        'custom': {'id': 'custom', 'models': {name: {'id': name, 'tool_call': True,
            'limit': {'context': 100000, 'output': 4000}} for name in MODELS}}})
    try:
        resolved = switch_model(raw_input='review-alias', current_provider='custom', current_model='original',
                                current_base_url=old, current_api_key='old-key', is_global=False)
        assert resolved.success, resolved.error_message
        assert (resolved.target_provider, resolved.base_url, resolved.api_key) == ('custom', alias, 'alias-key')
        model, runtime = one_turn_runtime({'model': 'review-alias'}, 'original',
            {'provider': 'custom', 'base_url': old, 'api_key': 'old-key', 'api_mode': 'chat_completions'}, config)
        assert (model, runtime['base_url'], runtime['api_key']) == ('picked', alias, 'alias-key')
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


class Connection:
    def __init__(self):
        import queue
        from hermes_cli.gateway_connection import SessionState
        self.events = queue.Queue()
        self.calls = []
        self.closed = self.final = False
        self.session = SessionState('s')
        self.session.info = {'model': 'old/model', 'provider': 'openrouter', 'reasoning_effort': 'high'}

    def state(self, sid):
        return self.session

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'session.resume':
            return {'revision': 1, 'execution_generation': 0, 'info': self.session.info,
                    'messages': [], 'prompts': [], 'pending': [], 'session_id': 's'}
        if method == 'session.mutate':
            if params['operation'] == 'reasoning':
                self.session.info['reasoning_effort'] = params['payload']['effort']
            return {}
        from hermes_cli.gateway_client import GatewayClientError
        raise GatewayClientError('unsupported_method')


def test_once_reasoning_equal_to_inherited_level_does_not_pin(monkeypatch):
    from hermes_cli.gateway_agent import GatewayAgent
    from hermes_cli.cli_model_switch_mixin import _commit_model_switch
    from hermes_cli.model_switch import ModelSwitchResult
    from tests.hermes_cli.test_cli_model_once import _StubCLI
    import cli
    conn = Connection()
    stub = _StubCLI()
    stub.agent = GatewayAgent(connection=conn, session_id='s', model='old/model', provider='openrouter',
                              reasoning_config={'enabled': True, 'effort': 'high'})
    stub.reasoning_config = {'enabled': True, 'effort': 'high'}
    stub._reasoning_user_set = False
    stub._snapshot_model_runtime = cli.HermesCLI._snapshot_model_runtime.__get__(stub)
    monkeypatch.setattr(cli, '_cprint', lambda *a, **k: None)
    monkeypatch.setattr('hermes_cli.model_switch.resolve_display_context_length', lambda *a, **k: None)
    result = ModelSwitchResult(success=True, new_model='next/model', target_provider='openrouter',
                              api_key='key', base_url=stub.base_url, api_mode='chat_completions')
    _commit_model_switch(stub, result, persist_global=False, one_turn=True, reasoning_effort='high')
    cli.HermesCLI._restore_model_runtime_snapshot(stub, stub._pending_one_turn_model_restore)
    reasoning_writes = [p for m, p in conn.calls if m == 'session.mutate' and p['operation'] == 'reasoning']
    assert reasoning_writes == [], reasoning_writes


@pytest.mark.asyncio
async def test_settled_info_uses_current_profile_provider(tmp_path, monkeypatch):
    from gateway import run
    from gateway.session_cli_turn import cli_turn_scope
    from gateway.session_local_inventory import publish_settled_info
    from tests.gateway.test_session_follow_profile_config import _owner, _create
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', home)
    (home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'anthropic', 'default': 'old'}}))
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    try:
        await _create(owner, authority, home, 'follow')
        sid = next(iter(authority.sessions))
        (home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'openrouter', 'default': 'new'}}))
        published = []
        turn = SimpleNamespace(_ctx=SimpleNamespace(inbound_message_id='adm'), _approval_owner=(authority, sid, 1),
            _runner=runner, _publish_execution=lambda kind, payload: published.append(payload))
        agent = SimpleNamespace(model='temporary', provider='openrouter', tools=None,
                                _cached_system_prompt='', context_compressor=None)
        with cli_turn_scope('adm', {'model_override': {'model': 'temporary'}}):
            publish_settled_info(turn, agent)
        assert published
        assert (published[-1]['model'], published[-1]['provider']) == ('new', 'openrouter')
    finally:
        await owner.close()
        store.close_all_db_handles()
