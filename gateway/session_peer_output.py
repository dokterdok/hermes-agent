"""Exact canonical Run output, authorized by its retained Files consent.

Scope projection preserves #99159's Run contract on the canonical admission
ledger. Root API producer exposure is gated on retained publication consent.
"""
from pathlib import Path
import json
import os

from gateway.hosted_room_artifacts import RoomArtifactScope, validate_terminal_artifact_manifest
from gateway.hosted_room_peer import HostedMemberDispatch
from hermes_state_runtime import RuntimeStoreError


def root_output_authority(authority):
    home = Path(str(authority.profile_id))
    return (home.is_absolute() and home == home.resolve() and home.parent.name != 'profiles'
            and Path(authority.db.db_path).resolve().parent == home)


def admitted_peer_scope(authority, row):
    settings = row['payload'].get('api_turn_v1', {}).get('settings', {})
    if (settings.get('room_artifact_publication') is not True
            or not root_output_authority(authority) or row['principal_id'] != 'api'):
        return None
    retained = settings.get('room_dispatch')
    if retained is None:
        return None
    dispatch = HostedMemberDispatch.from_mapping(retained)
    from gateway import hosted_rooms
    if (dispatch.target_profile != 'default'
            or dispatch.target_install_id != hosted_rooms.local_authority_gateway_id()
            or dispatch.home_install_id == dispatch.target_install_id):
        return None
    fields = ('room_id', 'task_id', 'execution_generation', 'member_id', 'target_profile',
              'home_install_id', 'target_install_id', 'authority_gateway_id', 'authority_epoch')
    return RoomArtifactScope.from_mapping({key: getattr(dispatch, key) for key in fields})


def peer_output_binding(authority, ref, row):
    from gateway.session_managed_worker import managed_policy
    scope = admitted_peer_scope(authority, row)
    if scope is None or managed_policy(authority, ref) is not None:
        return None
    from gateway.session_hosted_output import HostedOutputBinding
    from gateway.hosted_room_artifacts import RoomArtifactError
    from hermes_state_runtime import _epoch, _row

    class PeerOutputBinding(HostedOutputBinding):
        def check_write(self, conn, scope):
            if not self.active or os.getpid() != self.owner_pid or scope != self.scope:
                raise RoomArtifactError('Group Chat output producer is no longer active')
            _epoch(conn, self.authority.epoch)
            saved = conn.execute('SELECT * FROM session_admissions WHERE admission_id=?',
                                  (self.row['admission_id'],)).fetchone()
            if (saved is None or saved['status'] != 'started'
                    or saved['owner_epoch'] != self.authority.epoch
                    or saved['generation'] != self.row['generation']
                    or saved['principal_id'] != 'api' or saved['target_session_id'] != self.ref.session_id
                    or saved['request_id'] != self.row['request_id']
                    or json.loads(saved['payload_json']) != self.row['payload']
                    or admitted_peer_scope(self.authority, _row(saved)) != scope):
                raise RoomArtifactError('Group Chat output admission changed')

    binding = PeerOutputBinding(authority, ref, row, scope, 0, os.getpid())
    with authority.db._read_ctx() as conn:
        binding.check_write(conn, scope)
    return binding


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
