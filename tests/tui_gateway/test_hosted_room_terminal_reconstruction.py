"""Terminal publication must reconstruct the input that was actually admitted."""

from __future__ import annotations

import sqlite3
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from gateway import hosted_room_discussion as discussion
from gateway import hosted_room_driver as driver
from gateway import hosted_rooms
from gateway.hosted_room_artifacts import (
    RoomArtifactOutbox,
    RoomArtifactScope,
    terminal_artifact_manifest,
)
from gateway.hosted_room_policy_checkpoint import (
    MAX_THREAD_TRANSCRIPT_EVENTS,
    MAX_TRANSCRIPT_POLICY_EVENTS,
)
from tests.tui_gateway.test_hosted_room_service import _server
from tui_gateway.hosted_room_service import HostedRoomService


def _service(db: Path) -> HostedRoomService:
    service = HostedRoomService(_server(), db_path=db)
    service.local_profiles = lambda: ("default", "ops")
    return service


def _start(service, task):
    binding = service.bindings()[0]
    lease = driver.acquire_lease(
        service.db_path,
        room_id=binding.room_id,
        gateway_id=binding.gateway_id,
        authority_epoch=binding.authority_epoch,
        process_generation="reconstruction-test",
        ttl_seconds=300,
        clock=time.time,
    )
    return driver.start_task(
        service.db_path,
        task["identity"],
        lease,
        expected_cancel_generation=task["cancel_generation"],
        clock=time.time,
    )


def _stopped_file_turn(db: Path, status="cancelled", recipient="builder"):
    service = _service(db)
    service.create_room(
        room_id="room-reconstruction",
        name="Decision board",
        members=[
            {"member_id": "pm", "profile": "default", "handle": "pm"},
            {"member_id": "builder", "profile": "ops", "handle": "builder"},
        ],
    )
    attachment = service.attachments.put(
        room_id="room-reconstruction",
        upload_id="acceptance-upload",
        kind="file",
        name="ACCEPTANCE-SPEC.md",
        mime="text/markdown",
        data=b"# Acceptance criteria\n",
    )
    source = service.send(
        room_id="room-reconstruction",
        event_id="initial-request",
        payload={
            "text": f"@{recipient} implement this",
            "thread_id": "design",
            "attachments": [
                {
                    key: attachment[key]
                    for key in ("attachment_id", "kind", "name", "mime", "size")
                }
            ],
        },
    )
    task = driver.list_tasks(db, room_id="room-reconstruction", status="queued")[0]
    assert task["payload"]["attachments"]
    if status == "cancelled":
        assert service.stop_room("room-reconstruction", cancel_id="operator-stop") == 1
    else:
        attempt = _start(service, task)
        if status == "deferred":
            driver.defer_not_admitted_task(
                db, attempt, reason="member_unavailable", clock=time.time
            )
        else:
            driver.settle_task(
                db,
                attempt,
                settlement_id="first-result",
                status="settled" if status == "pass" else "failed",
                result={"text": "(pass)"}
                if status == "pass"
                else {"error": "provider failed"},
                clock=time.time,
            )
    service.prepare_room(service.bindings()[0])
    events = service._events("room-reconstruction")
    kind = "turn.settled" if status == "pass" else f"turn.{status}"
    cancelled = next(event for event in events if event["kind"] == kind)
    assert cancelled["payload"]["seen_through_seq"] == source["seq"]
    return service, source, cancelled


