"""Reciprocal status crosses real HTTP, profile middleware and canonical storage."""
from contextlib import ExitStack
from dataclasses import replace
import json
import time
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from gateway import hosted_rooms
from gateway.config import GatewayConfig, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.session_authorities import SessionAuthorities
from gateway.session_contract import Principal
from gateway.session_group_delegation import dispatch_owner_delegation
from gateway.session_hosted_service import CanonicalHostedRoomService, _OWNER
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    runner = SimpleNamespace(config=GatewayConfig(multiplex_profiles=True), _draining=False)
    from gateway import run
    monkeypatch.setattr(run, '_gateway_runner_ref', lambda: runner)
    runner.session_authorities = SessionAuthorities(tmp_path)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'unused-api-key'}))
    adapter.gateway_runner = runner
    rows = {}
    with ExitStack() as stack:
        for name, home in [('default', tmp_path), ('reviewer', tmp_path / 'profiles' / 'reviewer')]:
            home.mkdir(parents=True, exist_ok=True)
            (home / 'config.yaml').write_text('model: {}\n')
            db = stack.enter_context(SessionDB(home / 'state.db'))
            authority = SimpleNamespace(db=db, profile_id=str(home), runner=runner,
                epoch=begin_runtime_epoch(db, instance_id=name))
            runner.session_authorities.add(home, authority, name=None if name == 'default' else name)
            service = CanonicalHostedRoomService(authority, None)
            authority.hosted_room_service = service
            service.authorize_room('native-owner', 'room', create=True)
            hosted_rooms.create_room(db.db_path, room_id='room', name=f'{name} private room',
                authority_gateway_id=hosted_rooms.local_authority_gateway_id(),
                members=[{'member_id': 'peer', 'profile': 'peer', 'handle': 'peer'}])
            actor = Principal('native-owner', str(home), frozenset({'session:control'}), 'native')
            grant = dispatch_owner_delegation(authority, actor, 'issue',
                {'room_id': 'room', 'member_id': 'peer', 'request_id': 'setup'})
            rows[name] = authority, actor, grant['control_token']
        runner.session_authority = rows['default'][0]
        try:
            yield adapter, rows
        finally:
            adapter._run_idempotency_store.close()


def _app(adapter):
    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    for method, path, handler in adapter._http_route_table():
        if '/room-controls/' in path:
            app.router.add_route(method, path, handler)
            app.router.add_route(method, '/p/{profile}' + path, handler)
    return app


def _headers(token):
    return {'Authorization': 'HermesRoomControl ' + token, 'X-Hermes-Room-Member': 'peer'}


@pytest.mark.asyncio
async def test_scoped_status_and_exact_revocation_do_not_share_profile_identity(homes):
    adapter, rows = homes
    async with TestClient(TestServer(_app(adapter))) as client:
        for name, path in [('default', '/v1/room-controls/room'),
                           ('reviewer', '/p/reviewer/v1/room-controls/room')]:
            response = await client.get(path, headers=_headers(rows[name][2]))
            assert response.status == 200, await response.text()
            result = await response.json()
            assert result['room']['name'] == f'{name} private room'
            assert result['control_actions'] == ['send']
            assert response.headers['Cache-Control'] == 'no-store'
        assert (await client.get('/p/reviewer/v1/room-controls/room',
            headers=_headers(rows['default'][2]))).status == 401
        assert (await client.get('/v1/room-controls/room')).status == 401
        assert (await client.get('/p/unserved/v1/room-controls/room',
            headers=_headers(rows['default'][2]))).status == 404
        assert (await client.get('/v1/room-controls/room?profile=reviewer',
            headers=_headers(rows['default'][2]))).status == 400
        for expected in (1, 0):
            response = await client.delete('/v1/room-controls/room', headers=_headers(rows['default'][2]))
            assert response.status == 200, await response.text()
            assert await response.json() == {'revoked': expected}
        assert (await client.get('/v1/room-controls/room', headers=_headers(rows['default'][2]))).status == 401
        assert (await client.get('/p/reviewer/v1/room-controls/room',
            headers=_headers(rows['reviewer'][2]))).status == 200
        assert (await client.post('/p/reviewer/v1/room-controls/room', headers=_headers(rows['reviewer'][2]),
            json={'action': 'stop', 'command_id': 'unsupported'})).status == 400


@pytest.mark.asyncio
async def test_status_does_not_release_data_after_owner_change_during_read(homes, monkeypatch):
    from gateway.platforms import api_server_room_controls as http
    adapter, rows = homes
    authority = rows['default'][0]
    original = http.dispatch_delegated_group_control
    async def change_after_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs['method'] == 'groups.log':
            authority.db._execute_write(lambda conn: conn.execute(
                'UPDATE state_meta SET value=? WHERE key=?', ('different-owner', _OWNER + 'room')))
        return result
    monkeypatch.setattr(http, 'dispatch_delegated_group_control', change_after_read)
    async with TestClient(TestServer(_app(adapter))) as client:
        response = await client.get('/v1/room-controls/room', headers=_headers(rows['default'][2]))
        assert response.status == 401
        assert 'private room' not in await response.text()


