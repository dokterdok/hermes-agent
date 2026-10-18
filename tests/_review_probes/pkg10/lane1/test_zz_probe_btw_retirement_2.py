"""G3 probe: profile retirement must not be held by a running ``/btw`` side question.

``session_tui_side.btw`` spawns its side question through ``_spawn`` without a ``holder``, so
``stop_authority_work`` never reaches the cache-parity fork it runs, while
``join_authority_work`` (the retirement join, ``_retire_profile_authority``, deadline
``TURN_SETTLE_SECONDS`` = 45 s) now waits for it. The /bg twin of this test is the PR's own
``test_side_agent_is_owner_work_until_its_thread_unwinds``; here the fork answers its
``hard_interrupt`` the way a real ``AIAgent`` does, so retirement can only finish if something
interrupts it.
"""
import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_retirement_interrupts_a_running_btw_instead_of_retaining_ownership(monkeypatch):
    from agent import background_review
    from gateway import session_tui_side
    from gateway.session_contract import SessionRef
    from gateway.session_runtime_workers import join_authority_work

    started, interrupted, finished = threading.Event(), threading.Event(), threading.Event()

    class Fork:
        def run_conversation(self, **_):
            started.set()
            interrupted.wait(10)  # a provider call that only an interrupt cuts short
            finished.set()
            return {'final_response': 'interrupted' if interrupted.is_set() else 'late'}

        def hard_interrupt(self, message=None):
            interrupted.set()

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(background_review, 'build_cache_parity_fork', lambda *a, **k: (Fork(), None, False))
    monkeypatch.setattr(background_review, '_snapshot_review_usage', lambda fork: None)
    monkeypatch.setattr(background_review, '_record_review_usage_to_parent', lambda *a: None)
    parent = SimpleNamespace(_session_messages=[{'role': 'user', 'content': 'hi'},
                                                {'role': 'assistant', 'content': 'hello'}])
    authority = SimpleNamespace(sessions={}, pending_stops={}, adopted={}, agent=lambda ref: parent)
    runner = SimpleNamespace(_running_agents={}, session_authority=authority, _persist_active_agents=lambda: None)
    authority.runner = runner
    monkeypatch.setattr(session_tui_side, '_admit', lambda *_: (None, None, 'what did we decide?', 'btw_x'))
    monkeypatch.setattr(session_tui_side, '_side_scope', lambda *_: contextlib.nullcontext())
    monkeypatch.setattr(session_tui_side, '_resolve_runtime', lambda *_: ('model', {}))
    try:
        await session_tui_side.btw(SimpleNamespace(authority=authority), SessionRef('p', 's'), {})
        assert await asyncio.to_thread(started.wait, 5)
        # Well inside the 45 s retirement deadline: a stoppable side question unwinds at once.
        try:
            await join_authority_work(authority, 3.0)
        except TimeoutError:
            pytest.fail('retirement timed out on a running /btw it never interrupted '
                        f'(interrupted={interrupted.is_set()}): ownership retained')
    finally:
        interrupted.set()
        await asyncio.to_thread(finished.wait, 5)
