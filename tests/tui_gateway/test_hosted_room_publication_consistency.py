"""Publication eligibility precedes file access; committed files survive replay."""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from gateway import hosted_room_discussion as discussion
from gateway import hosted_room_driver as driver
from gateway import hosted_rooms
from gateway.hosted_room_artifacts import (
    RoomArtifactError,
    RoomArtifactOutbox,
    RoomArtifactScope,
    terminal_artifact_manifest,
)
from gateway.hosted_room_attachments import AttachmentNotFoundError
from tests.tui_gateway.test_hosted_room_terminal_reconstruction import _service, _start
from tests.tui_gateway.test_hosted_room_artifact_service import _ArtifactPeerClient
from tui_gateway.hosted_room_peer_transport import PeerMemberRoute


def _task(tmp_path, monkeypatch, *, legacy=False):
    if legacy:
        original = discussion.plan_next_task
        monkeypatch.setattr(
            discussion,
            "plan_next_task",
            lambda *args, **kwargs: original(
                *args,
                **{**kwargs, "freeze_input_context": False},
            ),
        )
    service = _service(tmp_path / "state.db")
    service.create_room(
        room_id="publication",
        name="Publication",
        members=[
            {"member_id": "owner", "profile": "default", "handle": "owner"},
            {"member_id": "builder", "profile": "ops", "handle": "builder"},
        ],
    )
    _send(service, "first")
    task = driver.list_tasks(service.db_path, room_id="publication", status="queued")[0]
    return service, task, _start(service, task)


def _send(service, event_id, thread="work"):
    return service.send(
        room_id="publication",
        event_id=event_id,
        payload={"text": f"@builder {event_id}", "thread_id": thread},
    )


def _settle(service, task, attempt):
    room = hosted_rooms.room_state(service.db_path, room_id="publication")
    scope = RoomArtifactScope.from_mapping({
        "room_id": "publication",
        "task_id": task["identity"].task_id,
        "execution_generation": attempt.execution_generation,
        "member_id": "builder",
        "target_profile": "ops",
        "home_install_id": room["authority_gateway_id"],
        "target_install_id": room["authority_gateway_id"],
        "authority_gateway_id": room["authority_gateway_id"],
        "authority_epoch": room["authority_epoch"],
    })
    outbox = RoomArtifactOutbox(service.db_path)
    for index in range(3):
        outbox.put_bytes(
            scope=scope,
            data=f"result-{index}".encode(),
            source_name=f"result-{index}.txt",
        )
    result = {
        "text": "Completed",
        "artifacts": terminal_artifact_manifest(service.db_path, scope),
    }
    driver.settle_task(
        service.db_path,
        attempt,
        settlement_id="result",
        status="settled",
        result=result,
        clock=time.time,
    )
    return outbox, scope, deepcopy(result)


def _tick(service):
    service.prepare_room(service.bindings()[0])


def test_user_send_rollback_cannot_revoke_another_senders_durable_files(
    tmp_path, monkeypatch
):
    first, _, _ = _task(tmp_path, monkeypatch)
    second = _service(first.db_path)
    uploaded = first.put_attachment(
        room_id="publication",
        upload_id="raced-user",
        kind="file",
        name="input.txt",
        mime="text/plain",
        data=b"shared input",
    )
    manifest = [
        {
            key: uploaded[key]
            for key in ("attachment_id", "kind", "name", "size", "mime")
        }
    ]
    payload = {"text": "input", "thread_id": "other", "attachments": manifest}
    original = hosted_rooms.append_event

    def other_sender_wins(*args, **kwargs):
        with monkeypatch.context() as inner:
            inner.setattr(hosted_rooms, "append_event", original)
            inner.setattr(second, "prepare_room", lambda binding: None)
            second.send(room_id="publication", event_id="raced-user", payload=payload)
        raise OSError("first sender lost its response")

    with monkeypatch.context() as pause:
        pause.setattr(hosted_rooms, "append_event", other_sender_wins)
        with pytest.raises(OSError, match="lost its response"):
            first.send(room_id="publication", event_id="raced-user", payload=payload)
    assert (
        second.read_attachment(
            room_id="publication",
            event_id="raced-user",
            recipient_member_id="owner",
            attachment_id=manifest[0]["attachment_id"],
        ).data
        == b"shared input"
    )
    before = second._events("publication")
    assert second.send(room_id="publication", event_id="raced-user", payload=payload)[
        "idempotent"
    ]
    assert second._events("publication") == before


