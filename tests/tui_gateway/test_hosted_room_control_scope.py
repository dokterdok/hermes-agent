"""Scoped hosted approvals and Stop preserve the plain-session control contract."""
import types

import pytest

from tests.tui_gateway.test_tui_gateway_server import _dispatch_sync, _session, server


@pytest.mark.parametrize('scope,matched', [
    ({'expected_hosted_task_id': 'old', 'expected_hosted_execution_generation': 2}, False),
    ({'expected_hosted_task_id': 'active', 'expected_hosted_execution_generation': 1}, False),
    ({'expected_hosted_task_id': 'active'}, False),
    ({'expected_hosted_execution_generation': 2}, False),
    ({'expected_hosted_task_id': 'active', 'expected_hosted_execution_generation': True}, False),
    ({'expected_hosted_task_id': 'active', 'expected_hosted_execution_generation': 2}, True),
    ({}, True),
])
@pytest.mark.parametrize('choice', ['once', 'deny', 'always'])
def test_hosted_approval_selection_is_exact_while_plain_approval_stays_unscoped(scope, matched, choice):
    import tools.approval as approval
    from tools.approval_gateway_wait import _ApprovalEntry
    key = 'hosted-approval-exact-selection'
    entry = _ApprovalEntry({'request_id': 'reused-prompt', 'command': 'later operation'})
    server._sessions['sid'] = _session(session_key=key, running=True,
        _hosted_room_task={'task_id': 'active', 'execution_generation': 2})
    with approval._lock:
        approval._gateway_queues[key] = [entry]
    try:
        result = _dispatch_sync({'id': 'approval', 'method': 'approval.respond', 'params': {
            'session_id': 'sid', 'request_id': 'reused-prompt', 'choice': choice, 'all': False, **scope}})
        accepted = matched and (not scope or choice in {'once', 'deny'})
        if accepted:
            assert result['result']['resolved'] == 1 and entry.result == choice and entry.event.is_set()
        else:
            assert 'error' in result and entry.result is None and not entry.event.is_set()
    finally:
        server._sessions.pop('sid', None)
        with approval._lock:
            approval._gateway_queues.pop(key, None)



@pytest.mark.parametrize("scope,accepted", [
    ({"expected_hosted_task_id": "active"}, False),
    ({"expected_hosted_task_id": "active", "expected_hosted_execution_generation": 1}, False),
    ({"expected_hosted_execution_generation": 2}, False),
    ({"expected_hosted_task_id": "active", "expected_hosted_execution_generation": 2}, True),
    ({}, True),
])
def test_hosted_interrupt_requires_generation_but_plain_stop_still_works(monkeypatch, scope, accepted):
    interrupted = []
    session = _session(agent=types.SimpleNamespace(interrupt=lambda: interrupted.append(True)),
                       running=True, _hosted_room_task={"task_id": "active", "execution_generation": 2})
    server._sessions["sid"] = session
    monkeypatch.setattr(server, "_resume_wake_after_interrupt", lambda: None)
    try:
        result = _dispatch_sync({"id": "stop", "method": "session.interrupt",
                                 "params": {"session_id": "sid", **scope}})
        assert result["result"]["status"] == ("interrupted" if accepted else "not_interrupted")
        assert interrupted == ([True] if accepted else [])
        if not accepted:
            assert session["running"] and not session.get("_turn_cancel_requested")
    finally:
        server._sessions.pop("sid", None)
