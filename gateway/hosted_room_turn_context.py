"""Verified per-turn provenance and typed Bot-to-Bot handoffs."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


MAX_HANDOFF_OBJECTIVE_BYTES = 8 * 1024
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RoomTurnContextError(ValueError):
    """Room turn provenance or a typed handoff is invalid."""


def _identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 128
        or _IDENTIFIER_RE.fullmatch(normalized) is None
    ):
        raise RoomTurnContextError(f"{field} is invalid")
    return normalized


def validate_turn_provenance(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RoomTurnContextError("turn provenance must be an object")
    kind = str(value.get("kind") or "")
    required = (
        {"kind", "user_event_id"}
        if kind == "user"
        else {
            "kind",
            "user_event_id",
            "handoff_event_id",
            "source_member_id",
        }
        if kind == "member_handoff"
        else set()
    )
    if not required or set(value) != required:
        raise RoomTurnContextError("turn provenance fields are invalid")
    normalized = {
        "kind": kind,
        "user_event_id": _identifier(value["user_event_id"], field="user_event_id"),
    }
    if kind == "member_handoff":
        normalized.update(
            handoff_event_id=_identifier(
                value["handoff_event_id"], field="handoff_event_id"
            ),
            source_member_id=_identifier(
                value["source_member_id"], field="source_member_id"
            ),
        )
    return normalized


def validate_handoff_targets(
    value: Any,
    *,
    current_member_id: str,
) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list) or len(value) > 5:
        raise RoomTurnContextError("handoff_targets must contain at most five Bots")
    result: list[dict[str, str]] = []
    member_ids: set[str] = set()
    handles: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"member_id", "handle"}:
            raise RoomTurnContextError("handoff target fields are invalid")
        member_id = _identifier(raw["member_id"], field="handoff member_id")
        handle = _identifier(raw["handle"], field="handoff handle")
        folded = handle.casefold()
        if (
            member_id == current_member_id
            or member_id in member_ids
            or folded in handles
        ):
            raise RoomTurnContextError("handoff targets are invalid")
        member_ids.add(member_id)
        handles.add(folded)
        result.append({"member_id": member_id, "handle": handle})
    return tuple(result)


@dataclass
class RoomTurnContext:
    current_member_id: str
    provenance: Mapping[str, str]
    targets: Sequence[Mapping[str, str]]
    _handoffs: list[dict[str, str]] = field(default_factory=list)

    def request_handoff(self, recipient: str, objective: str) -> dict[str, str]:
        lookup = str(recipient or "").strip().removeprefix("@").casefold()
        target = next(
            (
                candidate
                for candidate in self.targets
                if lookup
                in {
                    str(candidate["member_id"]).casefold(),
                    str(candidate["handle"]).casefold(),
                }
            ),
            None,
        )
        normalized_objective = str(objective or "").strip()
        if target is None:
            raise RoomTurnContextError("recipient is not an available Group Chat Bot")
        if (
            not normalized_objective
            or len(normalized_objective.encode("utf-8")) > MAX_HANDOFF_OBJECTIVE_BYTES
        ):
            raise RoomTurnContextError("handoff objective is empty or too large")
        handoff = {
            "recipient_member_id": str(target["member_id"]),
            "recipient_handle": str(target["handle"]),
            "objective": normalized_objective,
        }
        self._handoffs = [
            item
            for item in self._handoffs
            if item["recipient_member_id"] != handoff["recipient_member_id"]
        ]
        self._handoffs.append(handoff)
        return dict(handoff)

    def handoffs(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._handoffs]


_CURRENT_CONTEXT: ContextVar[RoomTurnContext | None] = ContextVar(
    "hosted_room_turn_context",
    default=None,
)


def room_turn_context_from_mapping(value: Mapping[str, Any]) -> RoomTurnContext:
    member_id = _identifier(value.get("member_id"), field="member_id")
    provenance = validate_turn_provenance(value.get("provenance"))
    targets = validate_handoff_targets(
        value.get("handoff_targets"),
        current_member_id=member_id,
    )
    return RoomTurnContext(
        current_member_id=member_id,
        provenance=provenance,
        targets=targets,
    )


def bind_room_turn_context(context: RoomTurnContext) -> Token:
    return _CURRENT_CONTEXT.set(context)


def reset_room_turn_context(token: Token) -> None:
    _CURRENT_CONTEXT.reset(token)


def current_room_turn_context() -> RoomTurnContext | None:
    return _CURRENT_CONTEXT.get()


__all__ = [
    "RoomTurnContext",
    "RoomTurnContextError",
    "bind_room_turn_context",
    "current_room_turn_context",
    "reset_room_turn_context",
    "room_turn_context_from_mapping",
    "validate_handoff_targets",
    "validate_turn_provenance",
]
