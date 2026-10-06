"""Hosted Stop stays pinned to its task and execution generation through real RPC."""
import types

import pytest

from tests.tui_gateway import test_tui_gateway_server as server_fixture
from tui_gateway import server


@pytest.mark.parametrize("scope,accepted", [
    ({"expected_hosted_task_id": "active"}, False),
    ({"expected_hosted_task_id": "active", "expected_hosted_execution_generation": 1}, False),
    ({"expected_hosted_execution_generation": 2}, False),
    ({"expected_hosted_task_id": "active", "expected_hosted_execution_generation": 2}, True),
    ({}, True),
])
def test_hosted_interrupt_requires_generation_but_plain_stop_still_works(monkeypatch, scope, accepted):
    interrupted = []
    session = server_fixture._session(agent=types.SimpleNamespace(interrupt=lambda: interrupted.append(True)),
                       running=True, _hosted_room_task={"task_id": "active", "execution_generation": 2})
    server._sessions["sid"] = session
    monkeypatch.setattr(server, "_resume_wake_after_interrupt", lambda: None)
    try:
        result = server_fixture._dispatch_sync({"id": "stop", "method": "session.interrupt",
                                 "params": {"session_id": "sid", **scope}})
        assert result["result"]["status"] == ("interrupted" if accepted else "not_interrupted")
        assert interrupted == ([True] if accepted else [])
        if not accepted:
            assert session["running"] and not session.get("_turn_cancel_requested")
    finally:
        server._sessions.pop("sid", None)


