"""Read-only page snapshots used by the passive publisher."""

import pytest
import sqlite3

from gateway import hosted_room_passive_lineage as lineage
from gateway import hosted_room_replica_source as source
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair  # noqa: F401


def test_v1_page_cannot_mix_header_with_a_later_committed_append(pair, monkeypatch):
    original = source._room_row
    def append_after_header(conn, *args):
        row = original(conn, *args)
        try:
            with rooms._transaction(pair.source, immediate=True) as writer:
                writer.execute('PRAGMA busy_timeout=0')
                writer.execute("INSERT INTO hosted_room_events VALUES ('room',3,'concurrent','message.user',?,1,?,12)",
                               ('{"kind":"user","id":"alice"}', '{"text":"new input"}'))
                writer.execute("UPDATE hosted_rooms SET next_seq=4 WHERE room_id='room'")
        except sqlite3.OperationalError as exc:
            # DELETE-journal readers can defer this ordinary writer's commit.
            assert 'locked' in str(exc).lower(), str(exc)
        return row
    monkeypatch.setattr(source, '_room_row', append_after_header)
    page = source.read_replica_page(pair.source, room_id='room')
    assert page['cursor'] <= page['latest_seq'], page
    assert all(event['seq'] <= page['latest_seq'] for event in page['events'])
    # The page reader releases its snapshot before returning to the publisher.
    rooms.append_event(pair.source, room_id='room', event_id='after-read', kind='message.user',
        actor={'kind': 'user', 'id': 'alice'}, payload={'text': 'later'},
        authority_gateway_id=pair.gateway, authority_epoch=pair.epoch)


def test_v2_descriptor_and_page_share_a_pinned_sqlite_view(pair, monkeypatch):
    pair.successor_fixture()
    original = lineage.source_locked
    observed = []
    def pinned(conn, room_id, authority):
        observed.append(conn.in_transaction)
        return original(conn, room_id, authority)
    monkeypatch.setattr(lineage, "source_locked", pinned)
    page = source.read_replica_page(pair.source, room_id="room", replica_version=2, limit=1)
    assert observed == [True]
    assert page["authority"] == {"gateway_id": pair.gateway, "epoch": pair.epoch}
    assert page["cursor"] == 1 and page["latest_seq"] == 3
    assert page["has_more"] is True
    assert page["events"][0]["authority_epoch"] == 1
    # The read snapshot must be released before network delivery or another write.
    with rooms._transaction(pair.source, immediate=True) as conn:
        _, digest = original(conn, "room", page["authority"])
    assert page["lineage_sha256"] == digest


@pytest.mark.parametrize("version", [None, True, 1, 2.0])
def test_v1_bytes_preserved_and_unknown_versions_refused(pair, version):
    if version is None:
        assert source.read_replica_page(pair.source, room_id="room") == rooms.read_events(pair.source, room_id="room")
    else:
        with pytest.raises(rooms.HostedRoomError):
            source.read_replica_page(pair.source, room_id="room", replica_version=version)
