"""Native RoomLink setup reaches scoped stores without a legacy server."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import hosted_rooms
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.hosted_room_grant_state import grant_state_db_paths
from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping, decode_room_grant, gateway_room_grant_secret
from gateway.platforms.api_server import APIServerAdapter
from gateway.run import _profile_runtime_scope
from gateway.session_contract import Principal
from gateway.session_group_controls import dispatch_group_control
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch


@pytest.fixture(params=['default', 'reviewer'])
def owner(tmp_path, monkeypatch, request):
    profile = request.param
    home = tmp_path / 'home'
    if profile != 'default':
        home = home / 'profiles' / profile
    home.mkdir(parents=True)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    with SessionDB(home / 'state.db') as db:
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'test-only-key'}))
        runner = SimpleNamespace(config=GatewayConfig(multiplex_profiles=False), _draining=False)
        authority = SimpleNamespace(runner=runner, db=db, profile_id=str(home),
            epoch=begin_runtime_epoch(db, instance_id='test'))
        runner.session_authority = authority
        runner._adapters_for_profile = lambda selected: {Platform.API_SERVER: adapter}
        adapter.gateway_runner = runner
        adapter._profile_scope = lambda selected: _profile_runtime_scope(home)
        service = CanonicalHostedRoomService(authority, None)
        authority.hosted_room_service = service
        actor = Principal('owner', str(home), frozenset({'session:read', 'session:control'}), 'test')
        try:
            yield SimpleNamespace(authority=authority, actor=actor), service, profile
        finally:
            adapter._run_idempotency_store.close()


@pytest.mark.asyncio
async def test_native_invite_and_exact_revoke_preserve_profile_and_signed_horizon(owner):
    connection, service, profile = owner
    params = dict(room_id='remote-room', home_install_id='remote-home', authority_gateway_id='remote-home',
                  authority_epoch=1, member_id='member', ttl_seconds=3600, status_ttl_seconds=7200)
    capabilities = await dispatch_group_control(connection, 'groups.capabilities', {})
    assert capabilities['room_link']['profile'] == profile
    assert not capabilities['room_link']['enabled']
    first = await dispatch_group_control(connection, 'groups.peer.invite', {**params, 'grant_id': 'first'})
    second = await dispatch_group_control(connection, 'groups.peer.invite', {**params, 'grant_id': 'second'})
    secret = gateway_room_grant_secret()
    first_claims = decode_room_grant(secret, first['grant'], permission='status')
    second_claims = decode_room_grant(secret, second['grant'], permission='status')
    assert first['target_profile'] == profile == first_claims['target_profile']
    assert first_claims['status_expires_at'] - first_claims['issued_at'] == 7200
    await dispatch_group_control(connection, 'groups.peer.revoke_exact', {'grant': first['grant']})
    for db in grant_state_db_paths(connection.authority.profile_id):
        assert hosted_rooms.room_grant_is_revoked(db, claims=first_claims)
        assert not hosted_rooms.room_grant_is_revoked(db, claims=second_claims)
    for bad in ({**params, 'profile': 'foreign'}, {**params, 'authority_epoch': True},
                {**params, 'ttl_seconds': float('nan')}, {**params, 'target_profile': 'foreign'}):
        with pytest.raises(RuntimeStoreError):
            await dispatch_group_control(connection, 'groups.peer.invite', bad)
    reader = replace(connection.actor, capabilities=frozenset({'session:read'}))
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await dispatch_group_control(SimpleNamespace(authority=connection.authority, actor=reader),
                                     'groups.peer.invite', params)


@pytest.mark.asyncio
async def test_native_registration_keeps_room_owner_and_exact_target_route(owner, monkeypatch):
    from gateway.hosted_room_link_records import room_link_record
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    connection, service, profile = owner
    gateway = hosted_rooms.local_authority_gateway_id()
    catalog = catalog_mapping(installation_id='peer-install', target_profile='peer', persistent_process=True)
    service.authorize_room(connection.actor.subject, 'room', create=True)
    service.create_room(room_id='room', name='Room', members=[
        {'member_id': 'local', 'profile': profile, 'handle': 'local'},
        {'member_id': 'peer', 'profile': 'peer', 'handle': 'peer', 'target': {
            'kind': 'peer', 'peer_id': 'peer', 'installation_id': 'peer-install',
            'profile': 'peer', 'capability_digest': catalog['catalog_digest']}}])
    monkeypatch.setattr(service.runtime, 'status', lambda: {'running': True})
    probes = []
    response = dict(room_id='room', home_install_id=gateway, authority_gateway_id=gateway,
                    authority_epoch=1, member_id='peer', target_profile='peer', catalog=catalog)
    def probe(client, *, grant):
        probes.append(grant)
        return response
    monkeypatch.setattr(PeerRunsHTTPClient, 'probe', probe)
    params = dict(room_id='room', member_id='peer', target_url='http://127.0.0.1:9999',
                  target_profile='peer', catalog=catalog, grant='target-issued-grant')
    result = await dispatch_group_control(connection, 'groups.peer.register', params)
    assert result['registered'] and result['target_install_id'] == GatewayRoomCatalog.from_mapping(catalog).installation_id
    stored = room_link_record(service.db_path, room_id='room', member_id='peer')
    assert stored['grant'] == params['grant'] and stored['target_profile'] == 'peer'
    response['authority_epoch'] = 2
    with pytest.raises(RuntimeStoreError, match='room_link_scope_changed'):
        await dispatch_group_control(connection, 'groups.peer.register', params)
    assert room_link_record(service.db_path, room_id='room', member_id='peer') == stored
    before = len(probes)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await dispatch_group_control(SimpleNamespace(authority=connection.authority,
            actor=replace(connection.actor, subject='another')), 'groups.peer.register', params)
    assert len(probes) == before
