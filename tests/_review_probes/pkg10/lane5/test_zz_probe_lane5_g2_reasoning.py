"""G2 probe (lane 5, MSG-22): ``/reasoning <level>`` committed on a Bot Chat that follows its profile
must stay the session's level, not the profile's ``agent.reasoning_effort``.

Self-contained (runs on main, the lane head and the composed tree). Real: SessionAuthority /
AuthorityConnection over SQLite, ``session.resume`` and ``session.mutate`` reasoning, the policy's
``current_reasoning_config``. No endpoint is contacted.
"""
import json

import pytest

from tests.gateway.test_follow_profile_followups import _create_bot_chat, _profile
from tests.gateway.test_session_follow_profile_config import _owner


@pytest.fixture
def home(monkeypatch):
    from gateway import run
    from hermes_constants import get_hermes_home
    profile = get_hermes_home()
    profile.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, '_hermes_home', profile)
    return profile


def _frozen_bot_chat(authority, actor, home):
    """A Bot Chat an earlier build committed without ``follow_profile_config`` (its delivery door)."""
    from gateway.session_local import stage_local_session
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_local_title import title_new_session
    from hermes_state_local import commit_local_session
    receipt = stage_local_session(authority, actor, {'request_id': 'earlier-build', 'source': 'cli', 'cwd': str(home)})
    commit_local_session(authority.db, epoch=authority.epoch, receipt=receipt)
    ref = restore_local_session(authority, receipt['session_id'])
    title_new_session(authority, ref, 'Bot Chat')
    return ref


async def _reasoning(owner, authority, sid, effort, request_id):
    snap = authority.db.get_session(sid)
    response = await owner.dispatch({'id': request_id, 'method': 'session.mutate', 'params': dict(
        session_id=sid, request_id=request_id, operation='reasoning', payload={'effort': effort},
        expected_revision=snap['runtime_revision'], expected_generation=snap['runtime_generation'])})
    assert 'result' in response, response


async def _open(authority, ref):
    from gateway.session_controls import AuthorityConnection
    desktop = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        response = await desktop.dispatch({'id': 'open', 'method': 'session.resume',
                                           'params': {'session_id': ref.session_id}})
        assert 'result' in response, response
    finally:
        await desktop.close()


def _effort(runner, live):
    from gateway.session_policy import policy_for_source
    policy = policy_for_source(runner, live.source)
    return (policy.current_reasoning_config(runner).get('effort'), policy.follows_profile,
            json.loads(policy.config_json).get('session_pins'))


def _rig(home):
    _profile(home, 'old-model', 1, 'old-key', agent={'reasoning_effort': 'low'})
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    runner._set_session_reasoning_override = lambda route, value: None
    return store, runner, authority, owner


@pytest.mark.asyncio
async def test_g2_reasoning_after_open_adoption_stays_the_sessions(home):
    """A frozen Bot Chat (an earlier build's delivery door), opened by Desktop, then ``/reasoning high``."""
    store, runner, authority, owner = _rig(home)
    try:
        ref = _frozen_bot_chat(authority, owner.actor, home)
        live = authority.sessions[ref.session_id]
        await _open(authority, ref)
        await _reasoning(owner, authority, ref.session_id, 'high', 'high-after-open')
        assert _effort(runner, live)[0] == 'high', _effort(runner, live)
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_g2_reasoning_on_a_delivery_minted_bot_chat_stays_the_sessions(home):
    """The Bot/cron delivery door mints the Bot Chat (``session_bot._target``), then ``/reasoning high``."""
    from gateway.session_bot import _target
    store, runner, authority, owner = _rig(home)
    try:
        ref, live, _entry = await _target(authority, owner.actor)
        await _reasoning(owner, authority, ref.session_id, 'high', 'high-on-minted')
        assert _effort(runner, live)[0] == 'high', _effort(runner, live)
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_g2_reasoning_on_a_desktop_created_bot_chat_stays_the_sessions(home):
    """Desktop's own create (``follow_profile_config=True``, also on main), then ``/reasoning high``."""
    store, runner, authority, owner = _rig(home)
    try:
        live = await _create_bot_chat(owner, authority, home, 'gui-first', title='Bot Chat', hidden=True)
        sid = next(s for s, v in authority.sessions.items() if v is live)
        await _reasoning(owner, authority, sid, 'high', 'high-on-desktop')
        assert _effort(runner, live)[0] == 'high', _effort(runner, live)
    finally:
        await owner.close()
        store.close_all_db_handles()
