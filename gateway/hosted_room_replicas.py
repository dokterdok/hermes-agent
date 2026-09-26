"""Replica store and takeover primitives for hosted Group Chat rooms.

Non-authority gateways keep a durable local copy of the room log (``ingest_page()``: idempotent,
gap- and epoch-regression-safe). ``promote_replica()`` copies that log locally at ``epoch + 1`` and
records ``authority.claimed`` with ``promoted_from_replica`` so Retention can quarantine the takeover.
It does not prove the previous host stopped, and it does not admit new events or driver tasks.
``demote_room()`` records ``authority.lost`` when a returning stale authority is shown a newer epoch.
This is not host-loss recovery.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing, contextmanager
from functools import partial
from typing import Any, Iterator

from gateway.hosted_rooms import (
    MAX_ACTOR_ID_CHARS, MAX_EVENT_ID_CHARS, HostedRoomError, RoomConflictError, _actor_json, _connect,
    _payload_json, _room_id, _transaction, _validate_actor, _validate_event_kind, _validate_identifier,
    _validate_members, _validate_room_name, local_authority_gateway_id)
from gateway.hosted_rooms_common import DbPath, bounded_int, clock, utf8_len

MAX_REPLICA_ROOMS = 256
MAX_REPLICA_EVENT_BYTES = 256 * 1024 * 1024
_SYSTEM_ACTOR = {"kind": "system", "id": "authority-control"}
_EVENT_COLUMNS = "(room_id, seq, event_id, kind, actor_json, authority_epoch, payload_json, created_at)"
_INSERT_ROOM_EVENT = f"INSERT INTO hosted_room_events {_EVENT_COLUMNS} VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
_INSERT_REPLICA_EVENT = f"INSERT INTO hosted_room_replica_events {_EVENT_COLUMNS} VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
_SELECT_REPLICA = "SELECT * FROM hosted_room_replicas WHERE room_id=?"


class ReplicaError(HostedRoomError): """Base class for invalid or conflicting replica operations."""
class ReplicaGapError(ReplicaError): """A page does not start at the replica's next expected sequence."""
class ReplicaEpochRegressionError(ReplicaError): """A page or demotion carries an older authority epoch than stored."""


