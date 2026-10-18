"""Probes (RT-37 focus 4/6): what the relay does with owner frames other than message.start /
message.complete for the forwarded admission.

1. approval.request: the forwarded turn runs in the owner; its approval prompt is published on the
   owner's stream (gateway/session_pending_controls.py ``register``) to the attached viewers, i.e.
   only to the relay, which drops it (``_RELAYED``). The Desktop user never sees the prompt; the
   command waits for the approval timeout and is denied.
2. session.replay_gap: the owner retires an overflowed subscription and sends this frame
   (gateway/session_events.py ``overflow``); the relay ignores it and waits RELAY_TIMEOUT_S (6 h)
   for a completion that will never reach it, so the Desktop never settles."""
import pytest

from hermes_state import SessionDB
from hermes_state_runtime import claim_session_input, settle_session_input
from tests.waiting import wait_until
from tui_gateway import server

import _rt37
from _rt37 import legacy, owner, running_bot_turn, submit


def test_an_approval_the_forwarded_turn_asks_reaches_the_desktop(monkeypatch, tmp_path):
    if _rt37.owner_forward is None:
        pytest.skip("main: no owner_forward")
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, emitted = legacy(monkeypatch, db)
    try:
        epoch, bot, claimed = running_bot_turn(db, key)
        authority = owner(monkeypatch, db, key, epoch)
        mine = submit(sid, "clean the build dir")["result"]["admission_id"]
        settle_session_input(db, epoch=epoch, admission_id=bot["admission_id"], generation=claimed["generation"],
                             outcome="completed")
        row = claim_session_input(db, epoch=epoch, session_id=key)
        assert row["admission_id"] == mine
        from gateway.session_contract import SessionRef
        from gateway.session_settlement_recovery import commit_and_publish
        ref, live = SessionRef("owned", key), authority.sessions[key]
        authority._stamp_claim(ref, live, row)
        wait_until(lambda: any(e == "message.start" and s == sid for e, s, _p in emitted), timeout=10)
        # The forwarded turn hits a dangerous command: the owner's real approval publication.
        live.controls.register(key, object(), row["generation"],
                               {"request_id": "apr-1", "command": "rm -rf build", "description": "recursive delete"})
        with live.event_stream.lock:
            authority.executing.pop(key, None)
        commit_and_publish(authority, live, ref, row, "done", "completed", None, {})
        wait_until(lambda: any(e == "message.complete" and s == sid for e, s, _p in emitted), timeout=10)
        kinds = [e for e, s, _p in emitted if s == sid]
        print("relayed onto the Desktop session:", kinds)
        assert "approval.request" in kinds, ("the forwarded turn's approval never reached the Desktop", kinds)
    finally:
        server._sessions.pop(sid, None)
        db.close()


def test_a_replay_gap_on_the_relay_settles_or_reattaches(monkeypatch, tmp_path):
    if _rt37.owner_forward is None:
        pytest.skip("main: no owner_forward")
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, emitted = legacy(monkeypatch, db)
    clients = []
    try:
        epoch, _bot, _claimed = running_bot_turn(db, key)
        authority = owner(monkeypatch, db, key, epoch, clients=clients)
        submit(sid, "queued while the bot turn runs")
        (client,) = clients
        live = authority.sessions[key]
        # The owner's overflow path: retire this peer's subscription and send the gap frame.
        authority._retire_overflowed(key, client)
        live.event_stream.fanout.detach(client)
        client.write({"jsonrpc": "2.0", "method": "event", "params": {
            "type": "session.replay_gap", "session_id": key,
            "payload": {"replay_epoch": live.event_stream.epoch, "latest_seq": live.event_stream.sequence}}})
        resumes = lambda: [c for c in client.calls if c[0] in ("session.resume", "session.events.since")]  # noqa: E731
        settled = lambda: any(e == "message.complete" and s == sid for e, s, _p in emitted)  # noqa: E731
        try:
            wait_until(lambda: len(resumes()) > 1 or settled(), timeout=5)
        except AssertionError:
            pass
        assert len(resumes()) > 1 or settled(), (
            "relay ignored session.replay_gap: no re-attach, no settle; it waits RELAY_TIMEOUT_S", client.calls)
    finally:
        server._sessions.pop(sid, None)
        db.close()


def test_stop_on_a_forwarded_turn_reaches_the_owner_or_is_refused(monkeypatch, tmp_path):
    """The relayed message.start shows the turn as running on the Desktop, whose Stop calls the
    LEGACY ``session.interrupt``. It answers ``interrupted`` unconditionally
    (methods_session_interrupt.py) while the owner keeps running the forwarded turn."""
    if _rt37.owner_forward is None:
        pytest.skip("main: no owner_forward")
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, emitted = legacy(monkeypatch, db)
    clients = []
    try:
        epoch, bot, claimed = running_bot_turn(db, key)
        authority = owner(monkeypatch, db, key, epoch, clients=clients)
        owner_interrupts = []
        real_interrupt = authority.interrupt

        async def spy(actor, ref, generation):
            owner_interrupts.append(generation)
            return await real_interrupt(actor, ref, generation)
        monkeypatch.setattr(authority, "interrupt", spy)
        mine = submit(sid, "long running job")["result"]["admission_id"]
        settle_session_input(db, epoch=epoch, admission_id=bot["admission_id"], generation=claimed["generation"],
                             outcome="completed")
        row = claim_session_input(db, epoch=epoch, session_id=key)
        assert row["admission_id"] == mine
        from gateway.session_contract import SessionRef
        authority._stamp_claim(SessionRef("owned", key), authority.sessions[key], row)
        wait_until(lambda: any(e == "message.start" and s == sid for e, s, _p in emitted), timeout=10)

        stopped = server.handle_request({"id": "i", "method": "session.interrupt", "params": {"session_id": sid}})
        print("legacy session.interrupt:", stopped)
        if stopped.get("result", {}).get("status") == "interrupted":
            try:
                wait_until(lambda: owner_interrupts or any(c[0] == "session.interrupt" for c in clients[0].calls), timeout=3)
            except AssertionError:
                pass
            assert owner_interrupts or any(c[0] == "session.interrupt" for c in clients[0].calls), (
                "legacy backend answered 'interrupted' but the owner was never asked to stop the forwarded turn",
                [r["status"] for r in __import__("hermes_state_runtime").list_session_admissions(db, session_id=key)])
    finally:
        server._sessions.pop(sid, None)
        db.close()