@pytest.mark.asyncio
async def test_native_invite_uses_frozen_peer_and_explicit_profile_endpoint(homes, monkeypatch):
    from gateway.hosted_room_peer import catalog_mapping
    from gateway.session_group_controls import dispatch_group_control
    from hermes_state_runtime import RuntimeStoreError
    adapter, rows = homes
    monkeypatch.setenv('HERMES_ROOM_LINK_URL', 'https://host.example.test/hermes')
    catalog = catalog_mapping(installation_id='peer-install', target_profile='peer', persistent_process=True)
    for name, (authority, actor, _token) in rows.items():
        service = authority.hosted_room_service
        service.authorize_room(actor.subject, 'peer-room', create=True)
        hosted_rooms.create_room(authority.db.db_path, room_id='peer-room', name='Shared',
            authority_gateway_id=hosted_rooms.local_authority_gateway_id(), members=[
                {'member_id': 'member', 'profile': 'peer', 'handle': 'peer', 'target': {
                    'kind': 'peer', 'peer_id': 'peer', 'installation_id': 'peer-install',
                    'profile': 'peer', 'capability_digest': catalog['catalog_digest']}}])
        connection = SimpleNamespace(authority=authority, actor=actor)
        params = {'room_id': 'peer-room', 'member_id': 'member',
                  'caller_install_id': 'peer-install', 'request_id': 'first'}
        result = await dispatch_group_control(connection, 'groups.control.invite', params)
        assert result['home_url'] == 'https://host.example.test/hermes/p/' + name
        assert result['member_id'] == 'member'
        with pytest.raises(RuntimeStoreError, match='room_control_participant_mismatch'):
            await dispatch_group_control(connection, 'groups.control.invite',
                {**params, 'caller_install_id': 'other-install'})
        connection.actor = replace(actor, subject='messaging-admin')
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await dispatch_group_control(connection, 'groups.control.invite', params)


@pytest.mark.asyncio
async def test_native_registration_and_revoke_keep_exact_route_during_rotation(homes, monkeypatch):
    from gateway import hosted_room_controls as controls
    from gateway.hosted_room_control_client import RoomControlHTTPClient
    from gateway.hosted_room_grant_state import grant_state_db_paths
    from gateway.session_group_controls import dispatch_group_control
    from hermes_state_runtime import RuntimeStoreError
    _adapter, rows = homes
    authority, actor, token = rows['reviewer']
    connection = SimpleNamespace(authority=authority, actor=actor)
    params = {'room_id': 'remote', 'member_id': 'peer', 'home_url': 'https://host.example.test/p/default',
        'authority_gateway_id': 'remote-owner', 'authority_epoch': 1, 'room_name': 'Remote room',
        'member_count': 2, 'control_token': token, 'expires_at': time.time() + 3600}
    with pytest.raises(RuntimeStoreError, match='room_control_reservation_required'):
        await dispatch_group_control(connection, 'groups.control.register', params)
    from gateway.run import _profile_runtime_scope
    with _profile_runtime_scope(authority.profile_id):
        paths = grant_state_db_paths(authority.profile_id)
        for path in paths:
            hosted_rooms.reserve_peer_room(path, claims={**params, 'target_profile': 'reviewer'},
                                            expires_at=params['expires_at'])
    monkeypatch.setattr(RoomControlHTTPClient, 'summary', lambda self: {'room': {
        key: params[key] for key in ('room_id', 'authority_gateway_id', 'authority_epoch')}})
    first = await dispatch_group_control(connection, 'groups.control.register', params)
    assert first == {'registered': True, 'idempotent': False, 'room_id': 'remote', 'member_id': 'peer'}
    assert (await dispatch_group_control(connection, 'groups.control.register', params))['idempotent']
    def rotate_during_revoke(client):
        controls.save_peer_control_link(authority.db.db_path, **{**params, 'control_token': rows['default'][2]},
                                        allow_rotation=True)
    monkeypatch.setattr(RoomControlHTTPClient, 'revoke', rotate_during_revoke)
    result = await dispatch_group_control(connection, 'groups.control.revoke', {'room_id': 'remote', 'member_id': 'peer'})
    assert result == {'revoked': 0}
    remaining = controls.load_peer_control_links(authority.db.db_path).links
    assert len(remaining) == 1 and remaining[0].control_token == rows['default'][2]


