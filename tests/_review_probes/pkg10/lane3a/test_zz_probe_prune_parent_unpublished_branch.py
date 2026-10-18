"""Probe G5: a prune that retires an ended PARENT while its branch is committed but not yet published.

The lane's own test (test_a_prune_keeps_the_parents_secret_for_a_branch_not_yet_published) prunes the
branch whose deleted parent named the slot; release-by-name protects that case. Here the pruned session
IS the slot's name owner, and the only session that still names the slot is the branch whose commit has
landed but whose ``restore_local_session`` has not run yet (absent from ``authority.sessions``). Prune
takes no session lock, so nothing serializes it with the branch's commit + publication.
"""
import asyncio
import copy
import threading

import pytest

# _branching_owner / _create copied from the lane's tests/gateway/test_launch_secret_retirement.py
# (bee90c2e12) so the probe also runs on main.
from types import SimpleNamespace


async def _branching_owner(tmp_path, monkeypatch):
    """An owner whose sessions carry a config secret, with the mutate and next-turn key helpers."""
    from gateway import run, session_mutations
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_policy import launch_key, policy_for_source
    config = {'platform_toolsets': {'cli': []}, 'model': {
        'provider': 'custom', 'api_key': 'config-secret', 'base_url': 'http://127.0.0.1:9/v1'}}
    monkeypatch.setattr(run, '_load_gateway_config', lambda *a, **k: config)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _evict_cached_agent=lambda route: None,
                             _set_session_reasoning_override=lambda route, value: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    owner = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='fixture')
    connection = AuthorityConnection(owner, object(), {'user_id': 'owner'})

    async def mutate(ref, operation, payload=None, request_id=None):
        handle = owner._handle(ref)
        return await session_mutations.mutate_session(owner, connection.actor, ref, dict(
            session_id=ref.session_id, request_id=request_id or operation + ref.session_id, operation=operation,
            payload=payload or {}, expected_revision=handle.revision,
            expected_generation=handle.execution_generation))

    def key(ref):
        return launch_key(owner, policy_for_source(runner, owner.sessions[ref.session_id].source))
    return owner, connection, mutate, key


def _create(owner, connection, tmp_path, api_key):
    from gateway.session_local import create_local_session
    return create_local_session(owner, connection.actor, {'request_id': 'parent', 'source': 'cli',
        'cwd': str(tmp_path), 'model': 'm', 'toolsets': [], **({'api_key': api_key} if api_key else {})})




@pytest.mark.asyncio
@pytest.mark.parametrize('api_key', ['launch-parent', None], ids=['launch-key', 'config-secret'])
async def test_a_prune_of_the_parent_keeps_the_secret_for_its_unpublished_branch(tmp_path, monkeypatch, api_key):
    from gateway import run, session_mutations
    from gateway.session_contract import SessionRef
    from gateway.session_policy import policy_for_source
    from gateway.session_prune import _apply
    owner, connection, mutate, key = await _branching_owner(tmp_path, monkeypatch)
    committed, release, pending = threading.Event(), threading.Event(), None
    try:
        parent = _create(owner, connection, tmp_path, api_key)
        owner.db.end_session(parent.session_id, 'tui_shutdown')  # an ended row, prunable
        rotated = copy.deepcopy(run._load_gateway_config())
        rotated['model']['api_key'] = 'rotated-config-secret'  # only the frozen copy still answers
        monkeypatch.setattr(run, '_load_gateway_config', lambda *a, **k: rotated)
        commit = session_mutations.mutate_runtime_session

        def held_publication(*args, **kwargs):
            result = commit(*args, **kwargs)
            if kwargs['request_id'] == 'held-branch' and not kwargs.get('_prepare_only'):
                committed.set()  # committed, not yet restored into the owner
                assert release.wait(10)
            return result
        monkeypatch.setattr(session_mutations, 'mutate_runtime_session', held_publication)
        pending = asyncio.create_task(mutate(parent, 'branch', request_id='held-branch'))
        assert await asyncio.to_thread(committed.wait, 10)
        pruned = await _apply(owner, {'older_than_days': None, 'chat_id': parent.session_id}, None)
        assert pruned['deleted'] == 1, pruned
        release.set()
        branch = SessionRef(parent.profile_id, (await pending)['branched_session_id'])
        if api_key:
            assert key(branch) == api_key  # head: RuntimeStoreError launch_credentials_unavailable
        else:
            assert policy_for_source(owner.runner, owner.sessions[branch.session_id].source).config(
                owner)['model']['api_key'] == 'config-secret'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await connection.close()
        owner.db.close()


@pytest.mark.asyncio
async def test_control_a_prune_of_the_parent_after_the_branch_is_published_keeps_the_key(tmp_path, monkeypatch):
    """Control: the same prune once the branch is published (the lane's fix covers it; main does not)."""
    from gateway.session_contract import SessionRef
    from gateway.session_prune import _apply
    owner, connection, mutate, key = await _branching_owner(tmp_path, monkeypatch)
    try:
        parent = _create(owner, connection, tmp_path, 'launch-parent')
        owner.db.end_session(parent.session_id, 'tui_shutdown')
        branch = SessionRef(parent.profile_id, (await mutate(parent, 'branch'))['branched_session_id'])
        assert (await _apply(owner, {'older_than_days': None, 'chat_id': parent.session_id}, None))['deleted'] == 1
        assert key(branch) == 'launch-parent'
    finally:
        await connection.close()
        owner.db.close()
