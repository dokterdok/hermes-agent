"""Retirement-aware passive payload pruning; identities and quarantines survive."""

import sqlite3

from gateway.hosted_rooms_common import table_exists


def _prune_disbanded_replicas_locked(
    conn: sqlite3.Connection,
    *,
    now: float | None,
    max_replica_event_bytes: int | None = None,
    max_replica_rooms: int | None = None,
) -> int:
    """Reclaim terminal replica payload while its room-ID reservation remains."""
    from gateway import hosted_rooms as limits
    from gateway.hosted_room_replica_retirement import RETIREMENT_TABLE
    canonical = "(disbanded_at IS NOT NULL AND last_seq=latest_seq)"
    eligible = canonical
    retired_at = "disbanded_at"
    if table_exists(conn, RETIREMENT_TABLE):
        retirement_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({RETIREMENT_TABLE})")}
        replica_columns = {row["name"] for row in conn.execute("PRAGMA table_info(hosted_room_replicas)")}
        legacy_scope = [
            "retirement.authority_gateway_id=hosted_room_replicas.authority_gateway_id",
            "retirement.authority_epoch=hosted_room_replicas.authority_epoch",
        ]
        if "version" in retirement_columns:
            legacy_scope.append("retirement.version IS NULL")
        if "replica_version" in replica_columns:
            legacy_scope.append("hosted_room_replicas.replica_version IS NULL")
        scopes = ["(" + " AND ".join(legacy_scope) + ")"]
        if {"version", "lineage_sha256"} <= retirement_columns and {"replica_version", "lineage_sha256"} <= replica_columns:
            # A v2 retiring sender can be ahead of the verified prefix head.
            # Its frozen lineage, not that earlier head, binds the retired copy.
            scopes.append("""(retirement.version=2 AND hosted_room_replicas.replica_version=2
                AND retirement.lineage_sha256 IS NOT NULL
                AND retirement.lineage_sha256=hosted_room_replicas.lineage_sha256)""")
        match = f"""SELECT retired_at FROM {RETIREMENT_TABLE} AS retirement
            WHERE retirement.room_id=hosted_room_replicas.room_id
              AND ({' OR '.join(scopes)})"""
        eligible = f"({canonical} OR EXISTS ({match}))"
        retired_at = f"CASE WHEN {canonical} THEN disbanded_at ELSE ({match}) END"
    eligible += """ AND quarantine_reason IS NULL AND NOT EXISTS (
        SELECT 1 FROM hosted_room_quarantine
        WHERE hosted_room_quarantine.room_id=hosted_room_replicas.room_id)"""
    candidates: set[str] = set()
    if now is not None:
        cutoff = now - limits.DISBANDED_REPLICA_RETENTION_SECONDS
        candidates.update(
            str(row["room_id"])
            for row in conn.execute(
                f"SELECT room_id FROM hosted_room_replicas WHERE {eligible} AND ({retired_at})<=?",
                (cutoff,),
            ).fetchall()
        )
    if max_replica_event_bytes is not None:
        retained_bytes = int(
            conn.execute(
                "SELECT COALESCE(SUM(event_bytes), 0) FROM hosted_room_replicas"
            ).fetchone()[0]
        )
        if retained_bytes > max_replica_event_bytes:
            for row in conn.execute(
                f"SELECT room_id,event_bytes FROM hosted_room_replicas WHERE {eligible} ORDER BY ({retired_at}),room_id"
            ).fetchall():
                candidates.add(str(row["room_id"]))
                retained_bytes -= int(row["event_bytes"])
                if retained_bytes <= max_replica_event_bytes:
                    break
    if max_replica_rooms is not None:
        retained_rooms = int(
            conn.execute("SELECT COUNT(*) FROM hosted_room_replicas").fetchone()[0]
        )
        if retained_rooms > max_replica_rooms:
            for row in conn.execute(
                f"SELECT room_id FROM hosted_room_replicas WHERE {eligible} ORDER BY ({retired_at}),room_id"
            ).fetchall():
                candidates.add(str(row["room_id"]))
                retained_rooms -= 1
                if retained_rooms <= max_replica_rooms:
                    break
    if not candidates:
        return 0
    placeholders = ",".join("?" for _ in candidates)
    room_ids = tuple(sorted(candidates))
    conn.execute(
        f"DELETE FROM hosted_room_replica_events WHERE room_id IN ({placeholders})",
        room_ids,
    )
    deleted = conn.execute(
        f"""DELETE FROM hosted_room_replicas
             WHERE room_id IN ({placeholders}) AND {eligible}""",
        room_ids,
    )
    return max(0, int(deleted.rowcount))
