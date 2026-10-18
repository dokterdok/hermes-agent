"""G3: retirement must reach the running side-question fork, not just count it."""
import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest

from gateway import session_tui_side
from gateway.session_contract import SessionRef
from gateway.session_runtime_workers import join_authority_work


@pytest.mark.asyncio
async def test_retirement_interrupts_btw_fork(monkeypatch):
    from agent import background_review
    started, release, interrupted = threading.Event(), threading.Event(), threading.Event()
    threads = []

    def run_conversation(**kwargs):
        threads.append(threading.current_thread())
        started.set()
        assert release.wait(10), 'probe cleanup did not release fork'
        return {'final_response': 'done'}

    def hard_interrupt(message=None):
        interrupted.set()
        release.set()

    fork = SimpleNamespace(run_conversation=run_conversation, hard_interrupt=hard_interrupt,
                           close=lambda: None, shutdown_memory_provider=lambda: None)
    parent = SimpleNamespace(_session_messages=[{'role': 'assistant', 'content': 'context'}])
    authority = SimpleNamespace(sessions={}, pending_stops={}, adopted={}, agent=lambda ref: parent)
    authority.runner = SimpleNamespace(_running_agents={})
    monkeypatch.setattr(session_tui_side, '_admit', lambda *_: (None, None, 'question', 'btw_probe'))
    monkeypatch.setattr(session_tui_side, '_side_scope', lambda *_: contextlib.nullcontext())
    monkeypatch.setattr(session_tui_side, '_resolve_runtime', lambda *_: ('probe-model', {}))
    monkeypatch.setattr(background_review, 'build_cache_parity_fork', lambda *a, **k: (fork, {}, False))
    try:
        await session_tui_side.btw(SimpleNamespace(authority=authority), SessionRef('p', 's'), {})
        assert await asyncio.to_thread(started.wait, 5)
        timed_out = False
        try:
            await join_authority_work(authority, 2)
        except TimeoutError:
            timed_out = True
        assert interrupted.is_set(), f'live /btw fork never interrupted; retirement timed_out={timed_out}'
        assert not timed_out
    finally:
        release.set()
        for thread in threads:
            await asyncio.to_thread(thread.join, 5)
