"""Native setup preserves roster identity and the shared API's exact authority scope."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.gateway.test_canonical_group_peer_setup import owner  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['before', 'during'])
@pytest.mark.parametrize('change', ['installation', 'profile', 'local', 'missing'])
async def test_registration_requires_same_peer_target_on_both_sides_of_probe(owner, monkeypatch, phase, change):
    from gateway import hosted_rooms
    from gateway.hosted_room_link_records import room_link_record
    from gateway.hosted_room_peer import catalog_mapping
    from gateway.session_group_controls import dispatch_group_control
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient

    connection, service, profile = owner
    gateway = hosted_rooms.local_authority_gateway_id()
    catalog = catalog_mapping(installation_id='peer-install', target_profile='peer', persistent_process=True)
    members = [
        {'member_id': 'local', 'profile': profile, 'handle': 'local'},
        {'member_id': 'peer', 'profile': 'peer', 'handle': 'peer', 'target': {
            'kind': 'peer', 'peer_id': 'peer', 'installation_id': 'peer-install',
            'profile': 'peer', 'capability_digest': catalog['catalog_digest']}}]
    service.authorize_room(connection.actor.subject, 'room', create=True)
    service.create_room(room_id='room', name='Room', members=members)
    monkeypatch.setattr(service.runtime, 'status', lambda: {'running': True})

    def change_snapshot():
        changed = json.loads(json.dumps(service._room('room')['members']))
        if change == 'missing':
            changed = changed[:1]
        elif change == 'local':
            changed[1]['target'] = {'kind': 'local', 'profile': 'peer'}
        else:
            key = 'installation_id' if change == 'installation' else 'profile'
            changed[1]['target'][key] = 'another-peer'
        # Model a different durable roster at the probe boundary, without remote work.
        with sqlite3.connect(service.db_path) as db:
            db.execute('UPDATE hosted_rooms SET members_json=? WHERE room_id=?',
                       (json.dumps(changed), 'room'))

    probes = []

    def probe(client, *, grant):
        probes.append(grant)
        if phase == 'during':
            change_snapshot()
        return dict(room_id='room', home_install_id=gateway, authority_gateway_id=gateway,
                    authority_epoch=1, member_id='peer', target_profile='peer', catalog=catalog)

    monkeypatch.setattr(PeerRunsHTTPClient, 'probe', probe)
    if phase == 'before':
        change_snapshot()
    with pytest.raises(RuntimeStoreError, match='room_link_scope_changed'):
        await dispatch_group_control(connection, 'groups.peer.register', dict(
            room_id='room', member_id='peer', target_url='http://127.0.0.1:9999',
            target_profile='peer', catalog=catalog, grant='target-issued-grant'))
    assert len(probes) == (1 if phase == 'during' else 0)
    assert room_link_record(service.db_path, room_id='room', member_id='peer') is None


@pytest.mark.asyncio
async def test_shared_api_invites_only_for_exact_registered_scoped_authority(tmp_path, monkeypatch):
    from gateway.config import Platform, PlatformConfig
    from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.run_runtime import initialize_gateway_runtime
    from gateway.runtime_ownership import process_ownership
    from gateway.session_authorities import owner_scope
    from gateway.session_contract import Principal
    from gateway.session_group_controls import dispatch_group_control
    from gateway.session_group_peers import _api_adapter
    from hermes_state_runtime import RuntimeStoreError
    from tests.gateway.test_session_authorities_multiplex import _reserve_homes, _runner

    root, homes = _reserve_homes(tmp_path, monkeypatch, names=('alpha',))
    process_ownership.reserve([home for _, home in homes])
    runner, adapter = _runner(root, homes), None
    try:
        await initialize_gateway_runtime(runner)
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'test-only-key'}))
        adapter.gateway_runner = runner
        runner.adapters[Platform.API_SERVER] = adapter
        runner._profile_adapters['alpha'] = {}
        alpha = runner.session_authorities.require(homes[1][1])
        assert alpha.profile_id in runner.session_ticket_store.profile_ids
        assert Platform.API_SERVER not in runner._adapters_for_profile('alpha')
        connection = SimpleNamespace(authority=alpha, actor=Principal(
            'owner', alpha.profile_id, frozenset({'session:read', 'session:control'}), 'test'))
        capabilities = await dispatch_group_control(connection, 'groups.capabilities', {})
        assert capabilities['room_link']['profile'] == 'alpha'
        assert capabilities['room_link']['enabled'] is False
        invitation = await dispatch_group_control(connection, 'groups.peer.invite', dict(
            room_id='remote-room', home_install_id='remote-home', authority_gateway_id='remote-home',
            authority_epoch=1, member_id='member', ttl_seconds=3600, status_ttl_seconds=7200))
        with owner_scope(alpha):
            assert adapter._ensure_session_db() is alpha.db
            assert _api_adapter(alpha) is adapter
            claims = decode_room_grant(gateway_room_grant_secret(), invitation['grant'], permission='status')
            assert claims['target_profile'] == 'alpha'
            assert invitation['catalog']['execution_policy']['target_profile'] == 'alpha'
            impostor = SimpleNamespace(runner=runner, profile_id=alpha.profile_id, db=alpha.db)
            with pytest.raises(RuntimeStoreError, match='room_link_api_unavailable'):
                _api_adapter(impostor)
            with monkeypatch.context() as patch:
                patch.setattr(adapter, 'gateway_runner', object())
                with pytest.raises(RuntimeStoreError, match='room_link_api_unavailable'):
                    _api_adapter(alpha)
            with monkeypatch.context() as patch:
                patch.setattr(adapter, '_run_idempotency_store', SimpleNamespace(durable=False))
                with pytest.raises(RuntimeStoreError, match='durable_run_storage_required'):
                    _api_adapter(alpha)
        with owner_scope(runner.session_authorities.launch):
            assert adapter._ensure_session_db() is not alpha.db
            with pytest.raises(RuntimeStoreError, match='room_link_api_unavailable'):
                _api_adapter(alpha)
        await dispatch_group_control(connection, 'groups.peer.revoke_exact', {'grant': invitation['grant']})
    finally:
        if adapter is not None:
            adapter._run_idempotency_store.close()
        for authority in getattr(runner, 'session_authorities', []):
            authority.db.close()
        for _, home in homes:
            process_ownership.release(home)
