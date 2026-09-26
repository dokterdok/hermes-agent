"""Exact hosted Stop fence shared by session.interrupt.

A user Stop supplies neither hosted coordinate and still stops the live turn.
A hosted Stop is the pair (task id, execution generation). Either coordinate
alone, or a pair that is not the running hosted task, does not interrupt.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

USER = "user"
EXACT = "exact"
NOT_INTERRUPTED = "not_interrupted"
INVALID = "invalid"


def classify_hosted_stop(
    *,
    expected_task_id: object,
    expected_execution_generation: object,
    generation_supplied: bool,
    running: bool,
    hosted_task: Mapping[str, Any] | None,
) -> str:
    """Classify one interrupt request against the live hosted task marker.

    ``generation_supplied`` is true only when the caller sent a non-null
    execution generation. A missing generation is not coerced from the task id.
    """
    task_id = expected_task_id.strip() if isinstance(expected_task_id, str) else ""
    if not task_id and not generation_supplied:
        return USER
    if generation_supplied and (
        type(expected_execution_generation) is not int or expected_execution_generation < 1
    ):
        return INVALID
    if not task_id or not generation_supplied:
        return NOT_INTERRUPTED
    live_generation = hosted_task.get("execution_generation") if isinstance(hosted_task, Mapping) else None
    if (
        running
        and isinstance(hosted_task, Mapping)
        and hosted_task.get("task_id") == task_id
        and type(live_generation) is int
        and live_generation == expected_execution_generation
    ):
        return EXACT
    return NOT_INTERRUPTED
