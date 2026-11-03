"""Renewal serializes its durable route publication with re-seating and Disband."""

import asyncio
import threading

import pytest

from gateway import hosted_room_links, hosted_rooms
from gateway.session_group_peers import room_link
from tests.gateway.test_session_group_peers import gateway as gateway_fixture, serve, linked_room, _seat, call

gateway = gateway_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['register', 'disband'])
async def test_renewal_save_cannot_overwrite_registration_or_disband(gateway, monkeypatch, operation):
    peer, url = await serve(gateway, monkeypatch)
    service, key = gateway.service, ('linked', 'reviewer')
    reached, release, lock = threading.Event(), threading.Event(), threading.Lock()

    class PublicationLock:
        def __enter__(self):
            if not lock.acquire(blocking=False):
                release.set()  # The competitor must wait for renewal's publication.
                assert lock.acquire(timeout=10)

        def __exit__(self, *args):
            lock.release()
            release.set()  # Without serialization the competitor finishes before renewal saves.

    try:
        catalog = room_link(gateway.authority)['catalog']
        room = await linked_room(gateway, catalog)
        original, _ = await _seat(gateway, room, url, catalog)
        save = service._save_link

        def paused_save(**link):
            if link['grant'] == 'renewed-grant':
                reached.set()
                assert release.wait(10)
            save(**link)

        monkeypatch.setattr(service, '_save_link', paused_save)
        monkeypatch.setattr(service, 'peer_route_lock', PublicationLock())
        renewal = asyncio.create_task(asyncio.to_thread(
            service._rotate_route_grant, *key, 'renewed-grant', previous=original))
        assert await asyncio.to_thread(reached.wait, 10)
        if operation == 'register':
            replacement, result = await _seat(gateway, room, url, catalog)
            assert result['registered'], result
        else:
            assert 'tombstone' in await call(gateway.owner, 'groups.disband', room_id='linked')
        await renewal
        links = hosted_room_links.load_room_links(service.db_path)
        if operation == 'register':
            assert service.peer_routes[key].grant == links[0].grant == replacement
        else:
            assert key not in service.peer_routes and links == ()
            with pytest.raises(hosted_rooms.HostedRoomError):
                service._rotate_route_grant(*key, 'late-renewal', previous=original)
            assert hosted_room_links.load_room_links(service.db_path) == ()
    finally:
        release.set()
        await peer.close()
