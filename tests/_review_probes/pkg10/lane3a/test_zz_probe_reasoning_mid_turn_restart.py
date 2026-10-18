"""Probe G3: a ``config.set reasoning`` acknowledged while a turn runs lives only in memory
(``live.pending_reasoning`` + the handler's override) until that turn settles. An owner restart before
then loses it, although the reply said it was set; a deferred model pick is durable (state_meta)."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.gateway.test_session_mutation_model_busy import _owner
from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_reasoning_set_during_a_turn_survives_an_owner_restart(tmp_path, monkeypatch):
    from gateway.session_authority import SessionAuthority
    from gateway.session_contract import Submission
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_policy import policy_for_source
    import hermes_state_runtime as rt
    started, release = threading.Event(), asyncio.Event()

    async def handle(event):
        if event.text == 'RUNNING':
            started.set()
            await release.wait()
        return 'ok'
    monkeypatch.chdir(tmp_path)  # the handler's profile scope is the owner's relative profile id
    store, db, authority, owner, ref = _owner(tmp_path, monkeypatch, handle)
    authority.runner._set_session_reasoning_override = lambda *a: None
    authority.runner._apply_reasoning_selection = lambda route, platform, value, persist_global=False: 'ok'
    try:
        await authority.submit(owner.actor, Submission('run', ref, {'text': 'RUNNING'}, 'queue'))
        await async_wait_until(started.is_set, timeout=10, what='a turn is running')
        reply = await owner.dispatch({'id': 1, 'method': 'config.set', 'params': {
            'session_id': ref.session_id, 'key': 'reasoning', 'value': 'minimal', 'scope': 'session'}})
        assert reply.get('result', {}).get('value') == 'minimal', reply  # acknowledged
        # The owner process dies here, before the running turn settles. A fresh owner on the same store:
        runner2 = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                                  _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                                  _handle_message=handle)
        runner2._adapter_for_source = lambda source: runner2.adapters.get(source.platform)
        fresh = SessionAuthority(runner2, profile_id='owned', instance_id='owner-restarted', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner-restarted'))
        restore_local_session(fresh, ref.session_id)
        policy = policy_for_source(runner2, fresh.sessions[ref.session_id].source)
        assert (policy.current_reasoning_config(runner2) or {}).get('effort') == 'minimal'
    finally:
        release.set()
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_control_reasoning_set_while_idle_survives_an_owner_restart(tmp_path, monkeypatch):
    """Control for the probe above: the same harness, the same restart, an idle session."""
    from gateway.session_authority import SessionAuthority
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_policy import policy_for_source
    import hermes_state_runtime as rt

    async def handle(event):
        return 'ok'
    monkeypatch.chdir(tmp_path)
    store, db, authority, owner, ref = _owner(tmp_path, monkeypatch, handle)
    authority.runner._set_session_reasoning_override = lambda *a: None
    authority.runner._apply_reasoning_selection = lambda route, platform, value, persist_global=False: 'ok'
    try:
        reply = await owner.dispatch({'id': 1, 'method': 'config.set', 'params': {
            'session_id': ref.session_id, 'key': 'reasoning', 'value': 'minimal', 'scope': 'session'}})
        assert reply.get('result', {}).get('value') == 'minimal', reply
        runner2 = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                                  _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                                  _handle_message=handle)
        runner2._adapter_for_source = lambda source: runner2.adapters.get(source.platform)
        fresh = SessionAuthority(runner2, profile_id='owned', instance_id='owner-restarted', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner-restarted'))
        restore_local_session(fresh, ref.session_id)
        policy = policy_for_source(runner2, fresh.sessions[ref.session_id].source)
        assert (policy.current_reasoning_config(runner2) or {}).get('effort') == 'minimal'
    finally:
        await owner.close()
        store.close_all_db_handles()
