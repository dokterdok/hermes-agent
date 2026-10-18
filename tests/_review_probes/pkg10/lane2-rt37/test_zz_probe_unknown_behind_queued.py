"""Probe (RT-37 focus 5): the ledger gate reads ONE row (``LIMIT 1`` over the
``(target_session_id, status, seq)`` index, so 'queued' sorts before 'unknown'). After an owner
restart the FIFO holds an ``unknown`` turn with a queued delivery behind it; the commit promises an
``unknown`` admission keeps SESSION_NOT_OWNED and is "never forwarded past"."""
from hermes_state import SessionDB
from hermes_state_runtime import (
    admit_session_input, begin_runtime_epoch, claim_session_input, list_session_admissions,
    recover_session_inputs,
)
from tui_gateway import server

from _rt37 import legacy, owner, submit


def test_unknown_admission_with_queued_input_behind_it_is_not_forwarded_past(monkeypatch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, turns, _emitted = legacy(monkeypatch, db)
    try:
        db.create_session(key, "test")
        first = begin_runtime_epoch(db, instance_id="gateway-1")
        admit_session_input(db, epoch=first, principal_id="bot", session_id=key, request_id="dm-1",
                            payload={"text": "running when the gateway restarted"})
        claim_session_input(db, epoch=first, session_id=key)
        # A second delivery was queued behind it; queued input survives the restart.
        admit_session_input(db, epoch=first, principal_id="bot", session_id=key, request_id="dm-2",
                            payload={"text": "queued behind it"})
        epoch = begin_runtime_epoch(db, instance_id="gateway-2")
        recover_session_inputs(db, epoch=epoch)
        assert [r["status"] for r in list_session_admissions(db, session_id=key)] == ["unknown", "queued"]
        owner(monkeypatch, db, key, epoch)

        reply = submit(sid, "hi")

        # Expected (commit 40cdeb6dd6): refused SESSION_NOT_OWNED, nothing admitted past the unknown turn.
        assert "error" in reply, reply
        assert reply["error"]["data"] == {"reason": "SESSION_NOT_OWNED"}, reply
        assert len(list_session_admissions(db, session_id=key)) == 2 and turns == []
    finally:
        server._sessions.pop(sid, None)
        db.close()
