"""Probe G7: after a one-turn admission on a profile-following session, the published session.info
must pair the model and the provider of ONE profile version (the route the next turn runs)."""
import json
from types import SimpleNamespace

import pytest

from tests.gateway.test_follow_profile_followups import _create_bot_chat
from tests.gateway.test_session_follow_profile_config import _owner


@pytest.fixture
def home(monkeypatch):
    from gateway import run
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', home)
    return home


def _profile(home, provider, model, port):
    (home / 'config.yaml').write_text(json.dumps({'model': {
        'provider': provider, 'default': model, 'base_url': f'http://127.0.0.1:{port}/v1', 'api_key': 'k'}}))


@pytest.mark.asyncio
async def test_one_turn_settled_info_pairs_model_and_provider_of_one_profile_version(home):
    from gateway.session_cli_turn import cli_turn_scope
    from gateway.session_local_inventory import publish_settled_info
    from gateway.session_policy import policy_for_source
    _profile(home, 'anthropic', 'claude-a', 1)
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    try:
        live = await _create_bot_chat(owner, authority, home, 'follow')
        sid = next(s for s, v in authority.sessions.items() if v is live)
        _profile(home, 'openrouter', 'openai/gpt-b', 2)  # the profile moves Anthropic -> OpenRouter
        config, model = policy_for_source(runner, live.source).effective(authority, secrets=False)
        assert (model, config['model']['provider']) == ('openai/gpt-b', 'openrouter')  # the next turn's route
        published = []
        turn = SimpleNamespace(_ctx=SimpleNamespace(inbound_message_id='adm'), _approval_owner=(authority, sid, 1),
                               _runner=runner, _publish_execution=lambda kind, payload: published.append(payload))
        once_agent = SimpleNamespace(model='once-model', provider='nous', tools=None, _cached_system_prompt='',
                                     context_compressor=None)
        with cli_turn_scope('adm', {'model_override': {'model': 'once-model', 'provider': 'nous'}}):
            publish_settled_info(turn, once_agent)
        assert published, 'no session.info'
        info = published[-1]
        assert (info.get('model'), info.get('provider')) == ('openai/gpt-b', 'openrouter'), info
    finally:
        await owner.close()
        store.close_all_db_handles()
