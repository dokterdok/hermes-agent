"""G1/G2: real stored policies, resume and mutation handlers, isolated profile."""
import json

import pytest

from tests.gateway.test_follow_profile_followups import home, _profile
from tests.gateway.test_session_follow_profile_config import _owner, _route


def frozen(authority, actor, home):
    from gateway.session_local import stage_local_session
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_local_title import title_new_session
    from hermes_state_local import commit_local_session
    staged = stage_local_session(authority, actor, {
        'request_id': 'old-build', 'source': 'cli', 'cwd': str(home)})
    commit_local_session(authority.db, epoch=authority.epoch, receipt=staged)
    ref = restore_local_session(authority, staged['session_id'])
    title_new_session(authority, ref, 'Bot Chat')
    return ref


async def rpc(owner, method, **params):
    reply = await owner.dispatch({'id': 'probe', 'method': method, 'params': params})
    assert 'result' in reply, reply
    return reply['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('reload_mcp', [False, True], ids=['control', 'mcp-reload'])
async def test_mcp_refresh_does_not_forget_busy_adoption(home, monkeypatch, reload_mcp):
    import hermes_state_mutation_guards as guards
    from gateway.session_policy_refresh import commit_refresh
    from hermes_state_runtime import RuntimeStoreError
    _profile(home, 'old-model', 1, 'old-key')
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    original, attempts = guards.require_idle, []

    def busy_once(db, conn, ids):
        attempts.append(ids)
        if len(attempts) == 1:
            raise RuntimeStoreError('session_busy')
        return original(db, conn, ids)

    monkeypatch.setattr(guards, 'require_idle', busy_once)
    try:
        ref = frozen(authority, owner.actor, home)
        _profile(home, 'new-model', 2, 'new-key')
        await rpc(owner, 'session.resume', session_id=ref.session_id)
        if reload_mcp:
            await commit_refresh(authority, ref, mcp_only=True)
        await rpc(owner, 'session.resume', session_id=ref.session_id)
        assert _route(runner, authority.sessions[ref.session_id]) == (
            'new-model', 'http://127.0.0.1:2/v1', 'new-key')
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
@pytest.mark.parametrize('creation', ['frozen', 'following'])
async def test_reasoning_choice_after_open_is_effective(home, creation):
    from gateway.session_policy import policy_for_source
    _profile(home, 'model', 1, 'key', agent={'reasoning_effort': 'low'})
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    runner._set_session_reasoning_override = lambda route, value: None
    try:
        if creation == 'frozen':
            sid = frozen(authority, owner.actor, home).session_id
        else:
            created = await rpc(owner, 'session.create', request_id='new', source='cli',
                                cwd=str(home), follow_profile_config=True, title='Bot Chat')
            sid = created['session_id']
        await rpc(owner, 'session.resume', session_id=sid)
        row = authority.db.get_session(sid)
        await rpc(owner, 'session.mutate', session_id=sid, request_id='high', operation='reasoning',
                  payload={'effort': 'high'}, expected_revision=row['runtime_revision'],
                  expected_generation=row['runtime_generation'])
        policy = policy_for_source(runner, authority.sessions[sid].source)
        assert json.loads(policy.config_json)['agent']['reasoning_effort'] == 'high'
        assert policy.current_reasoning_config(runner).get('effort') == 'high'
    finally:
        await owner.close()
        store.close_all_db_handles()
