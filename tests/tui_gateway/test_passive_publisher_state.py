"""Publisher checkpoint and failure contracts, independent of execution."""

from dataclasses import replace

import pytest

from gateway import hosted_room_links as links
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError
from tests.gateway.passive_ingress_fixtures import pair  # noqa: F401
from tests.tui_gateway.passive_publisher_fixtures import KEY
from tests.tui_gateway.test_passive_publisher_delivery import publisher


@pytest.mark.parametrize("field,value", [("revision", True), ("digest", "wrong"), ("extra", "untrusted"),
                                        ("object", "different.endpoint"), ("extra", float("nan"))])
def test_malformed_work_ack_never_advances_sender_delivery(pair, monkeypatch, field, value):
    pub, receiver = publisher(pair, monkeypatch)
    pub._publish_one(KEY)
    receiver.after_work = lambda ack: {**ack, field: value}
    pub._publish_one(KEY)
    state = pub.status()
    assert state["routes"][0]["work_record_status"] == "invalid_ack"
    assert state["work_records"][0]["status"] == "invalid_ack"
    with rooms._transaction(pair.source) as conn:
        assert conn.execute(f"SELECT status FROM {work.PENDING_TABLE}").fetchone()[0] != "acked"


@pytest.mark.parametrize("status,expected", [(401, "needs_reauthorization"), (409, "rejected"), (507, "unavailable")])
def test_work_refusal_or_outage_does_not_block_history(pair, monkeypatch, status, expected):
    pub, receiver = publisher(pair, monkeypatch)
    pub._publish_one(KEY)
    def refuse(**kwargs):
        raise PeerRunsHTTPError("fixture work unavailable", status_code=status)
    monkeypatch.setattr(receiver, "replicate_work_records", refuse)
    for turn in range(3):
        rooms.append_event(pair.source, room_id="room", event_id=f"new-{turn}", kind="message.user",
            actor={"kind": "user", "id": "alice"}, payload={"text": "more"},
            authority_gateway_id=pair.gateway, authority_epoch=pair.epoch)
        pub._publish_one(KEY)
    assert len(receiver.pages) == 4
    assert pub.status()["routes"][0]["work_record_status"] == expected
    assert pub.status()["routes"][0]["target_acked_seq"] == pair.page()["latest_seq"]


def test_late_history_ack_cannot_advance_replaced_route(pair, monkeypatch):
    pub, receiver = publisher(pair, monkeypatch)
    previous = links.load_room_link(pair.source, room_id="room", member_id="reviewer")
    def replace_route(ack):
        links.save_room_link(pair.source, replace(previous, trace_id="replacement"))
        return ack
    receiver.after_page = replace_route
    pub._publish_one(KEY)
    assert pub.status()["routes"][0]["target_acked_seq"] == 0
    original_page = receiver.pages[-1]
    receiver.after_page = None
    pub._publish_one(KEY)
    assert receiver.pages[-1] == original_page
    assert pub.status()["routes"][0]["target_acked_seq"] == original_page["cursor"]
