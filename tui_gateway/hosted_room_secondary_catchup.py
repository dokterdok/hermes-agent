"""Durable keys for secondary catch-up after a no-op notify.

The runtime remembers ``(task identity, execution_generation)`` when a settled
invitation notify returns before primary terminal events exist. This table is
that set. It is not a scan of settled tasks, not a publication, and not
send-consent. ``publish_terminal`` and ``prepare_room`` do not read or write it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from gateway.hosted_room_driver import DriverStateError, DriverValidationError, TaskIdentity
from gateway.hosted_rooms_common import DbPath, connect, table_columns, table_exists, transaction

TABLE_NAME = "hosted_room_secondary_awaiting_primary"
_COLUMNS = frozenset({
    "room_id", "task_id", "thread_id", "turn_id", "execution_generation"})


def _generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DriverValidationError("execution_generation must be a non-negative integer")
    return value


def _identity(identity: TaskIdentity) -> TaskIdentity:
    if type(identity) is not TaskIdentity:
        raise DriverValidationError("secondary catch-up identity is invalid")
    return TaskIdentity(
        identity.room_id, identity.task_id, identity.thread_id, identity.turn_id)


def _ready(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, TABLE_NAME):
        return False
    if table_columns(conn, TABLE_NAME) != _COLUMNS:
        raise DriverStateError(
            "unsupported secondary catch-up schema; recreate the catch-up table")
    return True


def _create(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            room_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            execution_generation INTEGER NOT NULL CHECK (execution_generation >= 0),
            PRIMARY KEY (room_id, task_id, execution_generation))""")


def _connect(db_path: DbPath) -> sqlite3.Connection:
    return connect(
        db_path, db_label="state.db (secondary catch-up)", ready=_ready, initialize=_create)


def _open_existing(db_path: DbPath) -> sqlite3.Connection | None:
    """Open a database that already exists. Does not create the key table or the file."""
    path = db_path if isinstance(db_path, Path) else Path(db_path)
    if not path.is_file():
        return None
    from hermes_cli.sqlite_util import open_db

    return open_db(
        path, db_label="state.db (secondary catch-up)", foreign_keys=True)


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not table_exists(conn, TABLE_NAME):
        return []
    if table_columns(conn, TABLE_NAME) != _COLUMNS:
        raise DriverStateError(
            "unsupported secondary catch-up schema; recreate the catch-up table")
    return list(conn.execute(
        f"""SELECT room_id, task_id, thread_id, turn_id, execution_generation
            FROM {TABLE_NAME}"""))


def remember_awaiting_primary(
        db_path: DbPath, identity: TaskIdentity, execution_generation: int) -> None:
    """Persist one no-op notify. Replacing the same key keeps the latest identity."""
    identity = _identity(identity)
    generation = _generation(execution_generation)
    with transaction(_connect, db_path, immediate=True) as conn:
        conn.execute(
            f"""INSERT INTO {TABLE_NAME} (
                    room_id, task_id, thread_id, turn_id, execution_generation)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_id, task_id, execution_generation) DO UPDATE SET
                    thread_id=excluded.thread_id, turn_id=excluded.turn_id""",
            (identity.room_id, identity.task_id, identity.thread_id, identity.turn_id, generation))


def forget_awaiting_primary(
        db_path: DbPath, identity: TaskIdentity, execution_generation: int) -> None:
    """Drop one key. A missing table or row is already forgotten."""
    identity = _identity(identity)
    generation = _generation(execution_generation)
    conn = _open_existing(db_path)
    if conn is None:
        return
    from hermes_cli.sqlite_util import write_txn

    try:
        if not table_exists(conn, TABLE_NAME):
            return
        if table_columns(conn, TABLE_NAME) != _COLUMNS:
            raise DriverStateError(
                "unsupported secondary catch-up schema; recreate the catch-up table")
        with write_txn(conn):
            conn.execute(
                f"""DELETE FROM {TABLE_NAME}
                    WHERE room_id=? AND task_id=? AND execution_generation=?""",
                (identity.room_id, identity.task_id, generation))
    finally:
        conn.close()


def load_awaiting_primary(db_path: DbPath) -> set[tuple[TaskIdentity, int]]:
    """Return every durable key. Does not read settled tasks or create the table."""
    conn = _open_existing(db_path)
    if conn is None:
        return set()
    with closing(conn):
        rows = _rows(conn)
    loaded: set[tuple[TaskIdentity, int]] = set()
    for row in rows:
        generation = row["execution_generation"]
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise DriverValidationError("stored secondary catch-up generation is invalid")
        loaded.add((
            TaskIdentity(row["room_id"], row["task_id"], row["thread_id"], row["turn_id"]),
            generation))
    return loaded
