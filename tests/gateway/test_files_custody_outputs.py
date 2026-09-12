"""Explicit producer custody, exact versions and retirement without an executor."""

from dataclasses import replace
import hashlib
import json
import time

import pytest

from gateway.hosted_room_artifacts import (
    RoomArtifactError, RoomArtifactOutbox, RoomArtifactScope,
    terminal_artifact_manifest, validate_terminal_artifact_manifest,
)
from gateway.hosted_room_artifacts_classic import ClassicExports


def scope(**values):
    fields = dict(room_id="room-1", task_id="dtask:one", execution_generation=1,
                  member_id="writer", target_profile="writer", home_install_id="home",
                  target_install_id="target", authority_gateway_id="home", authority_epoch=1)
    fields.update(values)
    return RoomArtifactScope.from_mapping(fields)


def test_output_versions_require_exact_scope_and_ack_commitment(tmp_path):
    outbox = RoomArtifactOutbox(tmp_path / "state.db")
    first = outbox.put_bytes(scope=scope(), data=b"first", source_name="report.txt")
    second = outbox.put_bytes(scope=scope(), data=b"second", source_name="report.txt")
    assert first["artifact_id"] != second["artifact_id"]
    assert outbox.put_bytes(scope=scope(), data=b"first", source_name="report.txt") == first
    manifest = terminal_artifact_manifest(outbox.db_path, scope())
    assert len(validate_terminal_artifact_manifest(manifest)) == 2
    with pytest.raises(RoomArtifactError):
        validate_terminal_artifact_manifest({**manifest, "version": True})
    duplicates = [manifest["items"][0], manifest["items"][0]]
    digest = hashlib.sha256(json.dumps(duplicates, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(RoomArtifactError, match="repeats"):
        validate_terminal_artifact_manifest(dict(version=1, items=duplicates, manifest_digest=digest))
    for changed in (dict(member_id="other"), dict(authority_epoch=2), dict(execution_generation=2),
                    dict(task_id="dtask:other"), dict(target_profile="other")):
        with pytest.raises(RoomArtifactError):
            outbox.read(scope(**changed), first["artifact_id"])
    with pytest.raises(RoomArtifactError):
        outbox.acknowledge(scope(), [first["artifact_id"]], message_event_id="wrong")
    assert outbox.read(scope(), first["artifact_id"])[1] == b"first"
    ids = [x["artifact_id"] for x in manifest["items"]]
    assert outbox.acknowledge(scope(), ids, message_event_id="dmessage:one") == 2
    reopened = RoomArtifactOutbox(outbox.db_path)
    assert reopened.acknowledge(scope(), ids, message_event_id="dmessage:one") == 0
    with pytest.raises(RoomArtifactError):
        reopened.put_bytes(scope=scope(), data=b"late", source_name="report.txt")


def test_output_generation_retirement_does_not_cross_member_or_epoch(tmp_path):
    outbox = RoomArtifactOutbox(tmp_path / "state.db")
    original = outbox.put_bytes(scope=scope(), data=b"old", source_name="report.txt")
    other_scope = replace(scope(), member_id="other")
    other = outbox.put_bytes(scope=other_scope, data=b"other", source_name="report.txt")
    current_scope = replace(scope(), execution_generation=2)
    current = outbox.put_bytes(scope=current_scope, data=b"new", source_name="report.txt")
    with pytest.raises(RoomArtifactError):
        outbox.read(scope(), original["artifact_id"])
    assert outbox.read(other_scope, other["artifact_id"])[1] == b"other"
    outbox.discard_durably(current_scope)
    assert RoomArtifactOutbox(outbox.db_path).retirement_complete(current_scope)
    with pytest.raises(RoomArtifactError):
        outbox.read(current_scope, current["artifact_id"])
    next_epoch = replace(scope(), authority_epoch=2)
    assert outbox.put_bytes(scope=next_epoch, data=b"epoch two", source_name="report.txt")


def request(request_id):
    return dict(request_id=request_id, group_id="classic-group", thread_id="thread",
                recipients=[dict(installation="target", profile="reviewer")], issued_at=time.time())


def test_classic_published_versions_survive_later_turns_and_reopen(tmp_path):
    producer = ClassicExports(tmp_path)
    first_request = request("first")
    first, admitted = producer.admit("session", first_request, "write a report")
    assert admitted
    artifact = producer.outbox.put_bytes(scope=producer.scope(first), data=b"version 1", source_name="report.txt")
    producer.settle(first["export_id"], "shared", True)
    second, _ = producer.admit("session", request("second"), "revise it")
    revised = producer.outbox.put_bytes(scope=producer.scope(second), data=b"version 2", source_name="report.txt")
    producer.settle(second["export_id"], "revised", True)
    reopened = ClassicExports(tmp_path)
    assert reopened.read(first["export_id"], artifact["artifact_id"])[1] == b"version 1"
    assert reopened.read(second["export_id"], revised["artifact_id"])[1] == b"version 2"
    assert reopened.admit("session", first_request, "write a report")[1] is False
    with pytest.raises(RoomArtifactError):
        reopened.admit("session", first_request, "different input")


def test_classic_unpublished_and_retired_exports_never_become_downloads(tmp_path):
    producer = ClassicExports(tmp_path)
    row, _ = producer.admit("session", request("pending"), "write")
    artifact = producer.outbox.put_bytes(scope=producer.scope(row), data=b"private", source_name="report.txt")
    assert producer.status(row["export_id"])["items"] == []
    with pytest.raises(RoomArtifactError):
        producer.read(row["export_id"], artifact["artifact_id"])
    producer.settle(row["export_id"], "shared", True)
    producer.retire_group("classic-group")
    with pytest.raises(RoomArtifactError):
        producer.read(row["export_id"], artifact["artifact_id"])
    with pytest.raises(RoomArtifactError):
        producer.admit("session", request("later"), "write again")
