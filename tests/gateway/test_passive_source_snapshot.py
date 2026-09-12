"""Read-only page snapshots used by the passive publisher."""

import pytest

from gateway import hosted_room_passive_lineage as lineage
from gateway import hosted_room_replica_source as source
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair  # noqa: F401


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