@pytest.mark.parametrize("legacy", [False, True])
def test_permanent_import_failure_fences_another_publishers_staged_manifest(
    tmp_path, monkeypatch, legacy
):
    first, task, attempt = _task(tmp_path, monkeypatch, legacy=legacy)
    outbox, scope, result = _settle(first, task, attempt)
    second = _service(first.db_path)
    append_first, append_second = first._append_plan, second._append_plan
    fenced = []
    ready, release = threading.Event(), threading.Event()
    pending = []

    def suspend_first(*args, **kwargs):
        pending.extend((args, kwargs))
        ready.set()
        assert release.wait(10)
        try:
            return append_first(*args, **kwargs)
        except hosted_rooms.EventCursorConflictError:
            fenced.append(True)
            raise

    def fail_read(*args, **kwargs):
        raise RoomArtifactError("permanent verification failure")

    def after_rollback(room_id, publication, **kwargs):
        assert publication.terminal_kind == "turn.failed"
        assert not outbox.list(scope)
        release.set()
        running.result(timeout=10)
        return append_second(room_id, publication, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool, monkeypatch.context() as pause:
        pause.setattr(first, "_append_plan", suspend_first)
        running = pool.submit(_tick, first)
        try:
            assert ready.wait(10)
            pause.setattr(RoomArtifactOutbox, "read", fail_read)
            pause.setattr(second, "_append_plan", after_rollback)
            _tick(second)
        finally:
            release.set()
            running.result(timeout=10)
    assert fenced == [True]
    assert driver.get_task(first.db_path, task["identity"])["result"] == result
    own = [
        event
        for event in first._events("publication")
        if event["payload"].get("task_id") == task["identity"].task_id
    ]
    assert [event["kind"] for event in own] == ["turn.failed"]
    manifest = pending[0][1].events[0].payload["attachments"]
    message_id = pending[0][1].events[0].event_id
    _assert_no_access(first, manifest, message_id)
    cold = _service(first.db_path)
    _tick(cold)
    _assert_no_access(cold, manifest, message_id)
    assert not cold._artifact_retry_keys("publication")


@pytest.mark.parametrize("legacy", [False, True])
def test_stale_cursor_rollback_fences_fresh_publisher_then_recovers(
    tmp_path, monkeypatch, legacy
):
    first, task, attempt = _task(tmp_path, monkeypatch, legacy=legacy)
    outbox, scope, result = _settle(first, task, attempt)
    second = _service(first.db_path)
    append_first, append_second = first._append_plan, second._append_plan
    pending = []

    def interleave(*args, **kwargs):
        with monkeypatch.context() as pause:
            pause.setattr(second, "prepare_room", lambda binding: None)
            _send(second, "unrelated", thread="other")
        with monkeypatch.context() as pause:
            # Interrupt before the retain/ACK path, as a real paused publisher.
            def suspended(*a, **k):
                pending.extend((a, k))
                raise OSError("publisher suspended")

            pause.setattr(second, "_append_plan", suspended)
            with pytest.raises(OSError, match="publisher suspended"):
                _tick(second)
        return append_first(*args, **kwargs)

    with monkeypatch.context() as pause:
        pause.setattr(first, "_append_plan", interleave)
        _tick(first)
    assert first._artifact_retry_keys("publication")
    with pytest.raises(hosted_rooms.EventCursorConflictError):
        append_second(*pending[0], **pending[1])
    assert len(outbox.list(scope)) == 3
    assert not any(
        event["kind"] == "message.member" for event in first._events("publication")
    )
    cold = _service(first.db_path)
    cold._artifact_clock = lambda: time.time() + 1000
    _tick(cold)
    message = next(
        event
        for event in cold._events("publication")
        if event["kind"] == "message.member"
    )
    for index, item in enumerate(message["payload"]["attachments"]):
        assert (
            cold.attachments.read(
                room_id="publication",
                event_id=message["event_id"],
                attachment_id=item["attachment_id"],
                recipient_member_id="owner",
            ).data
            == f"result-{index}".encode()
        )
    assert not outbox.list(scope)
    assert not cold._artifact_retry_keys("publication")
    assert driver.get_task(first.db_path, task["identity"])["result"] == result
    before = cold._events("publication")
    _tick(cold)
    assert cold._events("publication") == before


@pytest.mark.parametrize("legacy", [False, True])
def test_superseded_files_retire_without_import_or_room_authorization(
    tmp_path, monkeypatch, legacy
):
    service, task, attempt = _task(tmp_path, monkeypatch, legacy=legacy)
    # Legacy admissions retain their original bounded input; modern admissions
    # additionally prove recovery when the old display row is compacted.
    for index in range(1 if legacy else 30):
        _send(service, f"newer-{index}")
    outbox, scope, result = _settle(service, task, attempt)
    imports = []
    original = service._import_terminal_artifacts

    def import_spy(**kwargs):
        imports.append(True)
        return original(**kwargs)

    monkeypatch.setattr(service, "_import_terminal_artifacts", import_spy)
    _tick(service)
    assert imports == []
    assert driver.get_task(service.db_path, task["identity"])["result"] == result
    events = service._events("publication")
    own = [
        event
        for event in events
        if event["payload"].get("task_id") == task["identity"].task_id
    ]
    assert [event["kind"] for event in own] == ["turn.cancelled"]
    assert not outbox.list(scope)
    with sqlite3.connect(service.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM hosted_room_attachments WHERE state='committed'"
            ).fetchone()[0]
            == 0
        )
    _tick(_service(service.db_path))
    assert service._events("publication") == events
    assert not service._artifact_retry_keys("publication")