def _initialize_replica_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hosted_room_replicas (
            room_id TEXT PRIMARY KEY, name TEXT NOT NULL, members_json TEXT NOT NULL,
            authority_gateway_id TEXT NOT NULL,
            authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
            last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
            latest_seq INTEGER NOT NULL DEFAULT 0, event_bytes INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS hosted_room_replica_events (
            room_id TEXT NOT NULL, seq INTEGER NOT NULL CHECK (seq >= 1), event_id TEXT NOT NULL,
            kind TEXT NOT NULL, actor_json TEXT NOT NULL, authority_epoch INTEGER,
            payload_json TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY (room_id, seq)
        )""")


@contextmanager
def _replica_transaction(db_path: DbPath) -> Iterator[sqlite3.Connection]:
    """Ensure the replica schema (own autocommit connection), then open an IMMEDIATE transaction. The DDL is
    deliberately re-run inside the transaction: that double init is the established statement order."""
    with closing(_connect(db_path)) as conn, conn:
        _initialize_replica_schema(conn)
    with _transaction(db_path, immediate=True) as conn:
        _initialize_replica_schema(conn)
        yield conn


_positive_int = partial(bounded_int, error=ReplicaError, low=1)


def _control_event(kind: str, epoch: int, payload: dict[str, Any]) -> tuple[str, str, str, str]:
    """(event_id, kind, actor_json, payload_json) of the system ``authority.<kind>`` control event for ``epoch``."""
    return f"system:authority-{kind}:{epoch}", f"authority.{kind}", _actor_json(_SYSTEM_ACTOR), _payload_json(payload)


def _append_control_event(
    conn: sqlite3.Connection, room_id: str, seq: int, epoch: int, event: tuple[str, str, str, str], now: float
) -> None:
    event_id, kind, actor_json, payload_json = event
    conn.execute(_INSERT_ROOM_EVENT, (room_id, seq, event_id, kind, actor_json, epoch, payload_json, now))


def _event_bytes(event: dict[str, Any]) -> int:
    return utf8_len(
        str(event["event_id"]), str(event["kind"]),
        json.dumps(event["actor"], ensure_ascii=False, separators=(",", ":")),
        json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":")))


def _validate_page(page: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(page, dict):
        raise ReplicaError("page must be an object")
    events, authority = page.get("events"), page.get("authority")
    if not isinstance(events, list):
        raise ReplicaError("page.events must be a list")
    if not isinstance(authority, dict):
        raise ReplicaError("page.authority is required for replication")
    gateway_id = _validate_identifier(
        authority.get("gateway_id"), label="page.authority.gateway_id", max_chars=MAX_ACTOR_ID_CHARS)
    epoch = _positive_int(authority.get("epoch"), message="page.authority.epoch must be a positive integer")
    previous_seq: int | None = None
    for event in events:
        if not isinstance(event, dict):
            raise ReplicaError("page events must be objects")
        seq = _positive_int(event.get("seq"), message="event.seq must be a positive integer")
        if previous_seq is not None and seq != previous_seq + 1:
            raise ReplicaGapError("page events must be contiguous")
        previous_seq = seq
        for field in ("event_id", "kind"):
            if not isinstance(event.get(field), str) or not event[field]:
                raise ReplicaError(f"event.{field} must be a non-empty string")
        if not isinstance(event.get("actor"), dict):
            raise ReplicaError("event.actor must be an object")
        if "payload" not in event:
            raise ReplicaError("event.payload is required")
    return events, {"gateway_id": gateway_id, "epoch": epoch}


def _replica_row_state(conn: sqlite3.Connection, room_id: str) -> tuple[sqlite3.Row | None, int, int, int]:
    """Return (row, stored_epoch, last_seq, stored_bytes); a new room is admitted only under the room cap."""
    row = conn.execute("""SELECT authority_gateway_id, authority_epoch, last_seq, latest_seq, event_bytes
             FROM hosted_room_replicas WHERE room_id=?""", (room_id,)).fetchone()
    if row is None:
        count = conn.execute("SELECT COUNT(*) FROM hosted_room_replicas").fetchone()[0]
        if int(count) >= MAX_REPLICA_ROOMS:
            raise ReplicaError("replica room capacity exhausted")
        return None, 0, 0, 0
    return row, int(row["authority_epoch"]), int(row["last_seq"]), int(row["event_bytes"])


def _store_replica(
    conn: sqlite3.Connection, *, is_new: bool, room_id: str, room_name: str, members_json: str,
    authority: dict[str, Any], new_last: int, latest_seq: int, added_bytes: int, now: float) -> None:
    """INSERT the replica row for a new room, else UPDATE it (event_bytes accumulates)."""
    values = (room_name, members_json, authority["gateway_id"], authority["epoch"], new_last, max(latest_seq, new_last))
    if is_new:
        # A live authority room, a retired replica, or any other owner already holds this id.
        # The replica insert trigger aborts that collision; refuse before the double insert.
        if _reservation_owner(conn, room_id) is not None:
            raise RoomConflictError("room_id is already reserved")
        conn.execute("""INSERT INTO hosted_room_replicas (room_id, name, members_json,
                authority_gateway_id, authority_epoch, last_seq, latest_seq, event_bytes,
                created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_id, *values, added_bytes, now, now))
    else:
        conn.execute("""UPDATE hosted_room_replicas SET name=?, members_json=?, authority_gateway_id=?,
                authority_epoch=?, last_seq=?, latest_seq=?, event_bytes=event_bytes+?,
                updated_at=? WHERE room_id=?""", (*values, added_bytes, now, room_id))