@pytest.mark.parametrize("previous_status", ["cancelled", "pass", "failed", "deferred"])
@pytest.mark.parametrize("legacy", [False, True])
def test_settled_followup_publishes_three_files_after_stopped_file_input(
    tmp_path, monkeypatch, previous_status, legacy
):
    if legacy:
        original_plan = discussion.plan_next_task
        monkeypatch.setattr(
            discussion,
            "plan_next_task",
            lambda *args, **kwargs: original_plan(
                *args,
                **{**kwargs, "freeze_input_context": False},
            ),
        )
    db = tmp_path / "state.db"
    service, source, cancelled = _stopped_file_turn(db, status=previous_status)
    followup = service.send(
        room_id="room-reconstruction",
        event_id="continue-request",
        payload={"text": "@builder continue", "thread_id": "design"},
    )
    task = driver.list_tasks(db, room_id="room-reconstruction", status="queued")[0]
    assert "attachments" not in task["payload"]
    snapshot = service._policy_snapshot(
        hosted_rooms.room_state(db, room_id="room-reconstruction")
    )
    assert snapshot.watermarks[("design", "builder")] == source["seq"]
    assert cancelled["seq"] < followup["seq"]
    attempt = _start(service, task)
    room = hosted_rooms.room_state(db, room_id="room-reconstruction")
    outbox_db = db.parent / "profiles" / "ops" / "state.db"
    scope = RoomArtifactScope.from_mapping({
        "room_id": room["room_id"],
        "task_id": task["identity"].task_id,
        "execution_generation": attempt.execution_generation,
        "member_id": "builder",
        "target_profile": "ops",
        "home_install_id": room["authority_gateway_id"],
        "target_install_id": room["authority_gateway_id"],
        "authority_gateway_id": room["authority_gateway_id"],
        "authority_epoch": room["authority_epoch"],
    })
    outbox = RoomArtifactOutbox(outbox_db)
    names = ["index.html", "app.js", "README.md"]
    for name in names:
        outbox.put_bytes(scope=scope, source_name=name, data=f"result {name}".encode())
    driver.settle_task(
        db,
        attempt,
        settlement_id="builder-result",
        status="settled",
        result={
            "text": "Decision board is ready.",
            "artifacts": terminal_artifact_manifest(outbox_db, scope),
        },
        clock=time.time,
    )
    if legacy:
        assert "input_context" not in task["payload"]
        # Model the v1 projection, without changing its valid driver receipt,
        # canonical log, artifact bytes, or persisted planning watermark.
        with sqlite3.connect(db) as conn:
            conn.execute(
                "DELETE FROM hosted_room_policy_transcript WHERE kind LIKE 'turn.%'"
            )
            conn.execute(
                "UPDATE hosted_room_policy_transcript_state SET schema_version=1"
            )
    resumed = _service(db)
    resumed.prepare_room(resumed.bindings()[0])
    events = resumed._events(room["room_id"])
    messages = [event for event in events if event["kind"] == "message.member"]
    assert len(messages) == 1
    assert {item["name"] for item in messages[0]["payload"]["attachments"]} == set(
        names
    )
    assert outbox.list(scope) == []
    assert outbox.retirement_complete(scope)
    assert not outbox.retirement_complete(replace(scope, task_id="dtask:other"))
    durable = driver.get_task(db, task["identity"])
    assert len(durable["result"]["artifacts"]["items"]) == 3
    if not legacy:
        assert "input_context" in durable["payload"]
    resumed.prepare_room(resumed.bindings()[0])
    assert resumed._events(room["room_id"]) == events
    assert resumed._artifact_retry_keys(room["room_id"]) == set()
    if not legacy:
        outbox.prune_acknowledged_receipts(now=time.time() + 24 * 60 * 60 + 1)
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM hosted_room_policy_events")
            conn.execute("DELETE FROM hosted_room_policy_transcript")

        private_reads = []

        def no_private_read(*args, **kwargs):
            private_reads.append((args, kwargs))
            raise AssertionError("already-ACKed private output was read again")

        monkeypatch.setattr(RoomArtifactOutbox, "read", no_private_read)
        cold = _service(db)
        cold.prepare_room(cold.bindings()[0])
        assert private_reads == []
        assert cold._events(room["room_id"]) == events
        assert cold._artifact_retry_keys(room["room_id"]) == set()
        for attachment in messages[0]["payload"]["attachments"]:
            published = cold.read_attachment(
                room_id=room["room_id"],
                attachment_id=attachment["attachment_id"],
                recipient_member_id=None,
                viewer=True,
                event_id=messages[0]["event_id"],
            )
            assert published.data == f"result {attachment['name']}".encode()


