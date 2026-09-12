"""Files-specific capability checks, after the caller verifies the grant.

Status lifetime may outlive dispatch, but status alone never grants access to
bytes or cleanup. These pure checks do not decode signatures, resolve paths,
or extend authorization horizons; the transport owner must do that first.
"""

from collections.abc import Mapping

from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactScope


def require_transfer_cleanup(claims: Mapping, scope: RoomArtifactScope) -> None:
    """Retain the source's status-horizon plus Files-eligibility intersection."""
    permissions = claims.get("permissions") if isinstance(claims, Mapping) else None
    if (not isinstance(permissions, (list, tuple, set, frozenset))
            or not {"status", "attachment.stage"}.issubset(permissions)):
        raise RoomArtifactError("attachment cleanup requires Files eligibility")
    _require_participant(claims, scope)


def require_artifact_access(claims: Mapping, scope: RoomArtifactScope, *, permission: str) -> None:
    if permission not in {"artifact.read", "artifact.ack"}:
        raise RoomArtifactError("unsupported artifact permission")
    permissions = claims.get("permissions") if isinstance(claims, Mapping) else None
    if not isinstance(permissions, (list, tuple, set, frozenset)) or permission not in permissions:
        raise RoomArtifactError("artifact permission is missing")
    _require_participant(claims, scope)


def _require_participant(claims, scope):
    expected = RoomArtifactScope.from_mapping(scope.as_mapping()).as_mapping()
    # Task/generation are additionally bound by the authenticated run receipt;
    # invitations authorize the exact seven-field participant, not arbitrary runs.
    for key in expected.keys() - {"task_id", "execution_generation"}:
        if type(claims.get(key)) is not type(expected[key]) or claims[key] != expected[key]:
            raise RoomArtifactError("Files participant scope changed")
