"""Probe (RT-37 focus 3/5): ``forward_prompt(sid, session, text)`` forwards the text only. The owner's
``prompt.submit`` accepts ``display_kind`` / ``title_preview`` / ``surface`` / ``voice_turn`` /
``voice_context`` / ``interrupted`` (gateway/session_controls.py ``submission``), and the legacy
handler honours them on the non-forwarded path. A hidden widget-intent send (``display_kind:
hidden``: "no client renders a bubble") made during a gateway turn becomes a VISIBLE user row in
the owner's transcript, and a spoken voice turn runs on the main model instead of
``auxiliary.voice_chat``."""
import pytest

from hermes_state import SessionDB
from hermes_state_runtime import list_session_admissions
from tui_gateway import server

import _rt37
from _rt37 import legacy, owner, running_bot_turn, submit


@pytest.mark.parametrize("extra, field", [
    ({"display_kind": "hidden"}, "display_v1"),
    ({"voice_turn": True}, "voice_turn"),
])
def test_forward_keeps_the_per_turn_fields_the_owner_accepts(monkeypatch, tmp_path, extra, field):
    if _rt37.owner_forward is None:
        pytest.skip("main: no owner_forward")
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, _emitted = legacy(monkeypatch, db)
    try:
        epoch, _bot, _claimed = running_bot_turn(db, key)
        owner(monkeypatch, db, key, epoch)
        reply = submit(sid, "widget intent", **extra)
        assert reply["result"]["forwarded"] is True, reply
        (mine,) = [r for r in list_session_admissions(db, session_id=key) if r["admission_id"] == reply["result"]["admission_id"]]
        print("forwarded payload:", mine["payload"])
        assert field in str(mine["payload"]), ("forward dropped", extra, mine["payload"])
    finally:
        server._sessions.pop(sid, None)
        db.close()
