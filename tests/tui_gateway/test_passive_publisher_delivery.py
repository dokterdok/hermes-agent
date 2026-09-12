"""Bounded history/work delivery through real passive stores, no inference."""

import threading

import pytest

from gateway import hosted_room_replicas as replicas
from gateway import hosted_rooms as rooms
from tui_gateway import hosted_room_replication as publishing
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError
from tests.gateway.passive_ingress_fixtures import pair  # noqa: F401
from tests.tui_gateway.passive_publisher_fixtures import KEY, Receiver, enroll, save_link


def publisher(pair, monkeypatch):
    receiver = Receiver(pair)
    monkeypatch.setattr(publishing, "PassiveReplicationHTTPClient", lambda **kwargs: receiver)
    save_link(pair)
    return publishing.HostedRoomReplicationPublisher(pair.source, local_gateway_id=pair.gateway), receiver


@pytest.mark.parametrize("version", [1, 2])
def test_history_and_anchored_work_progress_during_continuous_input(pair, monkeypatch, version):
    if version == 2:
        pair.successor_fixture()
        enroll(pair)
    pub, receiver = publisher(pair, monkeypatch)
    for turn in range(6):
        pub._publish_one(KEY)
        rooms.append_event(pair.source, room_id="room", event_id=f"busy-{turn}", kind="message.user",
            actor={"kind": "user", "id": "alice"}, payload={"text": "more"},
            authority_gateway_id=pair.gateway, authority_epoch=pair.epoch)
    assert len(receiver.pages) == 6
    assert len(receiver.records) >= 2
    assert receiver.records[0]["history"]["seq"] < receiver.pages[-1]["cursor"]
    assert receiver.records[0]["version"] == version
    assert not rooms.list_rooms(pair.target)
    assert pub.status()["source_loss_safe"] is False


@pytest.mark.parametrize("stream", ["history", "work"])
def test_lost_ack_retries_the_same_retained_prefix_after_restart(pair, monkeypatch, stream):
    pub, receiver = publisher(pair, monkeypatch)
    def lost_ack(_):
        raise PeerRunsHTTPError("fixture reply unavailable", retryable=True)
    if stream == "history":
        receiver.after_page = lost_ack
        pub._publish_one(KEY)
        original = receiver.pages[-1]
        receiver.after_page = None
    else:
        pub._publish_one(KEY)
        receiver.after_work = lost_ack
        pub._publish_one(KEY)
        original = receiver.records[-1]
        receiver.after_work = None
    rooms.append_event(pair.source, room_id="room", event_id="later", kind="message.user",
        actor={"kind": "user", "id": "alice"}, payload={"text": "later"},
        authority_gateway_id=pair.gateway, authority_epoch=pair.epoch)
    restarted = publishing.HostedRoomReplicationPublisher(pair.source, local_gateway_id=pair.gateway)
    restarted._publish_one(KEY)
    assert (receiver.pages if stream == "history" else receiver.records)[-1] == original
    assert not rooms.list_rooms(pair.target)


def test_explicit_worker_lifecycle_copies_then_joins_without_starting_room_work(pair, monkeypatch):
    pub, receiver = publisher(pair, monkeypatch)
    copied = threading.Event()
    receiver.after_work = lambda ack: (copied.set(), ack)[1]
    monkeypatch.setattr(publishing, "POLL_SECONDS", 0.05)
    assert pub.status()["running"] is False
    pub.start()
    try:
        assert copied.wait(8)
    finally:
        assert pub.stop(timeout=5)
    assert not any(thread.is_alive() for thread in pub._threads)
    assert replicas.replica_state(pair.target, room_id="room")["work_records"]["availability"] == "available"
    assert not rooms.list_rooms(pair.target)
