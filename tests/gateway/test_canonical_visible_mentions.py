"""Visible routing preserves canonical file receipts and frozen admissions."""
from types import SimpleNamespace

import pytest

from gateway import hosted_room_discussion as discussion
from gateway import hosted_room_driver as driver
from gateway.hosted_room_attachments import HostedRoomAttachmentStore
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch


PROFILES = ("alice", "bob", "carol")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with SessionDB(tmp_path / "state.db") as db:
        authority = SimpleNamespace(db=db, profile_id=str(tmp_path),
            epoch=begin_runtime_epoch(db, instance_id="mentions"))
        instance = CanonicalHostedRoomService(authority, None)
        instance.local_profiles = lambda: PROFILES
        instance.authorize_room("native-owner", "room", create=True)
        instance.create_room(room_id="room", name="Mentions", members=[
            {"member_id": name, "profile": name, "handle": name} for name in PROFILES])
        yield instance


def uploaded_manifest(service):
    stored = HostedRoomAttachmentStore(service.db_path).put(
        room_id="room", upload_id="upload", kind="file", name="@unknown.txt",
        mime="text/plain", data=b"@carol file contents are not a routing request")
    return {key: stored[key] for key in ("attachment_id", "kind", "name", "mime", "size")}


def test_visible_mentions_keep_file_identity_and_admitted_input_frozen(service):
    manifest = uploaded_manifest(service)
    caption = "`@all` [notes](https://example.test/@alice) @bob. Please inspect."
    source = service.send(room_id="room", event_id="input", payload={
        "thread_id": "thread", "text": caption, "attachments": [manifest]})
    task, = driver.list_tasks(service.db_path, room_id="room")
    assert task["status"] == "queued"
    assert task["payload"]["target_member_id"] == "bob"
    assert caption in task["payload"]["prompt"]
    assert task["payload"]["attachments"] == [{**manifest, "event_id": source["event_id"]}]
    assert task["payload"]["input_context"] == {"watermark": 0, "event_seqs": [source["seq"]]}
    saved = HostedRoomAttachmentStore(service.db_path).read(
        room_id="room", attachment_id=manifest["attachment_id"],
        event_id=source["event_id"], recipient_member_id="bob")
    assert saved.data == b"@carol file contents are not a routing request"

    # A later caption cannot reselect the target or rewrite an admitted payload.
    service.send(room_id="room", event_id="later", payload={
        "thread_id": "thread", "text": "@carol Handle the next request."})
    cold = CanonicalHostedRoomService(service.authority, None)
    cold.local_profiles = lambda: PROFILES
    cold.prepare_room(cold.bindings()[0])
    assert driver.list_tasks(service.db_path, room_id="room") == [task]
    replayed = discussion.reconstruct_task_plan(cold._room("room"), cold._events("room"),
        task, local_profiles=PROFILES)
    assert replayed.identity == task["identity"]
    assert replayed.payload == task["payload"]


@pytest.mark.parametrize("caption", ["@unknown inspect this", "@all @bob @typo inspect this"])
def test_unknown_visible_mentions_settle_without_file_task_admission(service, caption):
    manifest = uploaded_manifest(service)
    source = service.send(room_id="room", event_id="input", payload={
        "thread_id": "thread", "text": caption, "attachments": [manifest]})
    assert driver.list_tasks(service.db_path, room_id="room") == []
    activity, = [event for event in service._events("room") if event["kind"] == "room.activity"]
    assert activity["payload"] == {
        "status": "settled", "reason_code": "unresolved_mention",
        "thread_id": "thread", "discussion_event_id": source["event_id"]}
    before = service._events("room")
    service.prepare_room(service.bindings()[0])
    assert service._events("room") == before
    assert driver.list_tasks(service.db_path, room_id="room") == []
    gateway, epoch = service._owned_authority("room")
    saved = HostedRoomAttachmentStore(service.db_path).read_viewer(
        room_id="room", attachment_id=manifest["attachment_id"], event_id=source["event_id"],
        authority_gateway_id=gateway, authority_epoch=epoch)
    assert saved.data == b"@carol file contents are not a routing request"
