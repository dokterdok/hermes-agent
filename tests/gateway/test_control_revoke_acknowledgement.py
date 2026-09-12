"""Revoke receipts control exact local cleanup, never just HTTP success."""

import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_room_controls as controls
from gateway.hosted_room_control_client import RoomControlClientError
from gateway.session_group_controls import dispatch_group_control
from tests.gateway.test_canonical_room_control_http import homes, _headers  # noqa: F401


def application(adapter, mode):
    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    for method, path, handler in adapter._http_route_table():
        if '/room-controls/' not in path:
            continue
        if method == 'DELETE':
            async def receive(request, handler=handler):
                if mode['real']:
                    return await handler(request)
                return web.json_response(mode['response'])
            app.router.add_route(method, '/p/{profile}' + path, receive)
        else:
            app.router.add_route(method, '/p/{profile}' + path, handler)
    return app


def retain_route(client, rows):
    home, _, token = rows['default']
    receiver, actor, _ = rows['reviewer']
    gateway, epoch = home.hosted_room_service._owned_authority('room')
    controls.save_peer_control_link(receiver.db.db_path, room_id='room', member_id='peer',
        home_url=str(client.make_url('/p/default')), authority_gateway_id=gateway, authority_epoch=epoch,
        room_name='Registered room', member_count=1, control_token=token, expires_at=time.time() + 3600)
    return SimpleNamespace(authority=receiver, actor=actor), token


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [{}, {'revoked': False}, {'revoked': True}, {'revoked': -1},
    {'revoked': 2}, {'revoked': 1.0}, {'revoked': '1'}, {'revoked': None}])
async def test_invalid_ack_preserves_bearer_then_real_retry_completes(homes, response):
    adapter, rows = homes
    mode = {'real': False, 'response': response}
    async with TestClient(TestServer(application(adapter, mode))) as client:
        connection, token = retain_route(client, rows)
        with pytest.raises(RoomControlClientError, match='acknowledge revocation'):
            await dispatch_group_control(connection, 'groups.control.revoke', {'room_id': 'room', 'member_id': 'peer'})
        retained = controls.load_peer_control_links(connection.authority.db.db_path, include_inactive=True).links
        assert len(retained) == 1 and retained[0].status == 'revoked'
        assert (await client.get('/p/default/v1/room-controls/room', headers=_headers(token))).status == 200
        mode['real'] = True
        assert await dispatch_group_control(connection, 'groups.control.revoke',
            {'room_id': 'room', 'member_id': 'peer'}) == {'revoked': 1}
        assert controls.load_peer_control_links(connection.authority.db.db_path, include_inactive=True).links == ()
        assert (await client.get('/p/default/v1/room-controls/room', headers=_headers(token))).status == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('already_revoked', [False, True])
async def test_real_zero_or_one_receipt_allows_cleanup(homes, already_revoked):
    adapter, rows = homes
    async with TestClient(TestServer(application(adapter, {'real': True}))) as client:
        connection, token = retain_route(client, rows)
        if already_revoked:
            response = await client.delete('/p/default/v1/room-controls/room', headers=_headers(token))
            assert response.status == 200 and (await response.json()) == {'revoked': 1}
        assert await dispatch_group_control(connection, 'groups.control.revoke',
            {'room_id': 'room', 'member_id': 'peer'}) == {'revoked': 1}
        assert controls.load_peer_control_links(connection.authority.db.db_path, include_inactive=True).links == ()
