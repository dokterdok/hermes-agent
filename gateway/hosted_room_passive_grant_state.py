"""Read the canonical grant ledgers inside the passive receiver's writer lock.

Uses the parent's exact signed-token revocation identity, including legacy deny
rows. This module neither issues grants nor opens a second database connection.
"""

from gateway import hosted_rooms as rooms


def grant_is_current_locked(conn, *, claims, now):
    return conn.execute(
        """SELECT 1 FROM hosted_room_peer_reservations WHERE room_id=? AND member_id=?
            AND target_profile=? AND authority_gateway_id=? AND authority_epoch=?
            AND expires_at>? AND revoked_at IS NULL LIMIT 1""",
        (*rooms._reservation_claims(claims), now),
    ).fetchone() is not None


def grant_is_revoked_locked(conn, *, claims, now):
    scope = rooms._room_grant_scope_key(claims)
    token = conn.execute(
        """SELECT 1 FROM hosted_room_revoked_grant_tokens
           WHERE scope_key=? AND token_sha256=? AND expires_at>?""",
        (scope, str(claims.get("_token_sha256") or ""), now),
    ).fetchone()
    if token is not None:
        return True
    legacy = conn.execute(
        """SELECT 1 FROM hosted_room_revoked_grant_ids
           WHERE scope_key=? AND grant_id=? AND expires_at>?""",
        (scope, rooms._room_grant_id(claims), now),
    ).fetchone()
    if legacy is not None:
        return True
    fence = conn.execute(
        "SELECT revoked_before FROM hosted_room_revoked_grants WHERE scope_key=? AND expires_at>?",
        (scope, now),
    ).fetchone()
    return fence is not None and float(claims.get("issued_at") or 0) <= float(fence["revoked_before"])
