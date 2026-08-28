"""Typed, recipient-specific handoff for verified Group Chat turns."""

from __future__ import annotations

import json

from gateway.hosted_room_turn_context import (
    RoomTurnContextError,
    current_room_turn_context,
)
from tools.registry import registry


HANDOFF_GROUP_TASK_SCHEMA = {
    "name": "handoff_group_task",
    "description": (
        "Ask one specific Bot in this Group Chat to take a follow-up action. "
        "Use this instead of relying on a mention in ordinary chat text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "The recipient Bot handle, such as @reviewer.",
            },
            "objective": {
                "type": "string",
                "description": "The bounded follow-up objective for that Bot.",
            },
        },
        "required": ["recipient", "objective"],
    },
}


def handoff_group_task(recipient: str, objective: str) -> str:
    context = current_room_turn_context()
    if context is None:
        return json.dumps(
            {
                "ok": False,
                "error": "Handoffs are available only during a verified Group Chat turn.",
            }
        )
    try:
        handoff = context.request_handoff(recipient, objective)
    except RoomTurnContextError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps(
        {
            "ok": True,
            **handoff,
            "message": (
                f"@{handoff['recipient_handle']} will receive this follow-up after "
                "your message is published."
            ),
        }
    )


registry.register(
    name="handoff_group_task",
    toolset="bot_room",
    schema=HANDOFF_GROUP_TASK_SCHEMA,
    handler=lambda args, **_kwargs: handoff_group_task(
        args.get("recipient", ""),
        args.get("objective", ""),
    ),
    emoji="↪",
)


__all__ = ["HANDOFF_GROUP_TASK_SCHEMA", "handoff_group_task"]
