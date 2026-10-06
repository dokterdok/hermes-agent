"""Delayed promises cannot contradict a newer durable fence, authority or live lease."""
import pytest

from gateway import hosted_room_fence as fence
from tests.gateway.test_hosted_room_fence import ROOM, path as path, promise
from tests.gateway.test_hosted_room_fence_lease import ticking as ticking


@pytest.mark.parametrize('leased', [False, True])
def test_delayed_candidate_cannot_claim_a_learned_authoritys_epoch(path, ticking, leased):
    fence.learn_authority(path, room_id=ROOM, epoch=2, install_id='install:host')
    if leased:
        fence.grant_lease(path, room_id=ROOM, epoch=2, authority_install_id='install:host', duration_s=20)
    before = fence.room_fence_state(path, ROOM)
    with pytest.raises(fence.RoomAuthorityConflict):
        promise(path, 'install:other', 2)
    assert fence.room_fence_state(path, ROOM) == before


def test_a_promise_replay_cannot_bypass_a_later_explicit_fence(path):
    promise(path, 'install:old', 2)
    fence.fence_room(path, room_id=ROOM, fence_epoch=2)
    with pytest.raises(fence.RoomAuthorityFenced):
        promise(path, 'install:old', 2)


def test_a_promise_replay_cannot_bypass_a_newer_live_lease(path, ticking):
    promise(path, 'install:old', 2)
    fence.grant_lease(path, room_id=ROOM, epoch=3, authority_install_id='install:host', duration_s=20)
    with pytest.raises(fence.RoomLeaseActive):
        promise(path, 'install:old', 2)


def test_same_authority_keeps_its_idempotent_promise_while_leased(path, ticking):
    first = promise(path, 'install:host', 2)
    fence.learn_authority(path, room_id=ROOM, epoch=2, install_id='install:host')
    fence.grant_lease(path, room_id=ROOM, epoch=2, authority_install_id='install:host', duration_s=20)
    replay = promise(path, 'install:host', 2)
    assert replay['idempotent'] and replay['promise'] == first['promise']
