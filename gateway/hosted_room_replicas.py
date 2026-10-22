"""Replica store and takeover primitives for hosted Group Chat rooms.

Non-authority gateways keep a durable local copy of the room log (``ingest_page()``: idempotent,
gap- and epoch-regression-safe) plus fenced primitives to continue the room when the authority host
dies: ``promote_replica()`` resumes locally at ``epoch + 1`` with a lineage-proving ``authority.claimed``
event; ``demote_room()`` records ``authority.lost`` when a returning stale authority is shown a newer
epoch. Storage primitives only: the caller decides *when* takeover is safe.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from functools import partial
from typing import Any, Iterator

from gateway.hosted_rooms import (
    MAX_ACTOR_ID_CHARS, HostedRoomError, RoomConflictError, _actor_json, _connect, _payload_json, _room_id,
    _transaction, _validate_identifier, _validate_members, _validate_room_name, local_authority_gateway_id)
from gateway.hosted_rooms_common import DbPath, bounded_int, clock, table_columns, utf8_len

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
            created_at REAL NOT NULL, updated_at REAL NOT NULL, lineage_gateway_id TEXT
        )""")
    # authority_* is the newest authority a page announced (it fences older pages); lineage_gateway_id is the
    # gateway of the span the stored events reach, when known. They differ while a claim is still to come.
    if "lineage_gateway_id" not in table_columns(conn, "hosted_room_replicas"):
        conn.execute("ALTER TABLE hosted_room_replicas ADD COLUMN lineage_gateway_id TEXT")
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
    row = conn.execute("""SELECT authority_gateway_id, authority_epoch, last_seq, latest_seq, event_bytes,
             lineage_gateway_id FROM hosted_room_replicas WHERE room_id=?""", (room_id,)).fetchone()
    if row is None:
        count = conn.execute("SELECT COUNT(*) FROM hosted_room_replicas").fetchone()[0]
        if int(count) >= MAX_REPLICA_ROOMS:
            raise ReplicaError("replica room capacity exhausted")
        return None, 0, 0, 0
    return row, int(row["authority_epoch"]), int(row["last_seq"]), int(row["event_bytes"])


def _check_replay(conn: sqlite3.Connection, room_id: str, replayed: list[dict[str, Any]]) -> None:
    """Refuse already-stored sequences whose events differ: the page belongs to a different room history."""
    if not replayed:
        return
    stored = conn.execute("""SELECT event_id, kind, actor_json, authority_epoch, payload_json
             FROM hosted_room_replica_events WHERE room_id=? AND seq BETWEEN ? AND ? ORDER BY seq""",
        (room_id, int(replayed[0]["seq"]), int(replayed[-1]["seq"]))).fetchall()
    if [tuple(row) for row in stored] != [
            (e["event_id"], e["kind"], _actor_json(e["actor"]), e.get("authority_epoch"), _payload_json(e["payload"]))
            for e in replayed]:
        raise ReplicaError("page conflicts with the stored replica history")


def _stored_span(conn: sqlite3.Connection, room_id: str, row: sqlite3.Row | None) -> tuple[int, str | None]:
    """(epoch, gateway or None) of the authority span the copy's last stored event belongs to."""
    last = conn.execute("SELECT authority_epoch FROM hosted_room_replica_events WHERE room_id=? AND seq=?",
                        (room_id, int(row["last_seq"]) if row is not None else 0)).fetchone()
    epoch = 1 if last is None or last["authority_epoch"] is None else int(last["authority_epoch"])
    if row is None:
        return epoch, None
    return epoch, row["lineage_gateway_id"] or (
        str(row["authority_gateway_id"]) if int(row["authority_epoch"]) == epoch else None)


def _claim_successor(event: dict[str, Any], epoch: int, gateway: str | None) -> str:
    """The gateway an ``authority.claimed`` event hands the room to, if it extends span (gateway, epoch)."""
    claim = event["payload"] if isinstance(event["payload"], dict) else {}
    successor, previous = claim.get("authority_gateway_id"), claim.get("previous_gateway_id")
    if (event.get("authority_epoch") != epoch + 1 or claim.get("authority_epoch") != epoch + 1
            or event["actor"] != _SYSTEM_ACTOR or not isinstance(successor, str) or not isinstance(previous, str)
            or gateway not in (None, previous)):
        raise ReplicaError(f"authority.claimed at seq {event['seq']} does not extend the stored replica authority")
    return successor


