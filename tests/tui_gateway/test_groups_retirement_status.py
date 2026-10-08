"""Both room-state adapters retain the process owner's durable cleanup receipt."""
import asyncio

import pytest

from gateway import hosted_room_driver, hosted_rooms, session_group_peer_cleanup
from gateway.session_hosted_service import CanonicalHostedRoomService
from tests.gateway.test_session_group_peers import call, gateway as gateway
from tests.gateway.test_session_group_peer_routes import capabilities, joined
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter', ['canonical', 'legacy'])
async def test_tombstone_state_keeps_pending_then_empty_cleanup_without_reopening(gateway, monkeypatch, adapter):
    import tui_gateway.server as server

    peer, url, _, _, grant = await joined(gateway, monkeypatch)
    original_revoke = PeerRunsHTTPClient.revoke_grant

    def offline(*args, **kwargs):
        raise PeerRunsHTTPError('target offline', retryable=True)

    try:
        monkeypatch.setattr(PeerRunsHTTPClient, 'revoke_grant', offline)
        ended = await call(gateway.owner, 'groups.disband', room_id='linked')
        assert ended['tombstone']['disbanded_at']
        assert session_group_peer_cleanup.status(gateway.service.db_path, 'linked')
        assert await asyncio.to_thread(capabilities, url, grant) == (200, None)

        # Restart the actual lifecycle service from the same durable room/cleanup store.
        restarted = CanonicalHostedRoomService(gateway.authority, None)
        restarted.runtime.status = lambda: {'running': True, 'stopping': False, 'blocked_rooms': []}
        gateway.authority.hosted_room_service = restarted
        monkeypatch.setattr(server, 'get_hosted_room_service', lambda: restarted)

        async def request(method, **params):
            frame = {'id': 1, 'method': method, 'params': params}
            if adapter == 'canonical':
                return await gateway.owner.dispatch(frame)
            def legacy_request():
                # The legacy adapter normally binds the install-wide room DB;
                # here it must read the same real profile-owned service store.
                with monkeypatch.context() as binding:
                    binding.setattr(hosted_rooms, 'default_db_path', lambda: restarted.db_path)
                    return server.handle_request(frame)
            return await asyncio.to_thread(legacy_request)

        assert 'error' in await request('groups.state', room_id='linked')
        pending = await request('groups.state', room_id='linked', include_disbanded=True)
        assert 'error' not in pending, pending
        assert pending['result']['room']['disbanded_at'] == ended['tombstone']['disbanded_at']
        assert pending['result']['driver_status']['peer_cleanup'] == [
            {'room_id': 'linked', 'member_id': 'reviewer', 'mode': 'scope', 'status': 'pending', 'attempts': 1}]
        snapshot = hosted_rooms.room_state(restarted.db_path, room_id='linked', include_disbanded=True)

        monkeypatch.setattr(PeerRunsHTTPClient, 'revoke_grant', original_revoke)
        await asyncio.to_thread(session_group_peer_cleanup.drain, restarted, force=True)
        settled = await request('groups.state', room_id='linked', include_disbanded=True)
        assert settled['result']['driver_status']['peer_cleanup'] == []
        assert settled['result']['room']['disbanded_at'] == ended['tombstone']['disbanded_at']
        assert await asyncio.to_thread(capabilities, url, grant) == (403, 'room_reauthorization_required')

        refused = await request('groups.send', room_id='linked', event_id='after-end', payload={'text': 'do not run'})
        assert 'error' in refused
        assert hosted_rooms.room_state(restarted.db_path, room_id='linked', include_disbanded=True) == snapshot
        assert hosted_room_driver.list_tasks(restarted.db_path, room_id='linked') == []
        assert (await request('groups.list'))['result']['rooms'] == []
    finally:
        await peer.close()
