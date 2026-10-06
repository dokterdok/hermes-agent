"""Hosted cancellation cannot affect a newer task or reuse of the same task ID."""
from pathlib import Path

import pytest

from tests.tui_gateway import test_hosted_room_driver_runtime as driver_fixture

db = driver_fixture.db


@pytest.mark.parametrize("same_task", [False, True])
def test_cancel_never_interrupts_a_newer_task_in_the_same_session(db: Path, same_task):
    identity = driver_fixture._identity()
    driver_fixture._admit(db, identity)
    rpc = driver_fixture.FakeSessionRPC(auto_complete=False)
    runtime = driver_fixture._runtime(db, rpc)

    runtime.start()
    assert rpc.submitted.wait(1.0)
    session_id = next(iter(rpc.states))
    next_task_id = identity.task_id if same_task else "task-2"

    def switch_to_newer_task() -> None:
        with rpc._lock:
            rpc.states[session_id]["active"] = True
            rpc.states[session_id]["task_id"] = next_task_id
            rpc.states[session_id]["execution_generation"] = 2

    rpc.on_info = switch_to_newer_task
    cancelled = runtime.cancel(identity, cancel_id="cancel-old-task")

    assert cancelled["status"] == "stopping"
    assert not [call for call in rpc.calls if call[0] == "interrupt"]
    skipped = [params for method, params in rpc.calls if method == "interrupt_skipped"]
    assert all(params["expected_task_id"] == identity.task_id for params in skipped)
    assert all(params["expected_execution_generation"] == 1 for params in skipped)
    assert rpc.states[session_id]["active"] is True
    assert rpc.states[session_id]["task_id"] == next_task_id
    assert runtime.stop(timeout=5.0)