def _holds_disband(conn: sqlite3.Connection, room_id: str) -> bool:
    """Whether the stored copy holds ``room.disbanded`` anywhere: a copy stored before ingest refused events after
    it may hold some."""
    return conn.execute("SELECT 1 FROM hosted_room_replica_events WHERE room_id=? AND kind='room.disbanded' LIMIT 1",
                        (room_id,)).fetchone() is not None


def _check_lineage(
    conn: sqlite3.Connection, room_id: str, row: sqlite3.Row | None, new_events: list[dict[str, Any]],
    authority: dict[str, Any], *, caught_up: bool) -> str | None:
    """Accept an epoch or gateway change only through an ``authority.claimed`` boundary extending the copy.

    The work-evidence rule (``hosted_room_work_lineage``): a claim exactly at each epoch boundary, naming the
    span it leaves, and gateway actors matching their span. A page stamped ahead of its events is a partial
    read whose claim is still to come, so the stamp must match the events once the page is caught up. Returns
    the gateway of the span the events reach, when known."""
    epoch, gateway = _stored_span(conn, room_id, row)
    if gateway is None:  # nothing stored names it: the page's first claim does, or a stamp at this epoch
        claims = [event["payload"] for event in new_events if event["kind"] == "authority.claimed"]
        if claims:
            gateway = claims[0].get("previous_gateway_id") if isinstance(claims[0], dict) else None
        elif authority["epoch"] == epoch:
            gateway = authority["gateway_id"]
    for event in new_events:
        if event["kind"] == "authority.claimed":
            epoch, gateway = epoch + 1, _claim_successor(event, epoch, gateway)
            continue
        actor, event_epoch = event["actor"], event.get("authority_epoch")
        if (1 if event_epoch is None else event_epoch) != epoch or (
                actor.get("kind") == "gateway" and gateway not in (None, actor.get("id"))):
            raise ReplicaError(f"replica authority changed without an authority.claimed event at seq {event['seq']}")
    if authority["epoch"] < epoch or (authority["epoch"] > epoch and caught_up) or (
            authority["epoch"] == epoch and gateway not in (None, authority["gateway_id"])):
        raise ReplicaError("page authority does not match its authority.claimed lineage")
    return gateway if gateway is not None or authority["epoch"] != epoch else authority["gateway_id"]