@pytest.mark.parametrize("migrate", [False, True])
def test_service_preserves_remaining_attachments_after_visible_partial_batch(
    tmp_path, monkeypatch, migrate
):
    db = tmp_path / "state.db"
    service = _service(db)
    room = service.create_room(
        room_id="room-batches",
        name="Attachment backlog",
        members=[
            {"member_id": "pm", "profile": "default", "handle": "pm"},
            {"member_id": "builder", "profile": "ops", "handle": "builder"},
        ],
    )
    expected_ids = []
    # Accumulate accepted input before the next policy tick, as a disconnected
    # or occupied worker can. Upload/commit, then planning/publication are real.
    with monkeypatch.context() as paused:
        paused.setattr(service, "prepare_room", lambda binding: None)
        for batch in range(3):
            attachments = []
            for index in range(8):
                stored = service.put_attachment(
                    room_id=room["room_id"],
                    upload_id=f"batch-{batch}-{index}",
                    kind="file",
                    name=f"part-{batch}-{index}.txt",
                    mime="text/plain",
                    data=f"part {batch} {index}".encode(),
                )
                attachments.append({
                    key: stored[key]
                    for key in ("attachment_id", "kind", "name", "mime", "size")
                })
            expected_ids.extend(item["attachment_id"] for item in attachments)
            service.send(
                room_id=room["room_id"],
                event_id=f"batch-user-{batch}",
                payload={
                    "text": "@builder review",
                    "thread_id": "batches",
                    "attachments": attachments,
                },
            )
    binding = service.bindings()[0]
    service.prepare_room(binding)
    task = driver.list_tasks(db, room_id=room["room_id"], status="queued")[0]
    first_ids = [item["attachment_id"] for item in task["payload"]["attachments"]]
    assert first_ids == expected_ids[:16]
    assert task["payload"]["source_event_seq"] == 3
    attempt = _start(service, task)
    driver.settle_task(
        db,
        attempt,
        settlement_id="batch-reply",
        status="settled",
        result={"text": "Reviewed the first batch."},
        clock=time.time,
    )
    if migrate:
        service._publish_terminal_tasks(
            hosted_rooms.room_state(db, room_id=room["room_id"])
        )
        service._policy_snapshot(hosted_rooms.room_state(db, room_id=room["room_id"]))
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE hosted_room_policy_watermarks SET seen_through_seq=4")
            conn.execute(
                "UPDATE hosted_room_policy_transcript_state SET schema_version=1"
            )
        service = _service(db)
    service.prepare_room(binding)
    with sqlite3.connect(db) as conn:
        watermark = conn.execute(
            """SELECT seen_through_seq FROM hosted_room_policy_watermarks
               WHERE room_id=? AND thread_id='batches' AND member_id='builder'""",
            (room["room_id"],),
        ).fetchone()[0]
    assert watermark == 2
    queued = driver.list_tasks(db, room_id=room["room_id"], status="queued")
    assert len(queued) == 1
    assert (
        first_ids
        + [item["attachment_id"] for item in queued[0]["payload"]["attachments"]]
        == expected_ids
    )


def test_pending_task_reconstructs_original_input_after_thread_compaction(tmp_path):
    db = tmp_path / "state.db"
    service, _, _ = _stopped_file_turn(db, status="pass", recipient="pm")
    source = service.send(
        room_id="room-reconstruction",
        event_id="builder-request",
        payload={"text": "@builder review the earlier file", "thread_id": "design"},
    )
    task = driver.list_tasks(db, room_id="room-reconstruction", status="queued")[0]
    assert len(task["payload"]["attachments"]) == 1
    for index in range(30):
        service.send(
            room_id="room-reconstruction",
            event_id=f"while-pending-{index}",
            payload={
                "text": f"@builder additional detail {index}",
                "thread_id": "design",
            },
        )
    resumed = _service(db)
    room = hosted_rooms.room_state(db, room_id="room-reconstruction")
    events = resumed.policy_checkpoint.events_for_task(
        room_id=room["room_id"],
        source_event_seq=source["seq"],
        input_context=task["payload"].get("input_context"),
    )
    reconstructed = discussion.reconstruct_task_plan(
        room,
        events,
        task,
        local_profiles=resumed.local_profiles(),
    )
    assert dict(reconstructed.payload) == task["payload"]


@pytest.mark.parametrize("status", ["cancelled", "pass", "failed", "deferred"])
@pytest.mark.parametrize("legacy", [False, True])
def test_own_terminal_replay_uses_admission_not_current_watermark(
    tmp_path, monkeypatch, status, legacy
):
    if legacy:
        original_plan = discussion.plan_next_task
        monkeypatch.setattr(
            discussion,
            "plan_next_task",
            lambda *args, **kwargs: original_plan(
                *args,
                **{**kwargs, "freeze_input_context": False},
            ),
        )
    service, source, terminal = _stopped_file_turn(tmp_path / "state.db", status=status)
    task = next(
        task
        for task in driver.list_tasks(service.db_path, room_id=source["room_id"])
        if task["identity"].task_id == terminal["payload"]["task_id"]
    )
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("UPDATE hosted_room_policy_watermarks SET seen_through_seq=999999")
    events = service.policy_checkpoint.events_for_task(
        room_id=source["room_id"],
        source_event_seq=source["seq"],
        input_context=task["payload"].get("input_context"),
    )
    plan = discussion.reconstruct_task_plan(
        hosted_rooms.room_state(service.db_path, room_id=source["room_id"]),
        events,
        task,
        local_profiles=service.local_profiles(),
    )
    assert dict(plan.payload) == task["payload"]
    assert plan.identity == task["identity"]
    assert len(plan.payload["attachments"]) == 1


