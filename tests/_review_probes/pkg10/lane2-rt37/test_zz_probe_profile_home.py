"""Probe (RT-37 focus 2): the forward resolves the owner from the SESSION's profile home, not the
launch profile's, and leaves os.environ untouched. Expected to PASS (what holds up)."""
import contextlib
import os
from pathlib import Path

import pytest

from hermes_state import SessionDB
from tui_gateway import server

import _rt37
from _rt37 import OwnerClient, legacy, running_bot_turn, submit


def test_forward_dials_the_sessions_profile_home(monkeypatch, tmp_path):
    if _rt37.owner_forward is None:
        pytest.skip("main: no owner_forward")
    launch_db = SessionDB(db_path=tmp_path / "state.db")
    sid, key, _turns, _emitted = legacy(monkeypatch, launch_db)
    secondary = tmp_path / "profiles" / "work"
    secondary.mkdir(parents=True)
    db = SessionDB(db_path=secondary / "state.db")
    try:
        server._sessions[sid]["profile_home"] = str(secondary)
        epoch, _bot, _claimed = running_bot_turn(db, key)
        from types import SimpleNamespace
        from gateway.session_authority import LiveSession, SessionAuthority
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id="work", instance_id="gateway",
                                     db=db, epoch=epoch)
        authority.sessions[key] = LiveSession(SimpleNamespace(platform=None, user_id="desktop"), "route")
        monkeypatch.setattr(authority, "_schedule", lambda ref: None)
        homes = []

        def discover(home):
            homes.append(("discover", Path(home)))
            return "ready-owner"

        @contextlib.asynccontextmanager
        async def connect(home, endpoint):
            homes.append(("connect", Path(home)))
            yield OwnerClient(authority)
        monkeypatch.setattr(_rt37.owner_forward, "discover_owner", discover)
        monkeypatch.setattr(_rt37.owner_forward, "connect_owner", connect)

        import gateway.run  # noqa: F401  the in-process OWNER imports it; its import-time env bridging is not the forward's
        env_before = dict(os.environ)
        reply = submit(sid, "hi")

        assert reply["result"]["forwarded"] is True, reply
        assert homes == [("discover", secondary.resolve()), ("connect", secondary.resolve())], homes
        assert Path(server._hermes_home).resolve() != secondary.resolve()
        assert dict(os.environ) == env_before
    finally:
        server._sessions.pop(sid, None)
        db.close()
        launch_db.close()
