"""Canonical owner-only retirement recovery survives End and runtime reconstruction."""
import time

import pytest

from gateway import hosted_rooms
from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret
from gateway.session_controls import AuthorityConnection
from gateway.session_hosted_service import CanonicalHostedRoomService
from tests.gateway.test_session_group_peers import call, gateway as gateway
from tests.gateway.test_session_group_peer_routes import joined


@pytest.mark.asyncio
async def test_ended_owner_can_settle_expired_peer_authority_without_restoring_routes(gateway, monkeypatch):
    server, url, room, catalog, grant = await joined(gateway, monkeypatch)
    try:
        claims = decode_room_grant(gateway_room_grant_secret(), grant, permission='status')
        narrow = await call(gateway.owner, 'groups.peer.invite', retirement_only=True,
            **{key: claims[key] for key in ('room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch', 'member_id')})
        refused = await call(gateway.owner, 'groups.peer.register', room_id='linked', member_id='reviewer',
            target_url=url, target_profile='default', grant=narrow['grant'], catalog=catalog)
        assert refused == 'room_retirement_not_granted'
        assert gateway.service.peer_routes[('linked', 'reviewer')].grant == grant
        future = claims['status_expires_at'] + 3600
        monkeypatch.setattr(time, 'time', lambda: future)
        ended = await call(gateway.owner, 'groups.disband', room_id='linked')
        pending, = ended['retirements']
        assert pending['status'] == 'needs_reauthorization'
        assert hosted_rooms.list_room_link_records(gateway.service.db_path) == []
        future += hosted_rooms.DISBANDED_ROOM_RETENTION_SECONDS + 1
        hosted_rooms.prune_disbanded_rooms(gateway.service.db_path, now=future)
        gateway.service = gateway.authority.hosted_room_service = CanonicalHostedRoomService(gateway.authority, None)
        gateway.service.runtime.status = lambda: {'running': True, 'stopping': False}
        assert (await call(gateway.owner, 'groups.peer.retirements'))['retirements'] == [pending]
        other = AuthorityConnection(gateway.authority, object(), {'user_id': 'other'}, operator=True)
        assert await call(other, 'groups.peer.retirements') == {'retirements': []}
        assert await call(other, 'groups.peer.retire', room_id='linked', retirement_id=pending['retirement_id']) == 'permission_denied'
        fresh = await call(gateway.owner, 'groups.peer.invite', retirement_only=True,
            **{key: claims[key] for key in ('room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch', 'member_id')})
        assert isinstance(fresh, dict), fresh
        assert set(decode_room_grant(gateway_room_grant_secret(), fresh['grant'], permission='retire')['permissions']) == {'status', 'retire'}
        assert await call(gateway.owner, 'groups.peer.retire', room_id='linked', retirement_id=pending['retirement_id'], grant=fresh['grant']) == {'retirements': []}
        assert gateway.service.peer_routes == {}
        assert await call(gateway.owner, 'groups.send', room_id='linked', event_id='late', payload={'text': 'must remain ended'}) == 'room_retiring'
    finally:
        await server.close()
