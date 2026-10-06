"""Unreadable participant fences are not permission to follow a superseded room epoch."""
import sqlite3

import pytest

from gateway import hosted_room_fence as fence
from gateway import hosted_room_succession as succession
from tests.gateway.fixtures.succession import ROOM, copy_to
from tests.gateway.fixtures.succession_world import World
from tests.gateway.test_hosted_room_succession_stalled import owner_continues


@pytest.mark.parametrize('unreadable', [False, True], ids=['known-fence', 'unreadable-fence'])
def test_copy_never_follows_a_fenced_epoch_when_its_fence_store_fails(tmp_path, monkeypatch, unreadable):
    world = World(tmp_path, monkeypatch, ('h', 'c', 'd', 'p'), voters=('h', 'c', 'd'), others=('p',))
    try:
        world.advance(10)
        world.network.stopped.update({'h', 'p'})
        world.t += 65
        owner_continues(world, 'c')
        assert world.head('c')['authority_epoch'] == 2
        participant = world.gateways['p']
        with participant.acting():
            fence.fence_room(participant.runs.path, room_id=ROOM, fence_epoch=2)
        before = world.head('p')
        connect = fence._connect
        def unavailable(path):
            if str(path) == str(participant.runs.path):
                raise sqlite3.OperationalError('fence store unavailable')
            return connect(path)
        if unreadable:
            monkeypatch.setattr(fence, '_connect', unavailable)
        expected = fence.RoomFenceError if unreadable else succession.ProofInvalid
        with pytest.raises(expected):
            copy_to(world.gateways['c'], participant)
        assert world.head('p') == before
    finally:
        world.close()
