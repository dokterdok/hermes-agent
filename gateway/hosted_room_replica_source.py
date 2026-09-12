"""Passive page snapshots over canonical history, without a new history writer."""

from __future__ import annotations

import json
from typing import Any

from gateway.hosted_rooms_common import DbPath, utf8_len
from gateway.hosted_rooms import (
    HostedRoomError, MAX_LOG_LIMIT, MAX_LOG_PAGE_BYTES, _EVENT_COLUMNS,
    _bounded_limit, _event_from_row, _non_negative, _room_id, _room_row, _transaction,
)


def read_replica_page(
    db_path: DbPath, *, room_id: Any, since_seq: Any = 0, limit: Any = 100, include_disbanded: bool = False,
    replica_version: int | None = None,
) -> dict[str, Any]:
    """Read a monotonic delta; passive v2 pins its lineage in the same SQLite view."""
    if replica_version is not None and (type(replica_version) is not int or replica_version != 2):
        raise HostedRoomError("unsupported replica version")
    extra = {}
    room_id = _room_id(room_id)
    since_seq = _non_negative(since_seq, "since_seq")
    limit = _bounded_limit(limit, MAX_LOG_LIMIT)
    with _transaction(db_path) as conn:
        if replica_version == 2:
            # The default read transaction helper does not BEGIN for SELECTs.
            # Pin the head, descriptor and events before another writer claims.
            conn.execute("BEGIN")
        room = _room_row(
            conn, """SELECT next_seq, authority_gateway_id, authority_epoch FROM hosted_rooms
                WHERE room_id=? AND (disbanded_at IS NULL OR ?)""", (room_id, int(include_disbanded)), room_id)
        latest_seq = int(room["next_seq"]) - 1
        authority = {"gateway_id": str(room["authority_gateway_id"]), "epoch": int(room["authority_epoch"])}
        if replica_version == 2:
            from gateway.hosted_room_passive_lineage import source_locked
            _, digest = source_locked(conn, room_id, authority)
            extra = {"replica_version": 2, "lineage_sha256": digest}
        if since_seq > latest_seq:
            raise HostedRoomError("since_seq is ahead of the hosted room log")
        rows = conn.execute(
            f"""WITH candidates AS (
                   SELECT {_EVENT_COLUMNS},
                          SUM(
                              LENGTH(CAST(event_id AS BLOB)) +
                              LENGTH(CAST(kind AS BLOB)) +
                              LENGTH(CAST(actor_json AS BLOB)) +
                              LENGTH(CAST(payload_json AS BLOB))
                          ) OVER (ORDER BY seq ASC) AS cumulative_bytes
                     FROM hosted_room_events
                    WHERE room_id=? AND seq>?
                    ORDER BY seq ASC LIMIT ?
               )
               SELECT {_EVENT_COLUMNS}
                 FROM candidates
                WHERE cumulative_bytes<=?
                ORDER BY seq ASC""", (room_id, since_seq, limit, MAX_LOG_PAGE_BYTES)).fetchall()
    events = [_event_from_row(row) for row in rows]
    def build_page(page_events: list[dict[str, Any]]) -> dict[str, Any]:
        cursor = page_events[-1]["seq"] if page_events else since_seq
        return {"events": page_events, "cursor": cursor, "latest_seq": latest_seq, "has_more": cursor < latest_seq,
                "authority": authority, **extra}
    def fits(page_events: list[dict[str, Any]]) -> bool:
        page_json = json.dumps(build_page(page_events), ensure_ascii=False, separators=(",", ":"))
        return utf8_len(page_json) <= MAX_LOG_PAGE_BYTES
    if events and not fits(events):
        # Binary-search the largest prefix whose serialized page fits the budget.
        low, high = 1, len(events)
        while low < high:
            middle = (low + high + 1) // 2
            low, high = (middle, high) if fits(events[:middle]) else (low, middle - 1)
        events = events[:low]
        if not fits(events):
            raise HostedRoomError("hosted room event exceeds replay page limit")
    return build_page(events)
