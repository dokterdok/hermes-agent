"""Review probe (lane 2, RT-37 sibling): a kanban notification refused by the canonical ledger.

RT-37 passes ``report_ownership_refusal=False`` for process completions and watch matches, so a
notice the poller retries paints no failed turn. ``_notif_poll_kanban_scoped`` is the third
producer notice through ``_notif_submit``: it still paints the refusal as an ``error``, and it
removed the batch from ``_kanban_pending`` before submitting, so the refused card notice is gone.
Same harness as ``tests/tui_gateway/test_notification_refusal_backoff.py``.
"""
import contextlib
import threading
from types import SimpleNamespace

from hermes_state import SessionDB
from hermes_state_runtime import admit_session_input, begin_runtime_epoch, claim_session_input
from tui_gateway import server


def test_refused_kanban_notice_is_kept_and_not_painted_as_failed(monkeypatch, tmp_path):
    with contextlib.closing(SessionDB(db_path=tmp_path / "state.db")) as db:
        key = "kanban-owner"
        db.create_session(key, "desktop")
        epoch = begin_runtime_epoch(db, instance_id="gateway")
        admit_session_input(db, epoch=epoch, principal_id="owner", session_id=key,
                            request_id="busy", payload={"text": "running"})
        claim_session_input(db, epoch=epoch, session_id=key)  # a gateway turn holds started work
        monkeypatch.setattr(server, "_session_db", lambda session: contextlib.nullcontext(db))
        monkeypatch.setattr(server, "_ensure_session_db_row", lambda session: True)
        emitted = []
        monkeypatch.setattr(server, "_emit", lambda *args: emitted.append(args))
        monkeypatch.setattr(server, "time", SimpleNamespace(sleep=lambda s: None, time=lambda: 1))
        monkeypatch.setattr(server, "_collect_kanban_notifications", lambda session: ["Kanban card t1 completed"])
        session = {"history_lock": threading.RLock(), "session_key": key, "running": False}

        server._notif_poll_kanban_scoped("ui", session)

        assert session["running"] is False
        errors = [args for args in emitted if args[0] == "error"]
        pending = session.get("_kanban_pending") or []
        assert not errors and pending == ["Kanban card t1 completed"], (
            f"refused kanban notice: painted errors={errors!r}, still buffered={pending!r}")