def ingest_page(
    db_path: DbPath, *, room_id: Any, room_name: Any, members: Any, page: Any, now: float | None = None
) -> dict[str, Any]:
    """Persist one verbatim ``read_events()`` page idempotently; refuses seq gaps and epoch regressions."""
    room_id = _room_id(room_id)
    room_name = _validate_room_name(room_name)
    _, members_json = _validate_members(members)
    events, authority = _validate_page(page)
    now = clock(now)
    with _replica_transaction(db_path) as conn:
        row, stored_epoch, last_seq, stored_bytes = _replica_row_state(conn, room_id)
        if authority["epoch"] < stored_epoch:
            raise ReplicaEpochRegressionError("page authority epoch is older than the stored replica epoch")
        new_events = [e for e in events if int(e["seq"]) > last_seq]
        if new_events and int(new_events[0]["seq"]) != last_seq + 1:
            raise ReplicaGapError("page skips sequences the replica has not stored")
        added_bytes = 0
        for event in new_events:
            size = _event_bytes(event)
            if stored_bytes + added_bytes + size > MAX_REPLICA_EVENT_BYTES:
                raise ReplicaError("replica event storage exhausted")
            conn.execute(
                _INSERT_REPLICA_EVENT,
                (
                    room_id, int(event["seq"]), event["event_id"], event["kind"], _actor_json(event["actor"]),
                    event.get("authority_epoch"), _payload_json(event["payload"]),
                    float(event.get("created_at") or now)))
            added_bytes += size
        new_last = int(new_events[-1]["seq"]) if new_events else last_seq
        latest_seq = page.get("latest_seq")
        if isinstance(latest_seq, bool) or not isinstance(latest_seq, int):
            latest_seq = new_last
        _store_replica(
            conn, is_new=row is None, room_id=room_id, room_name=room_name, members_json=members_json,
            authority=authority, new_last=new_last, latest_seq=latest_seq, added_bytes=added_bytes, now=now)
    return {
        "room_id": room_id, "stored_seq": new_last, "ingested": len(new_events), "authority": authority,
        "caught_up": new_last >= max(latest_seq, new_last)}


def replica_state(db_path: DbPath, *, room_id: Any) -> dict[str, Any]:
    """Return the stored replica's coverage and authority lineage."""
    room_id = _room_id(room_id)
    with _replica_transaction(db_path) as conn:
        row = conn.execute(_SELECT_REPLICA, (room_id,)).fetchone()
    if row is None:
        raise ReplicaError("replica not found")
    return {
        "room_id": row["room_id"], "name": row["name"], "members": json.loads(row["members_json"]),
        "authority": {"gateway_id": row["authority_gateway_id"], "epoch": int(row["authority_epoch"])},
        "last_seq": int(row["last_seq"]), "latest_seq": int(row["latest_seq"]), "event_bytes": int(row["event_bytes"]),
        "created_at": float(row["created_at"]), "updated_at": float(row["updated_at"])}


def _reservation_owner(conn: sqlite3.Connection, room_id: str) -> str | None:
    """Return the Retention reservation owner, or None when that table is absent."""
    from gateway.hosted_rooms_common import table_exists
    if not table_exists(conn, "hosted_room_id_reservations"):
        return None
    row = conn.execute(
        "SELECT owner_kind FROM hosted_room_id_reservations WHERE room_id=?", (room_id,)).fetchone()
    return None if row is None else str(row["owner_kind"])


def _release_consumed_replica_reservation(conn: sqlite3.Connection, room_id: str) -> None:
    """Move a replica-owned id reservation out of the way of the authority insert.

    ``trg_hosted_rooms_reject_reserved_insert`` aborts any ``hosted_rooms`` insert
    while a reservation exists, and the safety schema never deletes that row when
    the replica is consumed. Delete only ``owner_kind='replica'``. The room
    insert's reserve trigger then records ``authority``. Any other owner stays.
    """
    owner = _reservation_owner(conn, room_id)
    if owner is None:
        return
    if owner != "replica":
        raise RoomConflictError("room_id is already reserved")
    deleted = conn.execute(
        "DELETE FROM hosted_room_id_reservations WHERE room_id=? AND owner_kind='replica'", (room_id,))
    if deleted.rowcount != 1:
        raise RoomConflictError("room_id is already reserved")


