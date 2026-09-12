"""Deterministic publisher fairness without threads or HTTP."""

from dataclasses import replace

import pytest

from tui_gateway.hosted_room_replication_selection import PassiveDeliveryRoute, select_route


def route(member, *, history="acked", work="pending", opted=True, version=2, updated=0):
    return PassiveDeliveryRoute(("room", member), history, work, opted, version, updated)


@pytest.mark.parametrize("ready,history_pending,work_status,expected", [
    (True, True, "pending", "work"),
    (True, True, "unavailable", "work"),
    (False, True, "pending", "history"),
    (True, True, "rejected", "history"),
    (True, True, "needs_reauthorization", "history"),
    (True, False, "pending", "work"),
])
def test_anchored_work_and_unblocked_history_each_get_a_turn(ready, history_pending, work_status, expected):
    candidates = [route("history", opted=False), route("work", history="unavailable", work=work_status)]
    selected = select_route(candidates, initial_key=("room", "history"), authority_epoch=2,
                            work_ready=ready, history_pending=history_pending)
    assert selected.key == ("room", expected)


@pytest.mark.parametrize("epoch", [1, 2])
def test_failed_work_and_negotiation_rotate_and_known_history_only_cannot_starve_them(epoch):
    candidates = [
        route("a-probe", history="unavailable", work="pending" if epoch == 2 else "unavailable", version=None),
        route("b-work", work="unavailable"),
        route("c-history-only", opted=epoch == 2, version=1),
        route("d-blocked", history="needs_reauthorization"),
    ]
    observed = []
    for turn in range(1, 7):
        initial = candidates[(turn - 1) % 2].key
        selected = select_route(candidates, initial_key=initial, authority_epoch=epoch,
                                work_ready=True, history_pending=True)
        observed.append(selected.key[1])
        candidates = [replace(candidate, updated_at=turn) if candidate == selected else candidate
                      for candidate in candidates]
    assert observed.count("a-probe") == observed.count("b-work") == 3
    assert all(left != right for left, right in zip(observed, observed[1:]))
    assert select_route([route("blocked", history="invalid_ack")], initial_key=("room", "blocked"),
                        authority_epoch=epoch, work_ready=True, history_pending=True) is None
