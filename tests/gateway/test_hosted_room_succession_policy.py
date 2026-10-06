"""Policy changes retain the previous authority guard until voters store the new choice."""
from contextlib import closing

from gateway import hosted_room_custody as custody
from gateway import hosted_rooms as rooms
from tests.gateway.fixtures.succession import ROOM
from tests.gateway.fixtures.succession_world import World


def test_disabling_automatic_during_partition_does_not_create_two_hosts(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c', 'd'), voters=('h', 'c', 'd'))
    try:
        world.advance(10)
        assert world.admitting() == {('h', 1)}
        world.network.isolate('h')
        host = world.gateways['h']
        with host.acting():
            custody.set_automatic(host.db, room_id=ROOM, enabled=False)
        world.configure('h')
        assert world.configuration('h')['automatic'] is False
        with host.acting(), closing(rooms._read_connection(host.db)) as conn:
            assert custody.admission_mode_locked(conn, ROOM) == 'majority'
        world.advance(180)
        assert world.admitting() == {('c', 2)}
    finally:
        world.close()


def test_acknowledged_disable_releases_the_old_lease_requirement(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, ('h', 'c', 'd'), voters=('h', 'c', 'd'))
    try:
        world.advance(10)
        host = world.gateways['h']
        with host.acting():
            custody.set_automatic(host.db, room_id=ROOM, enabled=False)
        world.settle('h')
        with host.acting(), closing(rooms._read_connection(host.db)) as conn:
            assert custody.admission_mode_locked(conn, ROOM) == 'ask'
        world.network.isolate('h')
        world.advance(180)
        assert world.admitting() == {('h', 1)}
    finally:
        world.close()
