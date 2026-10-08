"""Retirement must fence queued work before joining its preparation task."""
import asyncio

import pytest

from gateway import run_runtime, session_finite
from gateway.session_contract import Submission
from hermes_state_runtime import list_session_admissions
from tests.gateway.test_profile_retire_stops_turns import ACTOR, REF, _Agent, _authority


@pytest.mark.asyncio
async def test_queued_follower_cannot_start_while_retirement_waits_for_preparation(tmp_path, monkeypatch):
    agent = _Agent()
    db, authority, execute, returned = _authority(tmp_path, agent)
    monkeypatch.setattr(session_finite, 'execute_finite_admission', execute)
    monkeypatch.setattr('gateway.session_cron.unbind_owner', lambda authority: None)
    release_preparation = asyncio.Event()
    authority._logical_preparation_stopped = asyncio.Event()
    authority._logical_preparation_state = 'running'
    authority._logical_preparation_task = asyncio.create_task(release_preparation.wait())
    retiring = None
    try:
        await authority.submit(ACTOR, Submission(request_id='running', ref=REF, payload={'text': 'a'}, intent='queue'))
        await authority.submit(ACTOR, Submission(request_id='follower', ref=REF, payload={'text': 'b'}, intent='queue'))
        assert await asyncio.wait_for(asyncio.to_thread(agent.started.wait, 5), 6)
        retiring = asyncio.create_task(run_runtime._retire_profile_authority(authority))
        await asyncio.wait_for(authority._logical_preparation_stopped.wait(), 5)
        # The first turn finishes naturally while the actual preparation join is blocked.
        agent.release.set()
        await asyncio.wait_for(authority.sessions['s'].task, 5)
        rows = {row['request_id']: row for row in list_session_admissions(db, session_id='s', pending_only=False)}
        assert rows['running']['status'] == 'terminal'
        assert rows['follower']['status'] == 'queued', {
            'retiring': authority.retiring, 'follower': rows['follower']['status']}
    finally:
        agent.release.set()
        release_preparation.set()
        if retiring is not None:
            await asyncio.wait_for(retiring, 10)
        else:
            await authority._logical_preparation_task
        db.close()