def _raise_if_quarantined(conn: sqlite3.Connection, room_id: str) -> None:
    """Honor Retention quarantine when that schema is installed. No-op otherwise."""
    from importlib.util import find_spec
    if find_spec("gateway.hosted_room_safety") is None:
        return
    from gateway.hosted_room_safety import _raise_if_quarantined as raise_quarantined
    raise_quarantined(conn, room_id)


def promote_replica(
    db_path: DbPath, *, room_id: Any, reason: Any = "authority-unreachable", now: float | None = None
) -> dict[str, Any]:
    """Copy a replicated room onto this gateway at ``epoch + 1`` without executing it.

    The claim keeps ``promoted_from_replica`` so Retention's unsafe-lineage trigger and
    backfill quarantine the takeover. A confirmation flag, the new epoch, and releasing
    the replica reservation are not proof the previous host is fenced. Admission stays
    closed until that proof exists. This function does not implement host-loss recovery.
    """
    room_id = _room_id(room_id)
    if not isinstance(reason, str) or not reason or len(reason) > 200:
        raise ReplicaError("reason must be a non-empty string of at most 200 chars")
    now = clock(now)
    local_gateway = local_authority_gateway_id()
    with _replica_transaction(db_path) as conn:
        replica = conn.execute(_SELECT_REPLICA, (room_id,)).fetchone()
        if replica is None:
            raise ReplicaError("replica not found")
        if replica["authority_gateway_id"] == local_gateway:
            raise ReplicaError("this gateway already holds the room authority")
        if conn.execute("SELECT 1 FROM hosted_rooms WHERE room_id=?", (room_id,)).fetchone():
            raise RoomConflictError("room_id already exists in the local authoritative store")
        if conn.execute("SELECT 1 FROM hosted_room_retired_ids WHERE room_id=?", (room_id,)).fetchone():
            raise RoomConflictError("room_id belongs to a disbanded room")
        _raise_if_quarantined(conn, room_id)
        if "quarantine_reason" in replica.keys() and replica["quarantine_reason"] is not None:
            from gateway.hosted_rooms import RoomQuarantinedError
            raise RoomQuarantinedError(
                "This Group Chat has an unverified authority takeover and is read-only "
                f"until its history is reconciled ({replica['quarantine_reason']}).")
        # The replica insert already reserved this id. Release that replica owner so
        # the room insert can reserve it as authority. Do not clear any other owner.
        _release_consumed_replica_reservation(conn, room_id)
        previous_gateway, previous_epoch = str(replica["authority_gateway_id"]), int(replica["authority_epoch"])
        target_epoch, claim_seq = previous_epoch + 1, int(replica["last_seq"]) + 1
        # Retention classifies this fragment as an unverified takeover
        # (trg_hosted_events_quarantine_unsafe_lineage and the safety backfill).
        # Omitting it lets confirm=true become executable authority. claim_authority
        # stays without the fragment: that path is a compare-and-swap on one store.
        claim = _control_event("claimed", target_epoch, {
            "previous_gateway_id": previous_gateway, "authority_gateway_id": local_gateway,
            "authority_epoch": target_epoch, "promoted_from_replica": True, "reason": reason})
        conn.execute("""INSERT INTO hosted_rooms
               (room_id, name, members_json, authority_gateway_id, authority_epoch, next_seq, event_bytes,
                revision, created_at, updated_at, disbanded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL)""",
            (
                room_id, replica["name"], replica["members_json"], local_gateway, target_epoch, claim_seq + 1,
                int(replica["event_bytes"]) + utf8_len(*claim), now, now))
        # Drop the replica copy from the shared safety budget before the authority
        # copy is inserted. Copy-then-delete counts the same bytes twice and the
        # budget trigger aborts a replica that already fits.
        copied = [
            tuple(row) for row in conn.execute(
                f"""SELECT room_id, seq, event_id, kind, actor_json, authority_epoch, payload_json, created_at
                      FROM hosted_room_replica_events WHERE room_id=? ORDER BY seq""",
                (room_id,))]
        conn.execute("DELETE FROM hosted_room_replica_events WHERE room_id=?", (room_id,))
        conn.executemany(_INSERT_ROOM_EVENT, copied)
        _append_control_event(conn, room_id, claim_seq, target_epoch, claim, now)
        conn.execute("DELETE FROM hosted_room_replicas WHERE room_id=?", (room_id,))
    return {
        "room_id": room_id, "authority_gateway_id": local_gateway, "authority_epoch": target_epoch,
        "previous_gateway_id": previous_gateway, "previous_epoch": previous_epoch, "claim_seq": claim_seq,
        "latest_seq": claim_seq, "executable": False}