def test_input_receipt_is_bound_to_identity_and_missing_canonical_input_fails_closed(
    tmp_path,
):
    service, source, _ = _stopped_file_turn(tmp_path / "state.db")
    followup = service.send(
        room_id=source["room_id"],
        event_id="continue-request",
        payload={"text": "@builder continue", "thread_id": "design"},
    )
    task = driver.list_tasks(
        service.db_path, room_id=source["room_id"], status="queued"
    )[0]
    events = service.policy_checkpoint.events_for_task(
        room_id=source["room_id"],
        source_event_seq=followup["seq"],
        input_context=task["payload"]["input_context"],
    )
    room = hosted_rooms.room_state(service.db_path, room_id=source["room_id"])
    altered = deepcopy(task)
    altered["payload"]["input_context"]["watermark"] = 0
    with pytest.raises(
        discussion.DiscussionReconstructionError, match="deterministic reconstruction"
    ):
        discussion.reconstruct_task_plan(
            room, events, altered, local_profiles=service.local_profiles()
        )
    altered = deepcopy(task)
    altered["payload"]["attachments"] = source["payload"]["attachments"]
    with pytest.raises(
        discussion.DiscussionReconstructionError, match="deterministic reconstruction"
    ):
        discussion.reconstruct_task_plan(
            room, events, altered, local_profiles=service.local_profiles()
        )
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "DELETE FROM hosted_room_events WHERE room_id=? AND seq=?",
            (source["room_id"], followup["seq"]),
        )
    with pytest.raises(RuntimeError, match="input event is missing"):
        service.policy_checkpoint.events_for_task(
            room_id=source["room_id"],
            source_event_seq=followup["seq"],
            input_context=task["payload"]["input_context"],
        )
    assert driver.get_task(service.db_path, task["identity"])["status"] == "queued"


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"watermark": True, "event_seqs": [1]},
        {"watermark": 0, "event_seqs": []},
        {"watermark": 1, "event_seqs": [1]},
        {"watermark": 0, "event_seqs": [2, 1]},
        {"watermark": 0, "event_seqs": [1, 1]},
        {"watermark": 0, "event_seqs": list(range(1, 130))},
        {"watermark": 0, "event_seqs": [2**63]},
    ],
)
def test_driver_rejects_invalid_input_receipts(tmp_path, context):
    service, source, terminal = _stopped_file_turn(tmp_path / "state.db")
    payload = next(
        task["payload"]
        for task in driver.list_tasks(service.db_path, room_id=source["room_id"])
        if task["identity"].task_id == terminal["payload"]["task_id"]
    )
    payload["input_context"] = context
    with pytest.raises(driver.DriverValidationError, match="input"):
        driver.admit_task(
            service.db_path,
            driver.TaskIdentity(source["room_id"], "bad", "design", "bad"),
            payload=payload,
            clock=time.time,
        )


def test_transcript_migration_is_paged_restartable_and_normal_reads_stay_bounded(
    tmp_path, monkeypatch
):
    service, source, _ = _stopped_file_turn(tmp_path / "state.db", status="pass")
    for index in range(30):
        service.send(
            room_id=source["room_id"],
            event_id=f"pending-{index}",
            payload={"text": f"@builder detail {index}", "thread_id": "design"},
        )
    room = hosted_rooms.room_state(service.db_path, room_id=source["room_id"])
    canonical_before = service._events(source["room_id"])
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("UPDATE hosted_room_policy_transcript_state SET schema_version=1")
    original_read = hosted_rooms.read_events
    reads = []

    def interrupted(*args, **kwargs):
        reads.append((kwargs["since_seq"], kwargs["limit"]))
        if kwargs["since_seq"] > 0:
            raise RuntimeError("restart during migration")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(hosted_rooms, "MAX_LOG_LIMIT", 7)
    monkeypatch.setattr(hosted_rooms, "read_events", interrupted)
    with pytest.raises(RuntimeError, match="restart during migration"):
        service.policy_checkpoint.sync(
            room_id=room["room_id"], latest_seq=room["latest_seq"]
        )
    assert reads == [(0, 7), (7, 7)]
    monkeypatch.setattr(hosted_rooms, "read_events", original_read)
    resumed = _service(service.db_path)
    snapshot = resumed._policy_snapshot(room)
    assert snapshot.through_seq == room["latest_seq"]
    assert (
        len([event for event in snapshot.events if event["kind"] == "message.user"])
        <= MAX_THREAD_TRANSCRIPT_EVENTS
    )
    assert len(snapshot.events) <= MAX_TRANSCRIPT_POLICY_EVENTS + 64
    assert resumed._events(room["room_id"]) == canonical_before

    def no_replay(*args, **kwargs):
        raise AssertionError("caught-up checkpoint reread the log")

    monkeypatch.setattr(hosted_rooms, "read_events", no_replay)
    assert resumed._policy_snapshot(room) == snapshot


