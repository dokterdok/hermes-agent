"""Existing hosted-room viewer authority, without startup or migration writes.

Only viewer consumers use this path. General room state and mutation startup
retain their mandatory schema rules. Missing core authority is not legacy data
we can infer; optional safety tables may be absent, but present fences deny.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from typing import Any

from gateway.hosted_rooms import (
    AuthorityConflictError, HostedRoomError, RoomNotFoundError,
    RoomProbeUnavailableError, _actor_id, _room_id,
)


@contextmanager
def viewer_snapshot(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Read existing committed state; never wait for a writer lease or bootstrap.

    Do not use immutable=1: it ignores live WAL and would hide revocation races.
    Each snapshot ends before byte I/O and is reopened afterward by the consumer.
    """
    conn = None
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 2.0
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        conn.execute("BEGIN")
        yield conn
    except sqlite3.Error as exc:
        raise RoomProbeUnavailableError("Group Chat viewer state is unavailable") from exc
    finally:
        if conn is not None:
            conn.close()


def viewer_room_state(conn: sqlite3.Connection, *, room_id: Any) -> dict[str, Any]:
    """Read exactly one live room's core authority within the caller's snapshot."""
    room_id = _room_id(room_id)
    schema = conn.execute("SELECT type FROM sqlite_master WHERE name='hosted_rooms'").fetchall()
    if len(schema) != 1 or schema[0][0] != "table":
        raise RoomNotFoundError("Group Chat viewer room schema is unavailable")
    rows = conn.execute(
        """SELECT room_id, authority_gateway_id, authority_epoch, disbanded_at
           FROM hosted_rooms WHERE room_id=? LIMIT 2""", (room_id,),
    ).fetchall()
    if len(rows) != 1:
        raise RoomNotFoundError("Group Chat viewer room is missing or ambiguous")
    room = dict(rows[0])
    if room["room_id"] != room_id or room["disbanded_at"] is not None:
        raise RoomNotFoundError("Group Chat is unavailable to viewers")
    owner = room["authority_gateway_id"]
    if _actor_id(owner, "authority_gateway_id") != owner:
        raise AuthorityConflictError("Group Chat viewer authority is invalid")
    if type(room["authority_epoch"]) is not int or room["authority_epoch"] < 1:
        raise AuthorityConflictError("Group Chat viewer epoch is invalid")
    for table in ("hosted_room_quarantine", "hosted_room_disband_fences"):
        # Unexpected schema (including a view in place of a fence table) is not absence.
        schema = conn.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchall()
        if schema:
            if len(schema) != 1 or schema[0][0] != "table":
                raise RoomNotFoundError("Group Chat viewer fence schema is unavailable")
            if conn.execute(f"SELECT 1 FROM {table} WHERE room_id=? LIMIT 1", (room_id,)).fetchone():
                raise RoomNotFoundError("Group Chat is unavailable to viewers")
    return room


def owned_viewer_room(db_path: Path | str, *, room_id: Any) -> dict[str, Any]:
    """Authorize the existing local installation, without minting or cache fallback."""
    from hermes_cli.install_identity import _INSTALL_ID_FILENAME, _read_existing
    from hermes_constants import get_default_hermes_root

    room_id = _room_id(room_id)
    install_id, _mint = _read_existing(get_default_hermes_root() / _INSTALL_ID_FILENAME)
    if not install_id:
        raise HostedRoomError("stable gateway install identity is unavailable")
    with viewer_snapshot(db_path) as conn:
        room = viewer_room_state(conn, room_id=room_id)
    if room["authority_gateway_id"] != f"install:{install_id}":
        raise AuthorityConflictError("This Group Chat is managed by another gateway.")
    return room
