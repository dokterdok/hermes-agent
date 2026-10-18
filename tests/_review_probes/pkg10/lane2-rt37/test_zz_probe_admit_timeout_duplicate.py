"""Probe (RT-37 focus 1, exactly once): ``forward_prompt`` stops waiting after ``ADMIT_TIMEOUT_S``
but the relay thread keeps going and may still admit. ``_submit_to_ledger_owner`` turns that timeout
into SESSION_OWNER_BUSY "... Nothing was sent. Send it again ...", which the Desktop renders as a
retryable card. The retry is forwarded with a FRESH submission id, so the owner holds two admissions
of the same prompt and runs it twice."""
import asyncio
import threading

from hermes_state import SessionDB
from hermes_state_runtime import list_session_admissions
from tests.waiting import wait_until
from tui_gateway import server

import _rt37
from _rt37 import legacy, owner, running_bot_turn, submit


def test_a_timed_out_forward_that_says_nothing_was_sent_admits_nothing(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, _emitted = legacy(monkeypatch, db)
    release = threading.Event()

    async def slow_owner(method, _params):
        if method == "prompt.submit" and not release.is_set():
            await asyncio.to_thread(release.wait, 20)  # the owner's writer is slow (busy WAL, big catch-up)

    try:
        epoch, _bot, _claimed = running_bot_turn(db, key)
        owner(monkeypatch, db, key, epoch, before_rpc=slow_owner)
        if _rt37.owner_forward is not None:
            monkeypatch.setattr(_rt37.owner_forward, "ADMIT_TIMEOUT_S", 1.0)

        first = submit(sid, "deploy the fix")
        assert "error" in first, first
        print("first reply:", first["error"])
        release.set()

        def mine():
            return [r for r in list_session_admissions(db, session_id=key)
                    if (r.get("payload") or {}).get("text") == "deploy the fix"]

        said_nothing_sent = "Nothing was sent" in first["error"]["message"]
        # Give the still-running relay its chance to land the admission it was waiting on.
        try:
            wait_until(lambda: mine(), timeout=5)
        except AssertionError:
            pass
        late = mine()
        print("admitted after the 'Nothing was sent' refusal:", [r["request_id"] for r in late])

        # The Desktop card is retryable (SESSION_OWNER_BUSY): the user sends it again once.
        second = submit(sid, "deploy the fix")
        print("retry reply:", second)
        assert len(mine()) <= 1, ("the same prompt is admitted twice in the owner's FIFO",
                                  said_nothing_sent, [(r["request_id"], r["status"]) for r in mine()])
        assert not (said_nothing_sent and late), "refusal said 'Nothing was sent' but the owner admitted it"
    finally:
        release.set()
        server._sessions.pop(sid, None)
        db.close()