def test_frozen_input_reads_use_bounded_indexed_lookups_after_projection_cleanup(
    tmp_path,
):
    service, source, _ = _stopped_file_turn(
        tmp_path / "state.db", status="pass", recipient="pm"
    )
    followup = service.send(
        room_id=source["room_id"],
        event_id="builder-request",
        payload={"text": "@builder review", "thread_id": "design"},
    )
    task = driver.list_tasks(
        service.db_path, room_id=source["room_id"], status="queued"
    )[0]
    canonical = service._events(source["room_id"])
    with service.policy_checkpoint._connect() as conn:
        conn.execute("DELETE FROM hosted_room_policy_events")
        conn.execute("DELETE FROM hosted_room_policy_transcript")
        # Unrelated history must not turn exact admitted-input retrieval into a
        # room-log scan. These are ordinary indexed canonical user events.
        template = next(event for event in canonical if event["seq"] == followup["seq"])
        import json

        conn.executemany(
            """INSERT INTO hosted_room_events
               (room_id,seq,event_id,kind,actor_json,authority_epoch,payload_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    source["room_id"],
                    1000 + index,
                    f"unrelated-{index}",
                    "message.user",
                    json.dumps(template["actor"]),
                    template["authority_epoch"],
                    json.dumps({"thread_id": "other", "text": "irrelevant"}),
                    1,
                )
                for index in range(5000)
            ],
        )
    original_connect = service.policy_checkpoint._connect

    def bounded_connection():
        conn = original_connect()
        steps = 0

        def budget():
            nonlocal steps
            steps += 100
            return int(steps > 5000)

        conn.set_progress_handler(budget, 100)
        return conn

    service.policy_checkpoint._connect = bounded_connection
    events = service.policy_checkpoint.events_for_task(
        room_id=source["room_id"],
        source_event_seq=followup["seq"],
        input_context=task["payload"]["input_context"],
    )
    assert {event["seq"] for event in events} == {source["seq"], followup["seq"]}
    assert len(events) <= len(task["payload"]["input_context"]["event_seqs"]) + 2
    reconstructed = discussion.reconstruct_task_plan(
        hosted_rooms.room_state(service.db_path, room_id=source["room_id"]),
        events,
        task,
        local_profiles=service.local_profiles(),
    )
    assert reconstructed.payload == task["payload"]


def test_missing_private_rows_are_not_proof_of_completed_retirement(
    tmp_path, monkeypatch
):
    service, source, terminal = _stopped_file_turn(tmp_path / "state.db")
    room = hosted_rooms.room_state(service.db_path, room_id=source["room_id"])
    scope = RoomArtifactScope.from_mapping({
        "room_id": room["room_id"],
        "task_id": terminal["payload"]["task_id"],
        "execution_generation": 1,
        "member_id": "builder",
        "target_profile": "ops",
        "home_install_id": room["authority_gateway_id"],
        "target_install_id": room["authority_gateway_id"],
        "authority_gateway_id": room["authority_gateway_id"],
        "authority_epoch": room["authority_epoch"],
    })
    outbox = RoomArtifactOutbox(service.db_path)
    assert not outbox.retirement_complete(scope)
    artifact = outbox.put_bytes(scope=scope, source_name="private.txt", data=b"private")
    original_reclaim = outbox._reclaim_blob_rows
    monkeypatch.setattr(outbox, "_reclaim_blob_rows", lambda *args, **kwargs: 0)
    event_id = f"dmessage:{scope.task_id.removeprefix('dtask:')}"
    outbox.acknowledge(scope, [artifact["artifact_id"]], message_event_id=event_id)
    assert not outbox.retirement_complete(scope)
    monkeypatch.setattr(outbox, "_reclaim_blob_rows", original_reclaim)
    outbox.acknowledge(scope, [artifact["artifact_id"]], message_event_id=event_id)
    assert outbox.retirement_complete(scope)
