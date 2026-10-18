"""Probe (RT-37 focus 4): each forward runs its own relay, and each relay emits ``session.info``
``pending_submissions`` holding ONLY its own admission. The Desktop treats every such list as the
session's full pending snapshot (store/pending-submissions.ts: an observed admission absent from a
snapshot is retired). Two prompts typed during one gateway turn: each relay's list hides the other's
still-queued card, and when the first starts its relay emits [] while the second is still queued."""
from hermes_state import SessionDB
from hermes_state_runtime import claim_session_input, list_session_admissions, settle_session_input
from tests.waiting import wait_until
from tui_gateway import server

import pytest

import _rt37
from _rt37 import legacy, owner, running_bot_turn, submit


def _snapshots(emitted, sid):
    return [p["pending_submissions"] for e, s, p in emitted if e == "session.info" and s == sid]


def _two_forwards(monkeypatch, tmp_path):
    if _rt37.owner_forward is None:
        pytest.skip("main: no owner_forward")
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, emitted = legacy(monkeypatch, db)
    epoch, bot, claimed = running_bot_turn(db, key)
    authority = owner(monkeypatch, db, key, epoch)
    first = submit(sid, "first question")["result"]["admission_id"]
    wait_until(lambda: any(first in [r["admission_id"] for r in snap] for snap in _snapshots(emitted, sid)), timeout=10)
    second = submit(sid, "second question")["result"]["admission_id"]
    wait_until(lambda: any(second in [r["admission_id"] for r in snap] for snap in _snapshots(emitted, sid)), timeout=10)
    queued = [r["admission_id"] for r in list_session_admissions(db, session_id=key) if r["status"] == "queued"]
    assert queued == [first, second]
    return db, sid, key, emitted, epoch, bot, claimed, authority, first, second


def test_the_latest_pending_snapshot_lists_every_queued_forward(monkeypatch, tmp_path):
    db, sid, *_rest, first, second = _two_forwards(monkeypatch, tmp_path)
    try:
        latest = [r["admission_id"] for r in _snapshots(_rest[1], sid)[-1]]
        assert latest == [first, second], ("latest pending snapshot drops a queued forward", _snapshots(_rest[1], sid))
    finally:
        server._sessions.pop(sid, None)
        db.close()


def test_the_second_card_survives_the_first_forward_starting(monkeypatch, tmp_path):
    db, sid, key, emitted, epoch, bot, claimed, authority, first, second = _two_forwards(monkeypatch, tmp_path)
    try:
        settle_session_input(db, epoch=epoch, admission_id=bot["admission_id"], generation=claimed["generation"],
                             outcome="completed")
        row = claim_session_input(db, epoch=epoch, session_id=key)
        assert row["admission_id"] == first
        from gateway.session_contract import SessionRef
        before = len(_snapshots(emitted, sid))
        authority._stamp_claim(SessionRef("owned", key), authority.sessions[key], row)  # publishes pending [second]
        wait_until(lambda: any(e == "message.start" and s == sid for e, s, _p in emitted), timeout=10)
        wait_until(lambda: len(_snapshots(emitted, sid)) > before, timeout=10)  # a relay repainted the card list
        latest = [r["admission_id"] for r in _snapshots(emitted, sid)[-1]]
        assert second in latest, ("second prompt still queued in the owner but the latest snapshot is", latest,
                                  _snapshots(emitted, sid))
    finally:
        server._sessions.pop(sid, None)
        db.close()
