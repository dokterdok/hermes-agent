"""Local room approvals retain their offered choices and exact request identity."""

from pathlib import Path
import time

import pytest

from gateway import hosted_room_driver as driver
from tui_gateway.hosted_room_service import HostedRoomService
from tests.tui_gateway.test_hosted_room_service import _FakeRPC, _server, _start_approval_task


@pytest.mark.parametrize("offered", [["once", "always", "deny"], ["always", "session"], []])
def test_local_pending_approval_requires_exact_task_generation_and_request(
    tmp_path: Path, offered,
):
    class ApprovalRPC(_FakeRPC):
        def __init__(self) -> None:
            super().__init__()
            self.approvals = []

        def approve(self, *, session_id, request_id, choice, expected_task_id, expected_execution_generation):
            self.approvals.append((session_id, request_id, choice, expected_task_id, expected_execution_generation))
            return {"resolved": 1}

    db = tmp_path / "state.db"
    service = HostedRoomService(_server(), db_path=db)
    rpc = ApprovalRPC()
    service.rpc = rpc
    service.runtime.rpc = rpc
    service.local_profiles = lambda: ("default", "ops")
    service.create_room(
        room_id="room-1",
        name="Release room",
        members=[
            {"member_id": "default", "profile": "default", "handle": "hermes"},
            {"member_id": "ops", "profile": "ops", "handle": "ops"},
        ],
    )
    service.send(
        room_id="room-1",
        event_id="user-1",
        payload={"text": "@ops inspect", "thread_id": "thread-1"},
    )
    task = driver.list_tasks(db, room_id="room-1", status="queued")[0]
    binding = service.bindings()[0]
    lease = driver.acquire_lease(
        db,
        room_id="room-1",
        gateway_id=binding.gateway_id,
        authority_epoch=binding.authority_epoch,
        process_generation="worker",
        ttl_seconds=30,
        clock=time.time,
    )
    driver.start_task(
        db,
        task["identity"],
        lease,
        expected_cancel_generation=0,
        clock=time.time,
    )
    task = driver.get_task(db, task["identity"])
    service.runtime._report_pending_action(
        task,
        session_id="ops-session",
        info={
            "pending_approval": {
                "request_id": "approval-1",
                "choices": offered,
            }
        },
    )

    action = service.status("room-1")["pending_actions"][0]
    assert action["member_id"] == "ops"
    if "once" not in offered:
        assert action["approval"]["choices"] == []
        with pytest.raises(RuntimeError):
            service.approve_room_task("room-1", member_id="ops", task_id=task["identity"].task_id,
                execution_generation=1, choice="once", request_id="approval-1")
        assert rpc.approvals == []
        return
    assert action["approval"]["choices"] == ["once", "deny"]
    with pytest.raises(RuntimeError, match="no longer pending"):
        service.approve_room_task(
            "room-1",
            member_id="ops",
            task_id=task["identity"].task_id,
            execution_generation=1,
            choice="once",
            request_id="wrong-request",
        )

    assert service.approve_room_task(
        "room-1",
        member_id="ops",
        task_id=task["identity"].task_id,
        execution_generation=1,
        choice="once",
        request_id="approval-1",
    ) == {"resolved": 1}
    assert rpc.approvals == [("ops-session", "approval-1", "once", task["identity"].task_id, 1)]
    assert service.status("room-1")["pending_actions"] == []


def test_local_room_approval_uses_the_exact_hidden_session(tmp_path: Path):
    service = HostedRoomService(_server(), db_path=tmp_path / "state.db")
    rpc = _FakeRPC()
    service.rpc = rpc
    service.runtime.rpc = rpc
    service.local_profiles = lambda: ('default', 'ops')
    service.create_room(room_id='room-1', name='Approval room',
                        members=[{'member_id': 'local', 'profile': 'default', 'handle': 'hermes'},
                                 {'member_id': 'ops', 'profile': 'ops', 'handle': 'ops'}])
    _start_approval_task(service, task_id='task-local-1', member_id='local', profile='default')
    service._set_pending_action(
        "room-1",
        "local",
        {
            "kind": "approval",
            "task_id": "task-local-1",
            "execution_generation": 1,
            "session_id": "local-session",
            "request_id": "approval-local-1",
            "approval": {
                "description": "Run focused tests",
                "command": "pytest -q tests/focused",
                "choices": ["once", "deny"],
            },
        },
    )

    assert service.approve_room_task(
        "room-1",
        member_id="local",
        task_id="task-local-1",
        execution_generation=1,
        choice="once",
        request_id="approval-local-1",
    ) == {"resolved": 1}
    assert rpc.approvals == [
        {
            "session_id": "local-session",
            "request_id": "approval-local-1",
            "choice": "once",
            "expected_task_id": "task-local-1",
            "expected_execution_generation": 1,
        }
    ]
    assert service.status("room-1")["pending_actions"] == []


def test_stale_local_approval_cannot_resolve_replacement_request(tmp_path: Path):
    service = HostedRoomService(_server(), db_path=tmp_path / "state.db")
    rpc = _FakeRPC()
    service.rpc = rpc
    service.runtime.rpc = rpc
    action = {
        "kind": "approval",
        "task_id": "task-local-1",
        "execution_generation": 1,
        "session_id": "local-session",
        "approval": {"choices": ["once", "deny"]},
    }
    service._set_pending_action(
        "room-1", "local", {**action, "request_id": "approval-A"}
    )
    service._set_pending_action(
        "room-1", "local", {**action, "request_id": "approval-B"}
    )

    with pytest.raises(RuntimeError, match="no longer pending"):
        service.approve_room_task(
            "room-1",
            member_id="local",
            task_id="task-local-1",
            execution_generation=1,
            choice="once",
            request_id="approval-A",
        )

    assert rpc.approvals == []
    assert service.status("room-1")["pending_actions"][0]["request_id"] == (
        "approval-B"
    )
