"""Exact canonical Run output metadata; no producer or grant authority is minted.

Scope projection preserves #99159's Run contract on the canonical admission
ledger. Active API producer exposure remains gated on retained publication consent.
"""
from pathlib import Path

from gateway.hosted_room_artifacts import RoomArtifactScope, validate_terminal_artifact_manifest
from gateway.hosted_room_peer import HostedMemberDispatch
from hermes_state_runtime import RuntimeStoreError


def root_output_authority(authority):
    home = Path(str(authority.profile_id))
    return (home.is_absolute() and home == home.resolve() and home.parent.name != 'profiles'
            and Path(authority.db.db_path).resolve().parent == home)


def canonical_peer_artifact_fields(authority, row, result):
    """Only the exact completed admission can supply terminal Files evidence."""
    if row['status'] != 'terminal' or row['outcome'] != 'completed' or not result.get('artifacts'):
        return {}
    if result.get('failed') or result.get('error') or result.get('interrupted'):
        return {}
    settings = row['payload'].get('api_turn_v1', {}).get('settings', {})
    retained = settings.get('room_dispatch')
    if retained is None or settings.get('room_artifact_publication') is not True:
        return {}
    try:
        if not root_output_authority(authority) or row['principal_id'] != 'api':
            raise ValueError('unsupported output owner')
        dispatch = HostedMemberDispatch.from_mapping(retained)
        from gateway import hosted_rooms
        if (dispatch.target_profile != 'default'
                or dispatch.target_install_id != hosted_rooms.local_authority_gateway_id()
                or dispatch.home_install_id == dispatch.target_install_id):
            raise ValueError('unsupported peer output route')
        fields = ('room_id', 'task_id', 'execution_generation', 'member_id', 'target_profile',
                  'home_install_id', 'target_install_id', 'authority_gateway_id', 'authority_epoch')
        scope = RoomArtifactScope.from_mapping({k: getattr(dispatch, k) for k in fields})
        if result.get('artifact_scope') != scope.as_mapping():
            raise ValueError('output scope does not match admitted Run')
        validate_terminal_artifact_manifest(result['artifacts'])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeStoreError('storage_unavailable') from exc
    return {'room_artifact_scope': scope.as_mapping(), 'artifacts': result['artifacts']}
