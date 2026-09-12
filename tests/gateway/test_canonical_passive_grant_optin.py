"""Copy permissions require explicit opt-in and never extend execution lifetime."""
import pytest

from gateway.hosted_room_peer import (
    HostedRoomGrantError, decode_room_grant, invitation_permissions, issue_room_grant,
)


@pytest.mark.parametrize('replication,work,passive,expected', [
    (False, False, False, {'approve', 'dispatch', 'status', 'stop'}),
    (True, False, False, {'approve', 'dispatch', 'status', 'stop', 'replicate'}),
    (True, True, False, {'approve', 'dispatch', 'status', 'stop', 'replicate', 'work_records'}),
    (True, False, True, {'status', 'replicate'}),
    (True, True, True, {'status', 'replicate', 'work_records'}),
])
def test_explicit_optins_share_signed_observation_horizon(replication, work, passive, expected):
    file_permissions = {'attachment.stage', 'artifact.read', 'artifact.ack'}
    if not passive:
        expected = expected | file_permissions
    permissions = invitation_permissions(replication, work, passive_only=passive)
    assert set(permissions) == expected
    secret = b'passive-test-key-not-a-real-secret'
    token = issue_room_grant(secret, grant_id='grant', room_id='room', home_install_id='home',
        authority_gateway_id='home', authority_epoch=1, member_id='member', target_install_id='target',
        target_profile='default', execution_policy_digest='a' * 64, permissions=permissions,
        issued_at=100, ttl_seconds=60, status_ttl_seconds=600)
    for permission in ('replicate', 'work_records', 'status', 'artifact.read', 'artifact.ack'):
        if permission in expected:
            assert decode_room_grant(secret, token, permission=permission, now=200)['status_expires_at'] == 700
            with pytest.raises(HostedRoomGrantError):
                decode_room_grant(secret, token, permission=permission, now=700)
        else:
            with pytest.raises(HostedRoomGrantError):
                decode_room_grant(secret, token, permission=permission, now=110)
    with pytest.raises(HostedRoomGrantError):
        decode_room_grant(secret, token, permission='dispatch', now=200)
    with pytest.raises(HostedRoomGrantError):
        decode_room_grant(secret, token, permission='attachment.stage', now=200)
    if passive:
        for permission in {'dispatch', 'stop', 'approve'} | file_permissions:
            with pytest.raises(HostedRoomGrantError):
                decode_room_grant(secret, token, permission=permission, now=110)


@pytest.mark.parametrize('options', [
    {'replication': 'true'}, {'replication': 1}, {'work_records': True},
    {'passive_only': True}, {'replication': True, 'work_records': 1},
    {'replication': True, 'passive_only': 'true'},
])
def test_invalid_or_implicit_copy_consent_is_rejected(options):
    with pytest.raises(HostedRoomGrantError):
        invitation_permissions(**options)


@pytest.mark.asyncio
async def test_http_invitation_uses_explicit_signed_copy_permissions(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'test-only-passive-key'}))
    app = web.Application()
    for method, path, handler in adapter._http_route_table():
        if path == '/v1/room-members/invitations':
            app.router.add_route(method, path, handler)
    params = {'room_id': 'room', 'home_install_id': 'home', 'authority_gateway_id': 'home',
              'authority_epoch': 1, 'member_id': 'member', 'replication': True,
              'work_records': True, 'passive_only': True}
    try:
        async with TestClient(TestServer(app)) as client:
            path = '/v1/room-members/invitations'
            assert (await client.post(path, json=params)).status == 401
            headers = {'Authorization': 'Bearer test-only-passive-key'}
            response = await client.post(path, json=params, headers=headers)
            assert response.status == 201
            value = await response.json()
            claims = decode_room_grant(adapter._room_grant_secret(), value['grant'], permission='work_records')
            assert set(claims['permissions']) == {'status', 'replicate', 'work_records'}
            assert claims['target_profile'] == 'default'
            for bad in ({'replication': 'yes'}, {'replication': False, 'work_records': True}):
                response = await client.post(path, json={**params, **bad}, headers=headers)
                assert response.status == 400
    finally:
        adapter._run_idempotency_store.close()
