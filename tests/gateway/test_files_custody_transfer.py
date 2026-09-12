"""Files custody through real canonical task, event and byte-store helpers."""

from dataclasses import replace
import time

import pytest

from gateway import hosted_room_driver as driver, hosted_rooms
from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactOutbox, RoomArtifactScope, terminal_artifact_manifest
from gateway.hosted_room_attachments import HostedRoomAttachmentStore
from gateway.hosted_room_attachment_access import require_artifact_access, require_transfer_cleanup
from tui_gateway.hosted_room_artifact_service import prepare_output, acknowledge_published


def setup_transfer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    db = home / "state.db"
    hosted_rooms.create_room(db, room_id="room", name="Files", authority_gateway_id="home",
                             members=[dict(member_id="writer", profile="writer", handle="writer")])
    scope = RoomArtifactScope.from_mapping(dict(room_id="room", task_id="dtask:one", execution_generation=1,
        member_id="writer", target_profile="writer", home_install_id="home", target_install_id="producer",
        authority_gateway_id="home", authority_epoch=1))
    outbox = RoomArtifactOutbox(tmp_path / "producer" / "state.db")
    artifact = outbox.put_bytes(scope=scope, data=b"explicitly shared report", source_name="report.txt")
    manifest = terminal_artifact_manifest(outbox.db_path, scope)
    identity = driver.TaskIdentity("room", "dtask:one", "thread", "turn")
    driver.admit_task(db, identity, payload=dict(target_profile="writer", target_member_id="writer",
        prompt="Write", source_event_seq=1), clock=time.time)
    lease = driver.acquire_lease(db, room_id="room", gateway_id="home", authority_epoch=1,
        process_generation="process", ttl_seconds=30, clock=time.time)
    attempt = driver.start_task(db, identity, lease, expected_cancel_generation=0, clock=time.time)
    driver.settle_task(db, attempt, settlement_id="settlement", status="settled",
        result=dict(text="Shared", artifacts=manifest, artifact_scope=scope.as_mapping()), clock=time.time)
    return db, HostedRoomAttachmentStore(db), outbox, scope, artifact, manifest


def test_transfer_only_acknowledges_exact_durable_publication(tmp_path):
    db, store, outbox, scope, artifact, manifest = setup_transfer(tmp_path)
    args = dict(scope=scope, manifest=manifest)
    prepared = prepare_output(store, **args, recipient_member_ids=["writer"], read_artifact=outbox.read)
    assert prepare_output(store, **args, recipient_member_ids=["writer"], read_artifact=outbox.read) == prepared
    with pytest.raises(RoomArtifactError, match="not published"):
        acknowledge_published(store, **args, acknowledge=outbox.acknowledge)
    assert outbox.read(scope, artifact["artifact_id"])[1] == b"explicitly shared report"
    payload = {**prepared["payload"], "text": "Shared"}
    hosted_rooms.append_event(db, room_id="room", event_id=prepared["event_id"], kind="message.member",
        actor=dict(kind="member", id="writer", profile="writer"), payload=payload,
        authority_gateway_id="home", authority_epoch=1)
    assert acknowledge_published(store, **args, acknowledge=outbox.acknowledge) == 1
    assert acknowledge_published(store, **args, acknowledge=outbox.acknowledge) == 0
    assert store.abort_unpublished_event(room_id="room", event_id=prepared["event_id"]) is False
    reference = store.list_published(room_id="room", authority_gateway_id="home", authority_epoch=1)["items"][0]
    assert store.read_viewer(room_id="room", authority_gateway_id="home", authority_epoch=1,
        attachment_id=reference["attachment_id"], event_id=reference["event_id"]).data == b"explicitly shared report"


def test_transfer_refuses_changed_task_manifest_or_bytes_before_visibility(tmp_path):
    db, store, outbox, scope, artifact, manifest = setup_transfer(tmp_path)
    for changed in [replace(scope, execution_generation=2), replace(scope, target_install_id="foreign"),
                    replace(scope, member_id="other")]:
        with pytest.raises(RoomArtifactError):
            prepare_output(store, scope=changed, manifest=manifest, recipient_member_ids=["writer"], read_artifact=outbox.read)
    with pytest.raises(RoomArtifactError, match="bytes"):
        prepare_output(store, scope=scope, manifest=manifest, recipient_member_ids=["writer"],
                       read_artifact=lambda scope, artifact_id: (artifact, b"wrong bytes"))
    with pytest.raises(RoomArtifactError, match="recipients"):
        prepare_output(store, scope=scope, manifest=manifest, recipient_member_ids=["foreign"], read_artifact=outbox.read)
    assert store.list_published(room_id="room", authority_gateway_id="home", authority_epoch=1)["items"] == []
    assert outbox.read(scope, artifact["artifact_id"])[1] == b"explicitly shared report"


def test_cleanup_requires_files_eligibility_not_status(tmp_path):
    _, _, _, scope, _, _ = setup_transfer(tmp_path)
    claims = {**scope.as_mapping(), "permissions": ["status"]}
    with pytest.raises(RoomArtifactError):
        require_transfer_cleanup(claims, scope)
    claims["permissions"].append("attachment.stage")
    require_transfer_cleanup(claims, scope)
    for changed in [{"authority_epoch": True}, {"member_id": "other"}, {"target_profile": "other"}]:
        with pytest.raises(RoomArtifactError):
            require_transfer_cleanup({**claims, **changed}, scope)


def test_artifact_access_is_operation_and_participant_scoped(tmp_path):
    _, _, _, scope, _, _ = setup_transfer(tmp_path)
    claims = {**scope.as_mapping(), "permissions": ["artifact.read"]}
    require_artifact_access(claims, scope, permission="artifact.read")
    with pytest.raises(RoomArtifactError):
        require_artifact_access(claims, scope, permission="artifact.ack")
    with pytest.raises(RoomArtifactError):
        require_artifact_access(claims, replace(scope, authority_epoch=2), permission="artifact.read")
