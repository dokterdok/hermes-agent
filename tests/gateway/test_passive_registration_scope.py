"""Installation scope of registered passive APIs, without publisher activation."""

from pathlib import Path
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_rooms as rooms
from gateway import hosted_room_replica_retirement as retirement
from gateway import hosted_room_work_records as work
from gateway.config import PlatformConfig
from gateway.platforms import api_server


PASSIVE_PATHS = {
    '/v1/room-members/invitations', '/v1/room-members/capabilities',
    '/v1/room-members/replica', '/v1/room-members/work-records',
    '/v1/group-replicas/enroll', '/v1/group-replicas/revoke-enrollment', '/v1/group-replicas/retire',
}


def endpoint(tmp_path, monkeypatch, profile):
    root = tmp_path / '.hermes'
    home = root if profile == 'default' else root / 'profiles' / profile
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('model: {}\n')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    from hermes_cli.profiles import get_active_profile_name
    assert get_active_profile_name() == profile
    key = 'disposable-' + profile + '-profile-api-key'
    adapter = api_server.APIServerAdapter(PlatformConfig(enabled=True, extra={'key': key}))
    middleware = [adapter._make_profile_prefix_middleware(), api_server.cors_middleware,
                  api_server.body_limit_middleware, api_server.security_headers_middleware]
    app = web.Application(middlewares=middleware, client_max_size=api_server.MAX_REQUEST_BYTES)
    for method, path, handler in adapter._http_route_table():
        if path in PASSIVE_PATHS:
            app.router.add_route(method, path, handler)
            app.router.add_route(method, '/p/{profile}' + path, handler)
    return adapter, app, {'Authorization': 'Bearer ' + key}, root


def enrollment(target):
    return dict(enrollment_id='review-enrollment', room_id='review-room',
        authority_gateway_id='install:source', authority_epoch=1, target_install_id=target,
        roster_sha256=retirement.roster_digest([{'member_id': 'member', 'profile': 'default', 'handle': 'member'}]),
        commitment='a' * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['enroll', 'revoke-enrollment'])
async def test_named_daemon_key_cannot_administer_installation_retirement(tmp_path, monkeypatch, operation):
    adapter, app, headers, root = endpoint(tmp_path, monkeypatch, 'reviewer')
    assert rooms.default_db_path() == root / 'shared-state.db'
    value = enrollment(rooms.local_authority_gateway_id())
    if operation == 'revoke-enrollment':
        # Existing installation-owner setup is fixture data, not a source notice.
        retirement.enroll_target(root / 'shared-state.db', enrollment=value,
                                 target_install_id=value['target_install_id'])
    body = {'enrollment': value} if operation == 'enroll' else {
        'room_id': value['room_id'], 'enrollment_id': value['enrollment_id']}
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/v1/group-replicas/' + operation, json=body, headers=headers)
            stored = None
            if (root / 'shared-state.db').is_file():
                with sqlite3.connect(root / 'shared-state.db') as conn:
                    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                         (retirement.ENROLLMENT_TABLE,)).fetchone()
                    if table:
                        stored = conn.execute(f'SELECT state FROM {retirement.ENROLLMENT_TABLE} WHERE enrollment_id=?',
                                              (value['enrollment_id'],)).fetchone()
            assert response.status in {400, 401, 403, 404}, {'status': response.status, 'shared_store_row': stored}
            assert stored == (None if operation == 'enroll' else ('active',))
    finally:
        adapter._run_idempotency_store.close()


@pytest.mark.asyncio
async def test_default_installation_setup_is_registered_but_profile_alias_is_refused(tmp_path, monkeypatch):
    adapter, app, headers, root = endpoint(tmp_path, monkeypatch, 'default')
    value = enrollment(rooms.local_authority_gateway_id())
    try:
        async with TestClient(TestServer(app)) as client:
            path = '/v1/group-replicas/enroll'
            assert (await client.post(path, json={'enrollment': value})).status == 401
            response = await client.post(path, json={'enrollment': value}, headers=headers)
            assert response.status == 200
            assert (await client.post('/p/default' + path, json={'enrollment': value}, headers=headers)).status in {400, 404}
            response = await client.post('/v1/group-replicas/revoke-enrollment',
                json={'room_id': value['room_id'], 'enrollment_id': value['enrollment_id']}, headers=headers)
            assert response.status == 200
    finally:
        adapter._run_idempotency_store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['default', 'reviewer'])
async def test_registered_invitation_discovery_and_receivers_share_selected_profile(tmp_path, monkeypatch, profile):
    adapter, app, owner_headers, _ = endpoint(tmp_path, monkeypatch, profile)
    install = rooms.local_authority_gateway_id()
    members = [{'member_id': 'member', 'profile': profile, 'handle': 'member', 'target': {
        'kind': 'peer', 'peer_id': 'participant', 'installation_id': install, 'profile': profile,
        'capability_digest': 'b' * 64}}]
    source = tmp_path / 'source.db'
    rooms.create_room(source, room_id='room', name='Public history', members=members,
                      authority_gateway_id='install:source')
    rooms.append_event(source, room_id='room', event_id='input', kind='message.user',
        actor={'kind': 'user', 'id': 'owner'}, payload={'text': 'hello'},
        authority_gateway_id='install:source', authority_epoch=1)
    page = rooms.read_events(source, room_id='room')
    body = dict(room_id='room', room_name='Public history', members=members, page=page)
    params = dict(room_id='room', home_install_id='install:source', authority_gateway_id='install:source',
                  authority_epoch=1, member_id='member')
    prefix = '/p/' + profile
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post(prefix + '/v1/room-members/invitations', json=params, headers=owner_headers)
            assert response.status == 201
            normal = (await response.json())['grant']
            assert (await client.post(prefix + '/v1/room-members/replica', json=body,
                headers={'Authorization': 'HermesRoom ' + normal})).status == 401
            response = await client.post(prefix + '/v1/room-members/invitations',
                json={**params, 'replication': True, 'work_records': True, 'passive_only': True}, headers=owner_headers)
            assert response.status == 201
            grant = (await response.json())['grant']
            headers = {'Authorization': 'HermesRoom ' + grant}
            response = await client.get(prefix + '/v1/room-members/capabilities', headers=headers)
            assert response.status == 200
            proof = await response.json()
            assert proof['target_profile'] == profile
            assert 2 in proof['passive_replication']['history_versions']
            assert proof['retirement_enrollment'] is None
            wrong = 'reviewer' if profile == 'default' else 'default'
            assert (await client.get('/p/' + wrong + '/v1/room-members/capabilities', headers=headers)).status == 404
            if profile == 'default':
                entry = dict(enrollment_id='discovery-enrollment', room_id='room',
                    authority_gateway_id='install:source', authority_epoch=1, target_install_id=install,
                    roster_sha256=retirement.roster_digest(members), commitment='c' * 64)
                response = await client.post('/v1/group-replicas/enroll', json={'enrollment': entry}, headers=owner_headers)
                assert response.status == 200
                response = await client.get(prefix + '/v1/room-members/capabilities', headers=headers)
                assert response.status == 200
                assert (await response.json())['retirement_enrollment']['enrollment_id'] == entry['enrollment_id']
            response = await client.post(prefix + '/v1/room-members/replica', json=body, headers=headers)
            assert response.status == 200
            record = work.capture(source, room_id='room', local_gateway_id='install:source')
            response = await client.post(prefix + '/v1/room-members/work-records', json={'record': record}, headers=headers)
            assert response.status == 200
            assert (await response.json())['passive'] is True
    finally:
        adapter._run_idempotency_store.close()
