from __future__ import annotations

import json

from gateway.hosted_room_driver import TaskIdentity
import tui_gateway.server as server


def test_groups_retry_returns_a_json_serializable_task_receipt(monkeypatch):
    class Service:
        @staticmethod
        def retry_room_task(room_id: str, *, task_id: str):
            assert room_id == "room-1"
            assert task_id == "task-1"
            return {
                "identity": TaskIdentity("room-1", "task-1", "thread-1", "turn-1"),
                "status": "queued",
                "execution_generation": 3,
                "cancel_generation": 1,
                "payload": {"not": "part of the public receipt"},
            }

    monkeypatch.setattr(server, "get_hosted_room_service", lambda: Service())

    response = server._methods["groups.retry"](
        "retry-1", {"room_id": "room-1", "task_id": "task-1"}
    )

    assert response["result"] == {
        "retried": True,
        "task": {
            "room_id": "room-1",
            "task_id": "task-1",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "status": "queued",
            "execution_generation": 3,
            "cancel_generation": 1,
        },
    }
    json.dumps(response)