@pytest.mark.asyncio
async def test_registration_rechecks_reservation_after_probe(homes, monkeypatch):
    from gateway import hosted_room_controls as controls
    from gateway.hosted_room_control_client import RoomControlHTTPClient
    from gateway.hosted_room_grant_state import grant_state_db_paths
    from gateway.session_group_controls import dispatch_group_control
    from hermes_state_runtime import RuntimeStoreError
    _adapter, rows = homes
    authority, actor, token = rows['default']
    params = {'room_id': 'remote', 'member_id': 'peer', 'home_url': 'https://host.example.test/p/default',
        'authority_gateway_id': 'remote-owner', 'authority_epoch': 1, 'room_name': 'Remote room',
        'member_count': 2, 'control_token': token, 'expires_at': time.time() + 3600}
    for path in grant_state_db_paths(authority.profile_id):
        hosted_rooms.reserve_peer_room(path, claims={**params, 'target_profile': 'default'},
                                        expires_at=params['expires_at'])
    def expire_reservation(client):
        authority.db._execute_write(lambda conn: conn.execute(
            'UPDATE hosted_room_peer_reservations SET revoked_at=? WHERE room_id=?', (time.time(), 'remote')))
        return {'room': {key: params[key] for key in ('room_id', 'authority_gateway_id', 'authority_epoch')}}
    monkeypatch.setattr(RoomControlHTTPClient, 'summary', expire_reservation)
    with pytest.raises(RuntimeStoreError, match='room_control_reservation_required'):
        await dispatch_group_control(SimpleNamespace(authority=authority, actor=actor),
                                     'groups.control.register', params)
    assert controls.load_peer_control_links(authority.db.db_path, include_inactive=True).links == ()


@pytest.mark.asyncio
async def test_http_send_accepts_one_command_and_keeps_receiving_profile(homes):
    from gateway.hosted_room_driver import list_tasks
    adapter, rows = homes
    authority = rows['default'][0]
    roster = [{'member_id': 'writer', 'profile': 'default', 'handle': 'writer'},
        {'member_id': 'peer', 'profile': 'remote', 'handle': 'peer', 'target': {
            'kind': 'peer', 'installation_id': 'peer-install', 'profile': 'remote',
            'peer_id': 'remote-peer', 'capability_digest': 'a' * 64}}]
    authority.db._execute_write(lambda conn: conn.execute(
        'UPDATE hosted_rooms SET members_json=? WHERE room_id=?', (json.dumps(roster), 'room')))
    body = {'action': 'send', 'command_id': 'message-one', 'text': '@writer Prepare the report',
            'actor_display_name': 'Owner via messaging'}
    async with TestClient(TestServer(_app(adapter))) as client:
        received = []
        for _ in range(2):
            response = await client.post('/v1/room-controls/room', headers=_headers(rows['default'][2]), json=body)
            assert response.status == 200, await response.text()
            received.append(await response.json())
        assert all(value['accepted'] for value in received)
        assert received[0]['event']['event_id'] == received[1]['event']['event_id']
        assert received[1]['event']['idempotent']
        assert received[0]['event']['actor']['id'] == 'peer:peer'
        assert len(list_tasks(authority.db.db_path, room_id='room', status='queued')) == 1
        assert not any(e['kind'] == 'message.user' for e in rows['reviewer'][0].hosted_room_service._events('room'))
        rejected = await client.post('/v1/room-controls/room', headers=_headers(rows['default'][2]),
                                     json={**body, 'profile': 'reviewer'})
        assert rejected.status == 400


@pytest.mark.asyncio
async def test_already_draining_send_does_not_parse_body_or_reserve_work(homes, monkeypatch):
    adapter, rows = homes
    adapter.gateway_runner._draining = True
    async def unexpected_body(request):
        pytest.fail('Already-draining requests must not start body parsing')
    monkeypatch.setattr(adapter, '_read_json_body', unexpected_body)
    from gateway.platforms import api_server
    def unexpected_reservation(*args):
        pytest.fail('Already-draining requests must not reserve pending work')
    monkeypatch.setattr(api_server, '_reserve_pending_api_work', unexpected_reservation)
    async with TestClient(TestServer(_app(adapter))) as client:
        response = await client.post('/v1/room-controls/room', headers=_headers(rows['default'][2]),
                                     json={'action': 'send', 'command_id': 'one', 'text': 'Hello'})
        assert response.status == 503
        assert not any(e['kind'] == 'message.user' for e in rows['default'][0].hosted_room_service._events('room'))


@pytest.mark.asyncio
async def test_send_rechecks_drain_after_completed_body_parse(homes, monkeypatch):
    adapter, rows = homes
    original = adapter._read_json_body
    async def parse_then_drain(request):
        result = await original(request)
        adapter.gateway_runner._draining = True
        return result
    monkeypatch.setattr(adapter, '_read_json_body', parse_then_drain)
    before = adapter._pending_agent_requests
    async with TestClient(TestServer(_app(adapter))) as client:
        response = await client.post('/v1/room-controls/room', headers=_headers(rows['default'][2]),
                                     json={'action': 'send', 'command_id': 'one', 'text': 'Hello'})
        assert response.status == 503
        assert adapter._pending_agent_requests == before
        assert not any(e['kind'] == 'message.user' for e in rows['default'][0].hosted_room_service._events('room'))