def demote_room(
    db_path: DbPath, *, room_id: Any, observed_gateway_id: Any, observed_epoch: Any, now: float | None = None
) -> dict[str, Any]:
    """Fence THIS gateway's stale room authority against a proven newer epoch.

    When a returning gateway observes (replicated ``authority.claimed`` or a transport rejection) that another
    gateway owns the room at a higher epoch, append ``authority.lost`` and adopt the observed lineage so no
    local send can commit at the stale epoch. Idempotent per lineage.
    """
    room_id = _room_id(room_id)
    observed_gateway_id = _validate_identifier(
        observed_gateway_id, label="observed_gateway_id", max_chars=MAX_ACTOR_ID_CHARS)
    observed_epoch = _positive_int(observed_epoch, message="observed_epoch must be a positive integer")
    now = clock(now)
    local_gateway = local_authority_gateway_id()
    with _transaction(db_path, immediate=True) as conn:
        row = conn.execute("""SELECT authority_gateway_id, authority_epoch, next_seq
                 FROM hosted_rooms WHERE room_id=? AND disbanded_at IS NULL""", (room_id,)).fetchone()
        if row is None:
            raise ReplicaError("room not found in the local authoritative store")
        current_gateway, current_epoch = str(row["authority_gateway_id"]), int(row["authority_epoch"])
        if current_gateway == observed_gateway_id and current_epoch == observed_epoch:
            return {
                "room_id": room_id, "authority_gateway_id": current_gateway, "authority_epoch": current_epoch,
                "idempotent": True}
        if observed_epoch <= current_epoch:
            raise ReplicaEpochRegressionError("observed epoch does not supersede the stored authority")
        if current_gateway != local_gateway:
            raise ReplicaError("room is not locally authoritative; nothing to demote")
        # An already-quarantined room cannot accept another authority.lost. The
        # idempotent same-lineage return above stays available.
        _raise_if_quarantined(conn, room_id)
        lost = _control_event("lost", observed_epoch, {
            "previous_gateway_id": current_gateway, "authority_gateway_id": observed_gateway_id,
            "authority_epoch": observed_epoch})
        _append_control_event(conn, room_id, int(row["next_seq"]), observed_epoch, lost, now)
        conn.execute("""UPDATE hosted_rooms
                  SET authority_gateway_id=?, authority_epoch=?, next_seq=next_seq+1, revision=revision+1, updated_at=?
                WHERE room_id=?""",
            (observed_gateway_id, observed_epoch, now, room_id))
    return {
        "room_id": room_id, "authority_gateway_id": observed_gateway_id, "authority_epoch": observed_epoch,
        "idempotent": False}


