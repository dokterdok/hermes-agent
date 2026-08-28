from __future__ import annotations

import pytest

from gateway.hosted_room_turn_context import (
    RoomTurnContextError,
    room_turn_context_from_mapping,
)


def test_legacy_turn_can_finish_but_cannot_delegate() -> None:
    context = room_turn_context_from_mapping({"member_id": "writer"})

    assert context.provenance == {"kind": "legacy"}
    assert context.targets == ()
    with pytest.raises(RoomTurnContextError, match="cannot delegate work"):
        context.request_handoff("reviewer", "Review the draft")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "member_id": "writer",
            "provenance": {"kind": "user", "user_event_id": "event-1"},
        },
        {
            "member_id": "writer",
            "handoff_targets": [{"member_id": "reviewer", "handle": "reviewer"}],
        },
    ],
)
def test_partial_provenance_contract_is_rejected(payload: dict) -> None:
    with pytest.raises(RoomTurnContextError, match="must be supplied together"):
        room_turn_context_from_mapping(payload)


def test_scoped_turn_can_delegate_only_to_declared_target() -> None:
    context = room_turn_context_from_mapping({
        "member_id": "writer",
        "provenance": {"kind": "user", "user_event_id": "event-1"},
        "handoff_targets": [
            {"member_id": "reviewer", "handle": "reviewer"},
        ],
    })

    assert context.request_handoff("@reviewer", "Review the draft") == {
        "recipient_member_id": "reviewer",
        "recipient_handle": "reviewer",
        "objective": "Review the draft",
    }
    with pytest.raises(RoomTurnContextError, match="not an available Group Chat Bot"):
        context.request_handoff("unknown", "Do something else")
