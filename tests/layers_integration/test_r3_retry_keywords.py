"""R3 handback: groups.retry forwards a generation the base retry method rejects.

This does not change gateway code. It calls the methods already on cb8.
"""

import pytest

DESKTOP_RETRY = {
    "room_id": "room-one",
    "member_id": "one",
    "task_id": "task-1",
    "execution_generation": 1,
}


def test_execution_control_forwards_desktop_retry_into_the_narrow_method():
    from gateway.session_group_controls import _execution_control
    from tui_gateway.hosted_room_service import HostedRoomService

    class Service:
        retry_room_task = HostedRoomService.retry_room_task

    with pytest.raises(TypeError, match="unexpected keyword argument 'member_id'"):
        _execution_control(Service(), "groups.retry", dict(DESKTOP_RETRY))


def test_canonical_service_binds_the_control_that_compares_generation():
    from gateway.session_hosted_controls import HostedControls
    from gateway.session_hosted_service import CanonicalHostedRoomService

    assert CanonicalHostedRoomService.retry_room_task is HostedControls.retry_room_task
