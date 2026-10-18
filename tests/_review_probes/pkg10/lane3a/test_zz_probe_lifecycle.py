"""Independent lane3a review: accepted settings and branch commit/publication races."""
import asyncio
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_accepted_busy_reasoning_survives_owner_restart(tmp_path, monkeypatch):
    from tests.gateway.test_session_mutation_model_busy import _owner
    from gateway.session_contract import Submission
    from gateway.session_authority import SessionAuthority
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_policy import policy_for_source
    import hermes_state_runtime as rt
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(event):
        started.set()
        await release.wait()
        return 'ok'

    monkeypatch.chdir(tmp_path)
    store, db, authority, owner, ref = _owner(tmp_path, monkeypatch, handle)
    authority.runner._set_session_reasoning_override = lambda *a: None
    authority.runner._apply_reasoning_selection = lambda *a, **k: 'ok'
    try:
        await authority.submit(owner.actor, Submission('running', ref, {'text': 'RUNNING'}, 'queue'))
        await asyncio.wait_for(started.wait(), 10)
        reply = await owner.dispatch({'id': 1, 'method': 'config.set', 'params': {
            'session_id': ref.session_id, 'key': 'reasoning', 'value': 'high', 'scope': 'session'}})
        assert reply.get('result', {}).get('value') == 'high', reply
        # New owner epoch before the still-running old turn can reach its boundary.
        runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
            _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
            _peek_session_state=lambda route: None)
        runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
        fresh = SessionAuthority(runner, profile_id=authority.profile_id, instance_id='restarted', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='restarted'))
        runner.session_authority = fresh
        restore_local_session(fresh, ref.session_id)
        effort = (policy_for_source(runner, fresh.sessions[ref.session_id].source).reasoning_config or {}).get('effort')
    finally:
        release.set()
        await owner.close()
        store.close_all_db_handles()
    assert effort == 'high', effort


@pytest.mark.asyncio
async def test_prune_of_slot_owner_preserves_committed_unpublished_branch(tmp_path, monkeypatch):
    from gateway import run, session_mutations
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from gateway.session_contract import SessionRef
    from gateway.session_policy import launch_key, policy_for_source
    from gateway.session_prune import _apply
    monkeypatch.setattr(run, '_load_gateway_config', lambda *a, **k: {'model': {
        'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'config-key'}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
        _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='fixture')
    owner = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    entered, release, pending = threading.Event(), threading.Event(), None
    try:
        parent = create_local_session(authority, owner.actor, {'request_id': 'parent', 'source': 'cli',
            'cwd': str(tmp_path), 'model': 'm', 'toolsets': [], 'api_key': 'launch-parent'})
        authority.db.end_session(parent.session_id, 'tui_shutdown')
        real = session_mutations.mutate_runtime_session

        def hold(*args, **kwargs):
            result = real(*args, **kwargs)
            if kwargs['request_id'] == 'held-branch' and not kwargs.get('_prepare_only'):
                entered.set()
                assert release.wait(20)
            return result

        monkeypatch.setattr(session_mutations, 'mutate_runtime_session', hold)
        handle = authority._handle(parent)
        pending = asyncio.create_task(session_mutations.mutate_session(authority, owner.actor, parent, {
            'session_id': parent.session_id, 'request_id': 'held-branch', 'operation': 'branch', 'payload': {},
            'expected_revision': handle.revision, 'expected_generation': handle.execution_generation}))
        assert await asyncio.to_thread(entered.wait, 20)
        result = await _apply(authority, {'older_than_days': None, 'chat_id': parent.session_id}, None)
        assert result['deleted'] == 1
        release.set()
        child = SessionRef(parent.profile_id, (await pending)['branched_session_id'])
        policy = policy_for_source(runner, authority.sessions[child.session_id].source)
        assert launch_key(authority, policy) == 'launch-parent'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await owner.close()
        store.close_all_db_handles()


def test_once_agent_released_after_turn_slot_clears(monkeypatch):
    from gateway.run_agent_cache import GatewayAgentCacheMixin
    from gateway.session_cli_turn import cli_turn_scope
    from gateway.session_local_inventory import publish_settled_info
    from gateway import session_local, session_policy
    from gateway.session_state import SessionState
    released = []
    agent = SimpleNamespace(model='once', provider='openai', tools=None, _cached_system_prompt='',
        context_compressor=None, release_clients=lambda: released.append(True))
    state = SessionState()
    state.turn.agent = agent

    class Runner(GatewayAgentCacheMixin):
        _agent_cache_lock = None

        def _peek_session_state(self, route):
            return state

        def _running_agent_ids(self):
            return {id(state.turn.agent)} if state.turn.agent is not None else set()

        def _persist_active_agents(self):
            pass

        def _spawn_release_thread(self, target, args, *a, **k):
            target(*args)

    runner = Runner()
    runner._agent_cache = {'route': (agent, 'signature')}
    authority = SimpleNamespace(profile_id='p', sessions={'s': SimpleNamespace(route='route', source=object())})
    monkeypatch.setattr(session_local, 'local_session_info', lambda *a: {})
    monkeypatch.setattr(session_policy, 'policy_for_source', lambda *a: SimpleNamespace(
        current_model=lambda authority: 'normal', provider='openrouter'))
    turn = SimpleNamespace(_ctx=SimpleNamespace(inbound_message_id='adm'), _approval_owner=(authority, 's', 1),
        _runner=runner, _publish_execution=lambda *a: None)
    with cli_turn_scope('adm', {'model_override': {'model': 'once'}}):
        publish_settled_info(turn, agent)
    runner._release_running_agent_state('route')
    # Eventual TTL/next-route eviction is still able to release a cached agent on main.
    runner._evict_cached_agent('route')
    assert released == [True], released
