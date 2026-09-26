"""Behavior of the hosted Stop fence."""

from tui_gateway.hosted_stop_fence import (
    EXACT, INVALID, NOT_INTERRUPTED, USER, classify_hosted_stop,
)


def _classify(**overrides):
    params = dict(
        expected_task_id=None,
        expected_execution_generation=None,
        generation_supplied=False,
        running=True,
        hosted_task={"task_id": "task-a", "execution_generation": 4},
    )
    params.update(overrides)
    return classify_hosted_stop(**params)


def test_user_stop_ignores_a_live_hosted_task():
    assert _classify() == USER


def test_task_id_alone_does_not_interrupt():
    assert _classify(expected_task_id="task-a") == NOT_INTERRUPTED


def test_generation_alone_does_not_interrupt():
    assert _classify(
        expected_execution_generation=4, generation_supplied=True) == NOT_INTERRUPTED


def test_wrong_type_generation_is_invalid_and_not_coerced():
    assert _classify(
        expected_task_id="task-a",
        expected_execution_generation=True,
        generation_supplied=True,
    ) == INVALID
    assert _classify(
        expected_task_id="task-a",
        expected_execution_generation="4",
        generation_supplied=True,
    ) == INVALID


def test_exact_pair_matches_only_the_running_marker():
    assert _classify(
        expected_task_id="task-a",
        expected_execution_generation=4,
        generation_supplied=True,
    ) == EXACT
    assert _classify(
        expected_task_id="task-a",
        expected_execution_generation=5,
        generation_supplied=True,
    ) == NOT_INTERRUPTED
    assert _classify(
        expected_task_id="task-b",
        expected_execution_generation=4,
        generation_supplied=True,
    ) == NOT_INTERRUPTED
    assert _classify(
        expected_task_id="task-a",
        expected_execution_generation=4,
        generation_supplied=True,
        running=False,
    ) == NOT_INTERRUPTED
