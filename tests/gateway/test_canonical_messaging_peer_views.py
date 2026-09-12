"""Peer views use real registered summary HTTP and the recipient Files wire contract."""
import asyncio
from dataclasses import replace
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from gateway import hosted_room_controls as controls, hosted_rooms
from gateway.config import PlatformConfig
from gateway.group_home_consent import disclosure_stamp
from gateway.hosted_room_file_contract import FileAccessError, scope
from gateway.hosted_room_messaging import current_room_backend
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms import api_server_room_controls as http_controls
from gateway.session_authorities import owner_scope
from gateway.session_contract import Principal
from gateway.session_group_delegation import dispatch_owner_delegation
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401
from tests.gateway.test_canonical_messaging_files import publish


@pytest.mark.asyncio
async def test_named_peer_summary_and_files_never_use_ungranted_owner_view_or_global_home(view):
    authority = view.owners['worker']
    actor = Principal('native-owner', authority.profile_id, frozenset({'session:control'}), 'native')
    with owner_scope(authority):
        service = authority.hosted_room_service
        service.authorize_room(actor.subject, 'remote-room', create=True)
        gateway = hosted_rooms.local_authority_gateway_id()
        hosted_rooms.create_room(authority.db.db_path, room_id='remote-room', name='Remote secret', authority_gateway_id=gateway,
            members=[{'member_id': 'peer', 'profile': 'home', 'handle': 'peer'}, {'member_id': 'private', 'profile': 'private', 'handle': 'private'}])
        grant = dispatch_owner_delegation(authority, actor, 'issue', {'room_id': 'remote-room', 'member_id': 'peer', 'request_id': 'test'})
    allowed = publish(authority, 'remote-room', 1, ['peer'], name='peer-allowed.txt')
    publish(authority, 'remote-room', 2, ['private'], name='private-only.txt')
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'unused'}))
    adapter.gateway_runner = view.runner
    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    paths = []
    for method, path, handler in http_controls._http_routes(adapter):
        app.router.add_route(method, '/p/{profile}' + path, handler)

    # Files registration is parent-owned. Mount this explicit contract fixture,
    # using the actual parent authorizer and actual recipient-filtered store.
    async def catalog(request):
        from gateway.hosted_room_file_access import list_local_files
        from types import SimpleNamespace
        owner, principal, room_id, member_id, token = http_controls._authorize(adapter, request)
        room = hosted_rooms.room_state(owner.db.db_path, room_id=room_id)
        expected = scope(room, member_id, 'home')
        assert request.headers['X-Hermes-Room-Profile'] == 'home'
        assert request.headers['X-Hermes-Room-Authority'] == gateway
        assert request.headers['X-Hermes-Room-Epoch'] == '1'
        paths.append(request.path)
        page = list_local_files(SimpleNamespace(service=owner.hosted_room_service, db_path=owner.db.db_path),
                                room=room, member_id=member_id, limit=int(request.query.get('limit', 8)))
        return web.json_response({**page, 'scope': expected})
    app.router.add_get('/p/{profile}/v1/room-controls/{room_id}/files', catalog)
    try:
        async with TestClient(TestServer(app)) as client:
            with owner_scope(view.receiving):
                hosted_rooms.reserve_peer_room(view.receiving.db.db_path,
                    claims={'room_id': 'remote-room', 'member_id': 'peer', 'target_profile': 'home',
                            'authority_gateway_id': gateway, 'authority_epoch': 1}, expires_at=time.time() + 3600)
                controls.save_peer_control_link(view.receiving.db.db_path, room_id='remote-room', member_id='peer',
                    home_url=str(client.make_url('/p/worker')), authority_gateway_id=gateway, authority_epoch=1,
                    room_name='Remote secret', member_count=2, control_token=grant['control_token'], expires_at=time.time() + 3600)
            backend = current_room_backend(view.runner, view.event, disclosure_stamp(view.runner, view.event))
            rooms = await asyncio.to_thread(backend.list_rooms)
            assert [room['name'] for room in rooms] == ['Remote secret']
            result = await view.runner._handle_group_command(replace(view.event, text='/group 1 bots'))
            assert 'Remote secret' in result and 'send <' not in result
            result = await view.runner._handle_group_command(replace(view.event, text='/group 1 files'))
            assert 'peer-allowed.txt' in result and 'private-only.txt' not in result
            assert paths == ['/p/worker/v1/room-controls/remote-room/files']
            # Inaccessible/missing summary is not a working registered peer view.
            with owner_scope(authority):
                dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'remote-room', 'member_id': 'peer'})
            assert await asyncio.to_thread(backend.list_rooms) == []
    finally:
        adapter._run_idempotency_store.close()


@pytest.mark.asyncio
async def test_missing_files_http_is_reported_unavailable_without_a_fallback(view, monkeypatch):
    from gateway.hosted_room_control_client import RoomControlHTTPClient
    from gateway.hosted_room_control_files_client import list_files
    from types import SimpleNamespace
    app = web.Application()
    async with TestClient(TestServer(app)) as client:
        link = SimpleNamespace(status='active', expires_at=time.time()+3600, home_url=str(client.make_url('/p/worker')),
            room_id='room', member_id='peer', control_token='not-a-real-secret', authority_gateway_id='install:home', authority_epoch=1,
            as_status=lambda: {'room_id': 'room', 'authority_gateway_id': 'install:home', 'authority_epoch': 1})
        with pytest.raises(FileAccessError) as failure:
            await asyncio.to_thread(list_files, RoomControlHTTPClient(link), target_profile='home')
        assert failure.value.code == 'file_access_unsupported'
