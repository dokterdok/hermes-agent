"""Hosted approvals stay pinned to their task and execution generation through real RPC."""
import pytest

from tests.tui_gateway import test_tui_gateway_server as server_fixture
from tui_gateway import server


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
    server._sessions['sid'] = server_fixture._session(session_key=key, running=True,
        _hosted_room_task={'task_id': 'active', 'execution_generation': 2})
    with approval._lock:
        approval._gateway_queues[key] = [entry]
    try:
        result = server_fixture._dispatch_sync({'id': 'approval', 'method': 'approval.respond', 'params': {
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