def test_superseded_retirement_failure_stays_private_and_retries(tmp_path, monkeypatch):
    service, task, attempt = _task(tmp_path, monkeypatch)
    _send(service, "newer")
    outbox, scope, result = _settle(service, task, attempt)
    now = [0.0]
    service._artifact_clock = lambda: now[0]
    original = service._retire_failed_terminal_artifacts
    attempts = []

    def fail_once(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("cleanup temporarily unavailable")
        return original(**kwargs)

    monkeypatch.setattr(service, "_retire_failed_terminal_artifacts", fail_once)
    _tick(service)
    assert len(outbox.list(scope)) == 3
    assert service._artifact_retry_keys("publication")
    assert not any(
        event["payload"].get("task_id") == task["identity"].task_id
        for event in service._events("publication")
    )
    _tick(service)
    assert len(attempts) == 1
    now[0] = 2.0
    _tick(service)
    assert len(attempts) == 2
    assert not outbox.list(scope)
    assert driver.get_task(service.db_path, task["identity"])["result"] == result
    assert not service._artifact_retry_keys("publication")


@pytest.mark.parametrize("legacy", [False, True])
def test_published_files_survive_new_requests_and_legacy_cold_ack(
    tmp_path, monkeypatch, legacy
):
    service, task, attempt = _task(tmp_path, monkeypatch, legacy=legacy)
    outbox, scope, result = _settle(service, task, attempt)
    _tick(service)
    message = next(
        event
        for event in service._events("publication")
        if event["kind"] == "message.member"
    )
    _send(service, "later")
    outbox.prune_acknowledged_receipts(now=time.time() + 86401)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("DELETE FROM hosted_room_policy_events")
        conn.execute("DELETE FROM hosted_room_policy_transcript")
    reads = []

    def private_read(*args, **kwargs):
        reads.append(True)
        raise OSError("private bytes already ACKed")

    monkeypatch.setattr(RoomArtifactOutbox, "read", private_read)
    cold = _service(service.db_path)
    before = cold._events("publication")
    _tick(cold)
    assert not reads
    assert cold._events("publication") == before
    assert not cold._artifact_retry_keys("publication")
    assert driver.get_task(service.db_path, task["identity"])["result"] == result
    for attachment in message["payload"]["attachments"]:
        published = cold.read_attachment(
            room_id="publication",
            attachment_id=attachment["attachment_id"],
            recipient_member_id="owner",
            event_id=message["event_id"],
        )
        assert published.data == attachment["name"].removesuffix(".txt").encode()


@pytest.mark.parametrize("thread", ["work", "other"])
def test_newer_request_is_checked_at_the_atomic_publication_boundary(
    tmp_path, monkeypatch, thread
):
    service, task, attempt = _task(tmp_path, monkeypatch)
    outbox, scope, result = _settle(service, task, attempt)
    original = service._append_plan
    raced = []
    now = [0.0]
    service._artifact_clock = lambda: now[0]

    def interleaved(room_id, publication, **kwargs):
        if publication.events[0].kind == "message.member" and not raced:
            raced.append(True)
            with monkeypatch.context() as pause:
                pause.setattr(service, "prepare_room", lambda binding: None)
                _send(service, "at-publication-boundary", thread=thread)
        return original(room_id, publication, **kwargs)

    monkeypatch.setattr(service, "_append_plan", interleaved)
    _tick(service)
    assert not any(
        event["kind"] == "message.member" for event in service._events("publication")
    )
    assert service._artifact_retry_keys("publication")
    now[0] = 2.0
    _tick(service)
    own = [
        event
        for event in service._events("publication")
        if event["payload"].get("task_id") == task["identity"].task_id
    ]
    expected = (
        ["turn.cancelled"] if thread == "work" else ["message.member", "turn.settled"]
    )
    assert [event["kind"] for event in own] == expected
    assert not outbox.list(scope)
    assert driver.get_task(service.db_path, task["identity"])["result"] == result


def _assert_no_access(service, attachments, message_id):
    for attachment in attachments:
        for viewer in (False, True):
            with pytest.raises(AttachmentNotFoundError):
                service.attachments.read(
                    room_id="publication",
                    attachment_id=attachment["attachment_id"],
                    recipient_member_id="owner",
                    event_id=message_id,
                    viewer=viewer,
                )


def test_supersession_during_import_rolls_back_unpublished_commitment(
    tmp_path, monkeypatch
):
    service, task, attempt = _task(tmp_path, monkeypatch)
    outbox, scope, result = _settle(service, task, attempt)
    message_id = f"dmessage:{task['identity'].task_id.removeprefix('dtask:')}"
    staged = []
    original = service.attachments.commit_message

    def interleaved(**kwargs):
        attachments = original(**kwargs)
        staged.extend(attachments)
        _assert_no_access(service, attachments, message_id)
        with monkeypatch.context() as pause:
            pause.setattr(service, "prepare_room", lambda binding: None)
            _send(service, "accepted-before-publication")
        return attachments

    monkeypatch.setattr(service.attachments, "commit_message", interleaved)
    _tick(service)
    assert len(staged) == 3
    _assert_no_access(service, staged, message_id)
    assert not outbox.list(scope)
    assert driver.get_task(service.db_path, task["identity"])["result"] == result
    own = [
        event
        for event in service._events("publication")
        if event["payload"].get("task_id") == task["identity"].task_id
    ]
    assert [event["kind"] for event in own] == ["turn.cancelled"]


@pytest.mark.parametrize("message_committed", [False, True])
def test_interrupted_publication_preserves_only_durable_message_commitments(
    tmp_path, monkeypatch, message_committed
):
    service, task, attempt = _task(tmp_path, monkeypatch)
    outbox, scope, result = _settle(service, task, attempt)
    message_id = f"dmessage:{task['identity'].task_id.removeprefix('dtask:')}"

    def interrupted(room_id, publication, **kwargs):
        assert publication.events[0].kind == "message.member"
        if message_committed:
            hosted_rooms.append_event(
                service.db_path,
                **publication.events[0].append_kwargs(room_id),
                **kwargs,
            )
        raise OSError("crash at member publication")

    monkeypatch.setattr(service, "_append_plan", interrupted)
    with pytest.raises(OSError, match="crash"):
        _tick(service)
    with sqlite3.connect(service.db_path) as conn:
        attachments = [
            {"attachment_id": row[0]}
            for row in conn.execute(
                "SELECT attachment_id FROM hosted_room_attachments WHERE event_id=?",
                (message_id,),
            )
        ]
    assert len(attachments) == 3
    if not message_committed:
        _assert_no_access(service, attachments, message_id)
    with monkeypatch.context() as pause:
        pause.setattr(service, "prepare_room", lambda binding: None)
        _send(service, "while-offline")
    cold = _service(service.db_path)
    _tick(cold)
    own = [
        event
        for event in cold._events("publication")
        if event["payload"].get("task_id") == task["identity"].task_id
    ]
    assert [event["kind"] for event in own] == (
        ["message.member", "turn.settled"] if message_committed else ["turn.cancelled"]
    )
    assert driver.get_task(service.db_path, task["identity"])["result"] == result
    assert not outbox.list(scope)
    if message_committed:
        assert not cold.attachments.abort_unpublished_event(
            room_id="publication", event_id=message_id
        )
        for attachment in attachments:
            assert cold.attachments.read(
                room_id="publication",
                attachment_id=attachment["attachment_id"],
                recipient_member_id="owner",
                event_id=message_id,
            ).data
    else:
        _assert_no_access(cold, attachments, message_id)
    before = cold._events("publication")
    _tick(cold)
    assert cold._events("publication") == before


def test_expired_retirement_evidence_stays_conservative_without_losing_published_files(
    tmp_path, monkeypatch
):
    service, task, attempt = _task(tmp_path, monkeypatch)
    _settle(service, task, attempt)
    _tick(service)
    before = service._events("publication")
    message = next(event for event in before if event["kind"] == "message.member")
    future = time.time() + 61 * 86400
    monkeypatch.setattr(time, "time", lambda: future)
    cold = _service(service.db_path)
    cold.runtime.clock = lambda: future
    cold._artifact_clock = lambda: future
    _tick(cold)
    _tick(cold)
    assert cold._events("publication") == before
    with sqlite3.connect(service.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM hosted_room_output_generation_fences"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute(
            "SELECT attempts, blocked FROM hosted_room_artifact_retries"
        ).fetchall() == [(1, 1)]
    assert driver.get_task(service.db_path, task["identity"])["status"] == "settled"
    for attachment in message["payload"]["attachments"]:
        assert cold.read_attachment(
            room_id="publication",
            attachment_id=attachment["attachment_id"],
            recipient_member_id="owner",
            event_id=message["event_id"],
        ).data


@pytest.mark.parametrize("valid_retirement", [False, True])
def test_superseded_peer_output_needs_exact_retirement_not_import(
    tmp_path, monkeypatch, valid_retirement
):
    service = _service(tmp_path / "state.db")
    local_id = hosted_rooms.local_authority_gateway_id()
    peer = _ArtifactPeerClient()
    service.peer_routes[("publication", "reviewer")] = PeerMemberRoute(
        home_install_id=local_id,
        member_id="reviewer",
        target_install_id="peer-install",
        target_profile="reviewer",
        capability_digest="a" * 64,
        execution_policy_digest="b" * 64,
        cancellation_scope_id="cancel-publication",
        trace_id="trace-publication",
        grant="synthetic-room-grant",
        attachments=True,
    )
    service.peer_clients[("publication", "reviewer")] = peer
    service.create_room(
        room_id="publication",
        name="Peer publication",
        members=[
            {"member_id": "owner", "profile": "default", "handle": "owner"},
            {
                "member_id": "reviewer",
                "profile": "reviewer",
                "handle": "builder",
                "target": {
                    "kind": "peer",
                    "peer_id": "peer-install",
                    "installation_id": "peer-install",
                    "profile": "reviewer",
                    "capability_digest": "a" * 64,
                },
            },
        ],
    )
    _send(service, "first")
    task = driver.list_tasks(service.db_path, room_id="publication", status="queued")[0]
    attempt = _start(service, task)
    _send(service, "newer")
    result = {"text": "peer result", "run_id": "run-peer", "artifacts": peer.manifest}
    driver.settle_task(
        service.db_path,
        attempt,
        settlement_id="peer-result",
        status="settled",
        result=result,
        clock=time.time,
    )
    reads = []
    discards = []

    def read(*args, **kwargs):
        reads.append(True)
        raise AssertionError("superseded peer output must not be read")

    def discard(**kwargs):
        discards.append(kwargs)
        return {"discarded": True, "removed": 1 if valid_retirement else True}

    monkeypatch.setattr(peer, "read_artifact", read)
    monkeypatch.setattr(peer, "discard_artifacts", discard)
    _tick(service)
    assert reads == []
    assert discards == [{"run_id": "run-peer", "grant": "synthetic-room-grant"}]
    own = [
        event
        for event in service._events("publication")
        if event["payload"].get("task_id") == task["identity"].task_id
    ]
    assert [event["kind"] for event in own] == (
        ["turn.cancelled"] if valid_retirement else []
    )
    assert bool(service._artifact_retry_keys("publication")) is not valid_retirement
    assert driver.get_task(service.db_path, task["identity"])["result"] == result