def _store_replica(
    conn: sqlite3.Connection, *, is_new: bool, room_id: str, room_name: str, members_json: str,
    authority: dict[str, Any], lineage_gateway_id: str | None, new_last: int, latest_seq: int, added_bytes: int,
    now: float) -> None:
    """INSERT the replica row for a new room, else UPDATE it (event_bytes accumulates)."""
    values = (
        room_name, members_json, authority["gateway_id"], authority["epoch"], lineage_gateway_id, new_last,
        max(latest_seq, new_last))
    if is_new:
        conn.execute("""INSERT INTO hosted_room_replicas (room_id, name, members_json,
                authority_gateway_id, authority_epoch, lineage_gateway_id, last_seq, latest_seq, event_bytes,
                created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_id, *values, added_bytes, now, now))
    else:
        conn.execute("""UPDATE hosted_room_replicas SET name=?, members_json=?, authority_gateway_id=?,
                authority_epoch=?, lineage_gateway_id=?, last_seq=?, latest_seq=?, event_bytes=event_bytes+?,
                updated_at=? WHERE room_id=?""", (*values, added_bytes, now, room_id))


def ingest_page(
    db_path: DbPath, *, room_id: Any, room_name: Any, members: Any, page: Any, now: float | None = None
) -> dict[str, Any]:
    """Persist one verbatim ``read_events()`` page idempotently; refuses seq gaps, epoch regressions, a replay
    that differs from the stored copy, an authority change without an ``authority.claimed`` boundary and events
    after ``room.disbanded``."""
    room_id = _room_id(room_id)
    room_name = _validate_room_name(room_name)
    _, members_json = _validate_members(members)
    events, authority = _validate_page(page)
    now = clock(now)
    with _replica_transaction(db_path) as conn:
        row, stored_epoch, last_seq, stored_bytes = _replica_row_state(conn, room_id)
        if authority["epoch"] < stored_epoch:
            raise ReplicaEpochRegressionError("page authority epoch is older than the stored replica epoch")
        _check_replay(conn, room_id, [e for e in events if int(e["seq"]) <= last_seq])
        new_events = [e for e in events if int(e["seq"]) > last_seq]
        if new_events and int(new_events[0]["seq"]) != last_seq + 1:
            raise ReplicaGapError("page skips sequences the replica has not stored")
        new_last = int(new_events[-1]["seq"]) if new_events else last_seq
        latest_seq = page.get("latest_seq")
        if isinstance(latest_seq, bool) or not isinstance(latest_seq, int):
            latest_seq = new_last
        # A copy ends at room.disbanded, exactly caught up, and its row is then final.
        stored_terminal = _holds_disband(conn, room_id)
        terminal = new_events[-1]["kind"] == "room.disbanded" if new_events else stored_terminal
        if (stored_terminal and new_events) or (terminal and latest_seq != new_last) or any(
                event["kind"] == "room.disbanded" for event in new_events[:-1]):
            raise ReplicaError("a disbanded Group Chat accepts no later events")
        lineage_gateway_id = _check_lineage(
            conn, room_id, row, new_events, authority, caught_up=new_last >= latest_seq)
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
        if not stored_terminal:
            _store_replica(
                conn, is_new=row is None, room_id=room_id, room_name=room_name, members_json=members_json,
                authority=authority, lineage_gateway_id=lineage_gateway_id, new_last=new_last, latest_seq=latest_seq,
                added_bytes=added_bytes, now=now)
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


def promote_replica(
    db_path: DbPath, *, room_id: Any, reason: Any = "authority-unreachable", now: float | None = None
) -> dict[str, Any]:
    """Continue a replicated room on THIS gateway at ``epoch + 1``.

    Copies the replica log into the authoritative store and appends a lineage-proving ``authority.claimed``
    event, so wherever the claim replicates the old epoch is stale and every fenced primitive rejects it.
    The caller decides takeover is safe; this makes it atomic and provable. A copy that recorded a later
    authority than its stored events reach (that authority's claim not yet replicated) is refused, so the
    appended claim always extends the stored lineage and never reuses an epoch the copy has seen.
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
        if int(replica["authority_epoch"]) != _stored_span(conn, room_id, replica)[0]:
            raise ReplicaError(
                f"replica has not stored the authority.claimed for epoch {int(replica['authority_epoch'])}; "
                "replicate the current host's log through that claim before promoting")
        if replica["authority_gateway_id"] == local_gateway:
            raise ReplicaError("this gateway already holds the room authority")
        if conn.execute("SELECT 1 FROM hosted_rooms WHERE room_id=?", (room_id,)).fetchone():
            raise RoomConflictError("room_id already exists in the local authoritative store")
        if conn.execute("SELECT 1 FROM hosted_room_retired_ids WHERE room_id=?", (room_id,)).fetchone():
            raise RoomConflictError("room_id belongs to a disbanded room")
        previous_gateway, previous_epoch = str(replica["authority_gateway_id"]), int(replica["authority_epoch"])
        target_epoch, claim_seq = previous_epoch + 1, int(replica["last_seq"]) + 1
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
        conn.execute(
            f"""INSERT INTO hosted_room_events {_EVENT_COLUMNS}
               SELECT room_id, seq, event_id, kind, actor_json, authority_epoch, payload_json, created_at
                 FROM hosted_room_replica_events WHERE room_id=?""", (room_id,))
        _append_control_event(conn, room_id, claim_seq, target_epoch, claim, now)
        conn.execute("DELETE FROM hosted_room_replica_events WHERE room_id=?", (room_id,))
        conn.execute("DELETE FROM hosted_room_replicas WHERE room_id=?", (room_id,))
    return {
        "room_id": room_id, "authority_gateway_id": local_gateway, "authority_epoch": target_epoch,
        "previous_gateway_id": previous_gateway, "previous_epoch": previous_epoch, "claim_seq": claim_seq,
        "latest_seq": claim_seq}


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
