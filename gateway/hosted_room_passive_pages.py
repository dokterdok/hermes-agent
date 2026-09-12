"""Strict passive replay-page decoding; callers still own admission and storage.

Extracted from #104601 and consumed by the passive replica writer. A valid page is
not proof of enrolled lineage, authorized ingress, complete history or safety.
"""

from __future__ import annotations

import math
from typing import Any

from gateway.hosted_room_replicas import ReplicaError, ReplicaGapError
from gateway.hosted_rooms import (
    MAX_ACTOR_ID_CHARS, MAX_EVENT_ID_CHARS, MAX_EVENT_JSON_BYTES,
    MAX_LOG_LIMIT, MAX_LOG_PAGE_BYTES, MAX_ROOM_ID_CHARS,
    _canonical_json, _validate_actor, _validate_event_kind, _validate_identifier,
)


def _validate_non_negative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReplicaError(f"{label} must be a non-negative integer")
    return value


def validate_page(
    page: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], int, int, bool]:
    if not isinstance(page, dict):
        raise ReplicaError("page must be an object")
    v2 = "replica_version" in page
    if v2:
        if type(page["replica_version"]) is not int or page["replica_version"] != 2 or set(page) != {
            "events", "authority", "cursor", "latest_seq", "has_more", "replica_version", "lineage_sha256"
        }:
            raise ReplicaError("invalid v2 replica fields")
    elif "lineage_sha256" in page:
        raise ReplicaError("lineage digest requires v2")
    _canonical_json(page, label="page", max_bytes=MAX_LOG_PAGE_BYTES)
    events = page.get("events")
    authority = page.get("authority")
    if not isinstance(events, list):
        raise ReplicaError("page.events must be a list")
    if len(events) > MAX_LOG_LIMIT:
        raise ReplicaError(f"page.events cannot exceed {MAX_LOG_LIMIT} events")
    if not isinstance(authority, dict):
        raise ReplicaError("page.authority is required for replication")
    gateway_id = _validate_identifier(
        authority.get("gateway_id"),
        label="page.authority.gateway_id",
        max_chars=MAX_ACTOR_ID_CHARS,
    )
    epoch = authority.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ReplicaError("page.authority.epoch must be a positive integer")
    if v2 and (gateway_id != authority.get("gateway_id") or set(authority) != {"gateway_id", "epoch"} or epoch >= 2**63):
        raise ReplicaError("invalid v2 authority")
    cursor = _validate_non_negative_int(page.get("cursor"), label="page.cursor")
    latest_seq = _validate_non_negative_int(
        page.get("latest_seq"), label="page.latest_seq"
    )
    if v2 and (cursor >= 2**63 or latest_seq >= 2**63):
        raise ReplicaError("v2 replay coordinates exceed SQLite bounds")
    has_more = page.get("has_more")
    if not isinstance(has_more, bool):
        raise ReplicaError("page.has_more must be a boolean")
    if cursor > latest_seq:
        raise ReplicaError("page.cursor cannot exceed page.latest_seq")
    if has_more != (cursor < latest_seq):
        raise ReplicaError("page.has_more does not match its replay cursor")

    normalized_events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    previous_seq: int | None = None
    for event in events:
        if not isinstance(event, dict):
            raise ReplicaError("page events must be objects")
        seq = event.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ReplicaError("event.seq must be a positive integer")
        if previous_seq is not None and seq != previous_seq + 1:
            raise ReplicaGapError("page events must be contiguous")
        previous_seq = seq
        event_room_id = _validate_identifier(
            event.get("room_id"),
            label="event.room_id",
            max_chars=MAX_ROOM_ID_CHARS,
        )
        event_id = _validate_identifier(
            event.get("event_id"),
            label="event.event_id",
            max_chars=MAX_EVENT_ID_CHARS,
        )
        if event_id in event_ids:
            raise ReplicaError("page repeats an event_id")
        event_ids.add(event_id)
        kind = _validate_event_kind(event.get("kind"))
        actor, actor_json = _validate_actor(event.get("actor"), kind=kind)
        if v2 and (actor != event.get("actor") or event_id != event.get("event_id")
                or event_room_id != event.get("room_id") or kind != event.get("kind")):
            raise ReplicaError("v2 event identifiers and actors must be canonical")
        if not v2 and actor["kind"] == "gateway" and actor["id"] != gateway_id:
            raise ReplicaError("gateway actor does not match page authority")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ReplicaError("event.payload must be an object")
        payload_json = _canonical_json(
            payload, label="payload", max_bytes=MAX_EVENT_JSON_BYTES
        )
        event_epoch = event.get("authority_epoch")
        if (
            isinstance(event_epoch, bool)
            or not isinstance(event_epoch, int)
            or event_epoch < 1
            or event_epoch > epoch
        ):
            raise ReplicaError("event.authority_epoch is outside the page lineage")
        created_at = event.get("created_at")
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or not math.isfinite(float(created_at))
        ):
            raise ReplicaError("event.created_at must be a finite number")
        normalized_events.append(
            {
                "room_id": event_room_id,
                "seq": seq,
                "event_id": event_id,
                "kind": kind,
                "actor": actor,
                "actor_json": actor_json,
                "authority_epoch": event_epoch,
                "payload": payload,
                "payload_json": payload_json,
                "created_at": float(created_at),
            }
        )
    if normalized_events and normalized_events[-1]["seq"] != cursor:
        raise ReplicaError("page.cursor must equal the last returned sequence")
    if not normalized_events and cursor != latest_seq:
        raise ReplicaError("an incomplete replay page must include events")
    return (
        normalized_events,
        {"gateway_id": gateway_id, "epoch": epoch},
        cursor,
        latest_seq,
        has_more,
    )
