"""Page format tests only; no replica writer or authority-change operation."""

import copy

import pytest

from gateway import hosted_room_passive_pages as pages
from gateway import hosted_rooms as rooms


def page(path):
    rooms.create_room(path, room_id="room", name="Room", members=[{"profile": "ops", "handle": "ops"}],
                      authority_gateway_id="owner", now=10)
    for seq in (1, 2):
        rooms.append_event(path, room_id="room", event_id=f"input-{seq}", kind="message.user",
            actor={"kind": "user", "id": "alice"}, payload={"text": "hello"},
            authority_gateway_id="owner", authority_epoch=1, now=10 + seq)
    return rooms.read_events(path, room_id="room", limit=1)


@pytest.mark.parametrize("version", [1, 2])
def test_canonical_page_decodes_without_changing_its_history(tmp_path, version):
    original = page(tmp_path / "source.db")
    if version == 2:
        original.update(replica_version=2, lineage_sha256="a" * 64)
    before = copy.deepcopy(original)
    events, authority, cursor, latest, more = pages.validate_page(original)
    assert original == before
    assert (cursor, latest, more) == (1, 2, True)
    assert authority == original["authority"]
    assert events[0]["payload"] == original["events"][0]["payload"]
    assert events[0]["created_at"] == original["events"][0]["created_at"]


@pytest.mark.parametrize("change", ["version", "epoch", "cursor", "has_more", "duplicate", "timestamp", "extra", "bytes"])
def test_inconsistent_page_cannot_pass_the_staged_decoder(tmp_path, monkeypatch, change):
    value = page(tmp_path / "source.db")
    value.update(replica_version=2, lineage_sha256="a" * 64)
    mutations = {
        "version": lambda: value.update(replica_version=2.0),
        "epoch": lambda: value["authority"].update(epoch=True),
        "cursor": lambda: value.update(cursor=2**63, latest_seq=2**63, has_more=False),
        "has_more": lambda: value.update(has_more=False),
        "duplicate": lambda: value["events"].append({**value["events"][0], "seq": 2}),
        "timestamp": lambda: value["events"][0].update(created_at=True),
        "extra": lambda: value.update(execution_authorized=True),
        "bytes": lambda: monkeypatch.setattr(pages, "MAX_LOG_PAGE_BYTES", 1),
    }
    mutations[change]()
    with pytest.raises(rooms.HostedRoomError):
        pages.validate_page(value)
