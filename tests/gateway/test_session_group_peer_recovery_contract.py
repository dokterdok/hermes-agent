"""Unknown peer execution keeps its identity and requires terminal evidence for Stop."""
from gateway import hosted_room_driver as tasks
from tests.gateway.test_session_group_peer_controls import (
    case as case, current, rpc, selector, tick, unknown_with_receipt,
)


def test_stop_of_unknown_deferred_peer_waits_for_its_exact_terminal_receipt(case, monkeypatch):
    c = case
    unknown_with_receipt(c)
    task = current(c)
    binding = next(binding for binding in c.service.bindings() if binding.room_id == 'room')
    lease = c.service.runtime._ensure_lease(binding)
    tasks.defer_indeterminate_task(c.service.db_path, task['identity'], lease,
        expected_execution_generation=task['execution_generation'],
        expected_cancel_generation=task['cancel_generation'], reason='member_unavailable',
        clock=c.service.runtime.clock)
    exact = selector(c)
    assert not tasks.is_proven_nonadmission(current(c))
    c.peer.stop_status = None

    def status(**kwargs):
        stopped = bool(c.peer.stops) and c.peer.stop_status == 'cancelled'
        return {'active': not stopped, 'status': 'cancelled' if stopped else 'running',
                'task_id': exact['task_id'], 'execution_generation': exact['execution_generation']}

    monkeypatch.setattr(c.peer, 'status', status)
    assert 'result' in rpc(c, 'groups.stop')
    assert current(c)['status'] == 'stopping'
    assert current(c)['execution_generation'] == exact['execution_generation']
    assert c.peer.stops == [(exact['task_id'], exact['execution_generation'])]
    c.peer.stop_status = 'cancelled'
    assert 'result' in rpc(c, 'groups.stop')
    assert current(c)['status'] == 'cancelled'
    assert current(c)['execution_generation'] == exact['execution_generation']


def test_observed_active_peer_is_not_deferred_past_the_recovery_deadline(case, monkeypatch):
    c = case
    unknown_with_receipt(c)
    exact = selector(c)
    monkeypatch.setattr(c.peer, 'history', lambda **_: [])
    monkeypatch.setattr(c.peer, 'status', lambda **_: {
        'active': True, 'status': 'running', 'task_id': exact['task_id'],
        'execution_generation': exact['execution_generation']})
    c.now[0] += c.service.runtime.indeterminate_defer_seconds + 1
    tick(c)
    assert current(c)['status'] == 'indeterminate'
    assert current(c)['execution_generation'] == exact['execution_generation']
