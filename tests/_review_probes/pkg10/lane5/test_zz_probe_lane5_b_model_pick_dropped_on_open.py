"""Bug-hunt probe (lane 5, MSG-22 ``follow_bot_chat``): a ``/model`` the user committed on a Bot Chat
an earlier build froze (the delivery door's chat) is the session's own choice. Opening the chat
adopts it as following; the pick must hold until the profile moves (the rule a following session's
own ``/model`` and adoption's composer pick get), not vanish on the open.

Self-contained (main and the lane head). Real SessionAuthority / AuthorityConnection over SQLite,
``session.mutate`` model, ``session.resume`` and the turn route resolver. ``switch_model`` is stubbed
(no endpoint is contacted).
"""
import json

import pytest

from tests.gateway.test_follow_profile_followups import _profile
from tests.gateway.test_session_follow_profile_config import _owner, _route


@pytest.fixture
def home(monkeypatch):
    from gateway import run
    from hermes_constants import get_hermes_home
    profile = get_hermes_home()
    profile.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', profile)
    return profile


def _frozen_bot_chat(authority, actor, home):
    from gateway.session_local import stage_local_session
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_local_title import title_new_session
    from hermes_state_local import commit_local_session
    receipt = stage_local_session(authority, actor, {'request_id': 'earlier-build', 'source': 'cli', 'cwd': str(home)})
    commit_local_session(authority.db, epoch=authority.epoch, receipt=receipt)
    ref = restore_local_session(authority, receipt['session_id'])
    title_new_session(authority, ref, 'Bot Chat')
    return ref


async def _open(authority, ref):
    from gateway.session_controls import AuthorityConnection
    desktop = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        response = await desktop.dispatch({'id': 'open', 'method': 'session.resume',
                                           'params': {'session_id': ref.session_id}})
        assert 'result' in response, response
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_committed_model_pick_survives_the_adopting_open(home, monkeypatch):
    from hermes_cli import model_switch
    from gateway.session_policy import policy_for_source
    monkeypatch.setattr(model_switch, 'switch_model', lambda **k: model_switch.ModelSwitchResult(
        success=True, new_model='picked-model', target_provider='custom', base_url='http://127.0.0.1:1/v1'))
    _profile(home, 'profile-model', 1, 'profile-key')
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    runner._set_session_reasoning_override = lambda route, value: None
    from gateway.run_turn_prepare import GatewayTurnPrepareMixin
    runner._resolve_session_agent_runtime = (
        lambda **k: GatewayTurnPrepareMixin._resolve_session_agent_runtime(runner, **k))
    try:
        ref = _frozen_bot_chat(authority, owner.actor, home)
        live = authority.sessions[ref.session_id]
        snap = authority.db.get_session(ref.session_id)
        response = await owner.dispatch({'id': 'pick', 'method': 'session.mutate', 'params': dict(
            session_id=ref.session_id, request_id='pick', operation='model', payload={'model': 'picked-model'},
            expected_revision=snap['runtime_revision'], expected_generation=snap['runtime_generation'])})
        assert 'result' in response, response
        assert _route(runner, live)[0] == 'picked-model'
        # Desktop opens the chat; the profile has not moved since the pick.
        await _open(authority, ref)
        policy = policy_for_source(runner, live.source)
        model = (json.loads(policy.config_json).get('model') or {})
        assert _route(runner, live)[0] == 'picked-model', (policy.follows_profile, model)
    finally:
        await owner.close()
        store.close_all_db_handles()
