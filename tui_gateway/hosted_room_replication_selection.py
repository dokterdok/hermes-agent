"""Pure route ranking for the future passive publisher, not an execution queue.

The caller supplies only currently authorized, generation-bound routes while
holding the target copy lock. Selection never validates grants or sends data.
"""

from dataclasses import dataclass
from typing import Iterable

from gateway.hosted_room_work_records import BLOCKED_DELIVERY_STATUSES


BLOCKED_HISTORY_STATUSES = frozenset({
    "needs_reauthorization", "replica_rejected", "invalid_ack", "source_gap", "unsupported_lineage",
})


@dataclass(frozen=True)
class PassiveDeliveryRoute:
    key: tuple[str, str]
    history_status: str
    work_status: str
    work_opted_in: bool
    work_record_version: int | None
    updated_at: float


def select_route(
    routes: Iterable[PassiveDeliveryRoute], *, initial_key: tuple[str, str],
    authority_epoch: int, work_ready: bool, history_pending: bool,
) -> PassiveDeliveryRoute | None:
    """Rotate work/probe retries without starving history or anchored work.

    ``work_ready`` must come from a validated pending record at an acknowledged
    history anchor. ``history_pending`` includes an unacked page or unhealthy
    history checkpoint, not merely a comparison with the latest source cursor.
    Unknown v2 capability earns negotiation, never a blind work-record send.
    """
    selected = []
    for route in routes:
        if route.history_status in BLOCKED_HISTORY_STATUSES:
            continue
        opted_in = route.work_opted_in and (
            authority_epoch == 1 or route.work_record_version in (None, 2)
        )
        refused = route.work_status in BLOCKED_DELIVERY_STATUSES
        work_rank = 2 if opted_in and refused else 0 if opted_in else 1
        unavailable = route.history_status == "unavailable"
        work_unavailable = route.work_status == "unavailable" or (
            unavailable and authority_epoch != 1 and route.work_record_version is None
        )
        turn_rank = 0
        if work_rank == 0 and work_unavailable:
            turn_rank = route.updated_at if authority_epoch != 1 else route.key != initial_key
        selected.append((unavailable, work_rank, work_unavailable, turn_rank, route.key, route))

    can_deliver_work = work_ready and any(item[1] == 0 for item in selected)
    history_needed = not can_deliver_work and history_pending

    def rank(item):
        unavailable, work_rank, work_unavailable, turn_rank, key, _ = item
        if history_needed:
            return unavailable, work_rank, work_unavailable, turn_rank, key
        return work_rank, work_unavailable, turn_rank, unavailable, key

    return min(selected, key=rank)[5] if selected else None