def _audit_existing_replicas_locked(conn: sqlite3.Connection) -> None:
    """Quarantine lineage written by the pre-fix replica implementation."""
    for row in conn.execute(
        """SELECT room_id, authority_gateway_id, authority_epoch, last_seq,
                  latest_seq, event_bytes, disbanded_at, quarantine_reason
             FROM hosted_room_replicas"""
    ).fetchall():
        room_id = str(row["room_id"])
        events = conn.execute(
            """SELECT seq, event_id, authority_epoch, kind, actor_json,
                      payload_json, created_at
                 FROM hosted_room_replica_events
                WHERE room_id=? ORDER BY seq""",
            (room_id,),
        ).fetchall()
        reasons: list[str] = []
        seqs = [int(event["seq"]) for event in events]
        event_ids = [str(event["event_id"]) for event in events]
        last_seq = int(row["last_seq"])
        latest_seq = int(row["latest_seq"])
        if int(row["authority_epoch"]) != 1:
            reasons.append("unverified_authority_epoch")
        if seqs != list(range(1, last_seq + 1)):
            reasons.append("non_contiguous_history")
        if len(set(event_ids)) != len(event_ids):
            reasons.append("duplicate_event_id")
        if latest_seq < last_seq:
            reasons.append("coverage_regression")
        disband_positions = [
            index for index, event in enumerate(events)
            if event["kind"] == "room.disbanded"
        ]
        if disband_positions and disband_positions != [len(events) - 1]:
            reasons.append("events_after_disband")
        if disband_positions and last_seq != latest_seq:
            reasons.append("incomplete_terminal_history")
        if any(
            event["authority_epoch"] != int(row["authority_epoch"])
            for event in events
        ):
            reasons.append("mixed_authority_lineage")
        try:
            _validate_identifier(
                row["authority_gateway_id"],
                label="authority_gateway_id",
                max_chars=MAX_ACTOR_ID_CHARS,
            )
            for event in events:
                kind = _validate_event_kind(event["kind"])
                _validate_identifier(
                    event["event_id"],
                    label="event_id",
                    max_chars=MAX_EVENT_ID_CHARS,
                )
                actor, _ = _validate_actor(
                    json.loads(event["actor_json"]), kind=kind
                )
                if (
                    actor["kind"] == "gateway"
                    and actor["id"] != str(row["authority_gateway_id"])
                ):
                    reasons.append("gateway_actor_authority_mismatch")
                payload = json.loads(event["payload_json"])
                if not isinstance(payload, dict):
                    raise ReplicaError("event payload is not an object")
                if not math.isfinite(float(event["created_at"])):
                    raise ReplicaError("event timestamp is not finite")
        except (HostedRoomError, TypeError, ValueError, json.JSONDecodeError):
            reasons.append("invalid_event_shape")

        recomputed_bytes = sum(
            len(str(event["event_id"]).encode("utf-8"))
            + len(str(event["kind"]).encode("utf-8"))
            + len(str(event["actor_json"]).encode("utf-8"))
            + len(str(event["payload_json"]).encode("utf-8"))
            for event in events
        )
        if recomputed_bytes != int(row["event_bytes"]):
            conn.execute(
                "UPDATE hosted_room_replicas SET event_bytes=? WHERE room_id=?",
                (recomputed_bytes, room_id),
            )
        if reasons and row["quarantine_reason"] is None:
            conn.execute(
                """UPDATE hosted_room_replicas
                      SET quarantined_at=?, quarantine_reason=?
                    WHERE room_id=?""",
                (time.time(), reasons[0], room_id),
            )


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.
from pathlib import Path  # noqa: F401,E402
import time  # noqa: F401,E402


_PLUGIN_COMPAT_LAZY = {
    'MAX_EVENT_JSON_BYTES': ('gateway.hosted_rooms', 'MAX_EVENT_JSON_BYTES'),
    'MAX_ROOM_ID_CHARS': ('gateway.hosted_rooms', 'MAX_ROOM_ID_CHARS'),
}


def __getattr__(name):  # PEP 562 — lazy so no import cycles
    target = _PLUGIN_COMPAT_LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    from hermes_cli.plugin_compat import warn_once
    warn_once(__name__, name, *target)
    return getattr(importlib.import_module(target[0]), target[1])
# ---- END PLUGIN-COMPAT ----
