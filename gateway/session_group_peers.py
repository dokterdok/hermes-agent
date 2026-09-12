"""Profile-owned RoomLink setup on the canonical control plane.

Ports the target-issued invitation and exact route registration contracts from
#100016 without using the legacy server registry or granting another profile.
"""
from pathlib import Path
import secrets

from gateway.session_authorities import served_profile_name
from hermes_state_runtime import RuntimeStoreError


GROUP_PEER_METHODS = {name: 'session:control' for name in (
    'groups.peer.invite', 'groups.peer.revoke', 'groups.peer.revoke_exact', 'groups.peer.register')}
GROUP_PEER_FIELDS = {
    'groups.peer.invite': {'room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch',
                         'member_id', 'grant_id', 'ttl_seconds', 'status_ttl_seconds',
                         'replication', 'work_records', 'passive_only'},
    'groups.peer.revoke': {'grant'},
    'groups.peer.revoke_exact': {'grant'},
    'groups.peer.register': {'room_id', 'member_id', 'target_url', 'target_profile', 'catalog',
                            'grant', 'expected_grant_sha256', 'cancellation_scope_id', 'trace_id'},
}


def _api_adapter(authority):
    from gateway.config import Platform
    from gateway.session_authorities import authority_for_profile_id
    runner = getattr(authority, 'runner', None)
    if runner is None or authority_for_profile_id(runner, authority.profile_id) is not authority:
        raise RuntimeStoreError('room_link_api_unavailable')
    # The API listener serves all reserved authorities; messaging adapter maps do not.
    adapter = getattr(runner, 'adapters', {}).get(Platform.API_SERVER)
    if (adapter is None or getattr(adapter, 'gateway_runner', None) is not runner
            or adapter._ensure_session_db() is not authority.db):
        raise RuntimeStoreError('room_link_api_unavailable')
    if not getattr(getattr(adapter, '_run_idempotency_store', None), 'durable', False):
        raise RuntimeStoreError('durable_run_storage_required')
    return adapter


def peer_capabilities(authority):
    from gateway.hosted_rooms import local_authority_gateway_id
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    try:
        adapter = _api_adapter(authority)
        profile = served_profile_name(Path(authority.profile_id))
        _, catalog = _local_room_catalog(adapter, profile, local_authority_gateway_id())
        # Setup can be prepared, but native peer controls are a separate port.
        return {'enabled': False, 'reason': 'canonical_peer_controls_required',
                'profile': profile, 'catalog': catalog, 'endpoint': catalog['endpoint']}
    except RuntimeStoreError as exc:
        return {'enabled': False, 'reason': exc.reason}
    except (ValueError, OSError):
        return {'enabled': False, 'reason': 'room_link_configuration_unavailable'}


def _invite(authority, actor, service, params):
    from gateway.hosted_room_grant_state import grant_state_db_paths, reserve_grant_state
    from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret, issue_room_grant, invitation_permissions
    from gateway.hosted_rooms import local_authority_gateway_id
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    adapter = _api_adapter(authority)
    profile = served_profile_name(Path(authority.profile_id))
    install = local_authority_gateway_id()
    policy, catalog = _local_room_catalog(adapter, profile, install)
    for key in ('room_id', 'home_install_id', 'authority_gateway_id', 'member_id'):
        if not isinstance(params.get(key), str) or not params[key]:
            raise RuntimeStoreError('invalid_params')
    if type(params.get('authority_epoch')) is not int or params['authority_epoch'] < 1:
        raise RuntimeStoreError('invalid_params')
    ttl = float(params.get('ttl_seconds', 3600))
    status_ttl = float(params.get('status_ttl_seconds', ttl))
    if not 60 <= ttl <= 86400 or not ttl <= status_ttl <= 30 * 86400:
        raise RuntimeStoreError('invalid_params')
    secret = gateway_room_grant_secret()
    token = issue_room_grant(secret,
        grant_id=params.get('grant_id') or 'grant-' + secrets.token_hex(16),
        room_id=params['room_id'], home_install_id=params['home_install_id'],
        authority_gateway_id=params['authority_gateway_id'], authority_epoch=params['authority_epoch'],
        member_id=params['member_id'], target_install_id=install, target_profile=profile,
        execution_policy_digest=policy['policy_digest'], ttl_seconds=ttl, status_ttl_seconds=status_ttl,
        permissions=invitation_permissions(params.get('replication', False), params.get('work_records', False),
                                           passive_only=params.get('passive_only', False)))
    claims = decode_room_grant(secret, token, permission='status')
    reserve_grant_state(grant_state_db_paths(authority.profile_id), claims=claims,
                        expires_at=claims['status_expires_at'])
    return {'grant': token, 'target_profile': profile, 'catalog': catalog, 'endpoint': catalog['endpoint'],
            'expires_at': claims['expires_at'], 'status_expires_at': claims['status_expires_at']}


def _revoke(authority, actor, service, params, *, exact=False):
    from gateway.hosted_room_grant_state import grant_state_db_paths, revoke_grant_state
    from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret
    from gateway.hosted_rooms import local_authority_gateway_id
    claims = decode_room_grant(gateway_room_grant_secret(), params.get('grant'),
                               permission='status', allow_expired_for_revocation=True)
    if (claims['target_profile'] != served_profile_name(Path(authority.profile_id))
            or claims['target_install_id'] != local_authority_gateway_id()):
        raise RuntimeStoreError('profile_mismatch')
    revoke_grant_state(grant_state_db_paths(authority.profile_id), claims=claims,
                       expires_at=claims['status_expires_at'], exact=exact)
    return {'revoked': True}


def _require_peer_target(room, member_id, target):
    member = next((m for m in room['members'] if m['member_id'] == member_id), None)
    stored = member.get('target', {}) if member is not None else {}
    if (stored.get('kind') != 'peer'
            or (stored.get('installation_id'), stored.get('profile')) != target):
        raise RuntimeStoreError('room_link_scope_changed')


def _register(authority, actor, service, params):
    from gateway.hosted_room_peer import GatewayRoomCatalog, PROTOCOL_VERSION, validate_room_link_url
    from gateway.hosted_rooms import local_authority_gateway_id
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    from tui_gateway.hosted_room_peer_transport import PeerMemberRoute
    if service is None:
        raise RuntimeStoreError('runtime_coordination_required')
    room_id, member = params.get('room_id'), params.get('member_id')
    service.authorize_room(actor.subject, room_id)
    service._owned_authority(room_id)
    url, security = validate_room_link_url(params.get('target_url'))
    catalog = GatewayRoomCatalog.from_mapping(params.get('catalog'))
    if PROTOCOL_VERSION not in catalog.protocol_versions or 'direct' not in catalog.link_modes:
        raise RuntimeStoreError('room_link_protocol_unavailable')
    profile, grant = params.get('target_profile'), params.get('grant')
    if not all(isinstance(value, str) and value for value in (member, profile, grant)):
        raise RuntimeStoreError('invalid_params')
    fingerprint = params.get('expected_grant_sha256')
    if fingerprint is not None and (not isinstance(fingerprint, str) or fingerprint and (
            len(fingerprint) != 64 or any(char not in '0123456789abcdef' for char in fingerprint))):
        raise RuntimeStoreError('invalid_params')
    target = (catalog.installation_id, profile)
    _require_peer_target(service._room(room_id), member, target)
    client = PeerRunsHTTPClient(base_url=url, api_key='', target_profile=profile, receipt_db_path=service.db_path)
    probe = client.probe(grant=grant)
    if GatewayRoomCatalog.from_mapping(probe.get('catalog')) != catalog:
        raise RuntimeStoreError('room_capability_catalog_changed')
    service.authorize_room(actor.subject, room_id)
    service._owned_authority(room_id)
    room = service._room(room_id)
    _require_peer_target(room, member, target)
    install = local_authority_gateway_id()
    expected = {'room_id': room_id, 'home_install_id': install, 'member_id': member,
                'authority_gateway_id': room['authority_gateway_id'], 'authority_epoch': room['authority_epoch'],
                'target_profile': profile}
    if type(probe.get('authority_epoch')) is not int or any(probe.get(key) != value for key, value in expected.items()):
        raise RuntimeStoreError('room_link_scope_changed')
    route = PeerMemberRoute(home_install_id=install, member_id=member,
        target_install_id=catalog.installation_id, target_profile=profile,
        capability_digest=catalog.catalog_digest, execution_policy_digest=catalog.execution_policy.policy_digest,
        cancellation_scope_id=params.get('cancellation_scope_id') or f'cancel-{room_id}',
        trace_id=params.get('trace_id') or 'trace-' + secrets.token_hex(16), grant=grant)
    service.register_peer_route(room_id=room_id, member_id=member, route=route, client=client,
        target_url=url, catalog=catalog,
        **({'expected_grant_sha256': fingerprint} if fingerprint is not None else {}))
    return {'registered': True, 'mode': 'direct', 'transport_security': security,
            'target_install_id': catalog.installation_id, 'target_profile': profile}


def dispatch_group_peer(authority, actor, service, method, params):
    from functools import partial
    if method not in GROUP_PEER_METHODS or not isinstance(params, dict) or set(params) - GROUP_PEER_FIELDS[method]:
        raise RuntimeStoreError('invalid_params')
    if actor.profile_id != authority.profile_id or 'session:control' not in actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    handlers = {'groups.peer.invite': _invite, 'groups.peer.revoke': _revoke,
                'groups.peer.revoke_exact': partial(_revoke, exact=True), 'groups.peer.register': _register}
    return handlers[method](authority, actor, service, params)
