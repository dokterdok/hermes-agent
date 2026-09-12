"""Focused tests for scoped RoomLink attachment staging."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_rooms
from gateway.config import PlatformConfig
from gateway.hosted_room_peer import (
    HostedRoomGrantError,
    HostedMemberDispatch,
    attachment_manifest_digest,
    decode_room_grant,
    issue_room_grant,
)
from gateway.platforms import api_server_room_attachments as room_attachments
from gateway.platforms.api_server import APIServerAdapter, body_limit_middleware


TARGET_INSTALL = "install:target"


def _manifest(data: bytes = b"hello") -> list[dict[str, object]]:
    return [
        {
            "attachment_id": "att_0123456789abcdef0123456789abcdef",
            "kind": "file",
            "name": "brief.txt",
            "size": len(data),
            "mime": "text/plain",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    ]


def _dispatch(manifest=None, **overrides) -> HostedMemberDispatch:
    prompt = "Review the attached brief."
    value = {
        "protocol_version": 2,
        "room_id": "room-1",
        "home_install_id": "install-home",
        "authority_gateway_id": "gateway-home",
        "authority_epoch": 1,
        "member_id": "member-reviewer",
        "target_install_id": TARGET_INSTALL,
        "target_profile": "default",
        "task_id": "task-1",
        "execution_generation": 1,
        "source_event_seq": 1,
        "cancellation_scope_id": "cancel-1",
        "prompt": prompt,
        "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
        "capability_digest": "a" * 64,
        "execution_policy_digest": "b" * 64,
        "trace_id": "trace-1",
        **overrides,
    }
    if manifest is not None:
        value["attachment_manifest_digest"] = attachment_manifest_digest(manifest)
    return HostedMemberDispatch.from_mapping(value)


def _claims(dispatch: HostedMemberDispatch) -> dict[str, object]:
    return {
        "room_id": dispatch.room_id,
        "home_install_id": dispatch.home_install_id,
        "authority_gateway_id": dispatch.authority_gateway_id,
        "authority_epoch": dispatch.authority_epoch,
        "member_id": dispatch.member_id,
        "target_install_id": dispatch.target_install_id,
        "target_profile": dispatch.target_profile,
    }


def _legacy_batch_key(dispatch: HostedMemberDispatch) -> str:
    identity = "\0".join(
        (
            dispatch.home_install_id,
            dispatch.room_id,
            dispatch.member_id,
            dispatch.target_install_id,
            dispatch.target_profile,
            dispatch.task_id,
            str(dispatch.execution_generation),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def test_spool_is_idempotent_and_survives_restart(tmp_path: Path):
    db = tmp_path / "state.db"
    root = tmp_path / "spool"
    manifest = _manifest()
    dispatch = _dispatch(manifest)
    claims = _claims(dispatch)
    first = room_attachments.RoomAttachmentSpool(db, root=root)
    assert first.prepare(dispatch, manifest)["idempotent"] is False
    uploaded = first.put(
        claims=claims,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    assert uploaded == {"complete": True, "idempotent": False}
    assert first.put(
        claims=claims,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    ) == {"complete": True, "idempotent": True}

    restarted = room_attachments.RoomAttachmentSpool(db, root=root)
    assert restarted.prepare(dispatch, manifest)["complete"] is True
    assert restarted.require_complete(dispatch) == manifest
    assert stat_mode(root) == 0o700
    assert all(stat_mode(path) == 0o600 for path in root.iterdir())

    materialized = restarted.materialize(dispatch)
    assert [item["name"] for item in materialized] == ["brief.txt"]
    assert Path(materialized[0]["path"]).read_bytes() == b"hello"


def test_prepare_reclaims_superseded_generation_for_the_same_scoped_task(
    tmp_path: Path,
):
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db", root=tmp_path / "spool"
    )
    manifest = _manifest()
    first = _dispatch(manifest, execution_generation=1)
    second = _dispatch(manifest, execution_generation=2)
    first_key = spool.prepare(first, manifest)["batch_key"]
    spool.put(
        claims=_claims(first),
        task_id=first.task_id,
        execution_generation=first.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    first_path = spool._file_path(first_key, str(manifest[0]["attachment_id"]))
    assert first_path.is_file()

    second_key = spool.prepare(second, manifest)["batch_key"]

    assert second_key != first_key
    assert not first_path.exists()
    with sqlite3.connect(spool.db_path) as conn:
        assert conn.execute(
            """SELECT execution_generation FROM roomlink_attachment_batches
                 WHERE room_id='room-1' AND task_id='task-1'"""
        ).fetchall() == [(2,)]
    with pytest.raises(
        room_attachments.RoomAttachmentSpoolConflict, match="superseded"
    ):
        spool.prepare(first, manifest)

    assert spool.discard_attempt(
        claims=_claims(second),
        task_id=second.task_id,
        execution_generation=second.execution_generation,
    ) == 1
    with pytest.raises(
        room_attachments.RoomAttachmentSpoolConflict, match="already retired"
    ):
        spool.prepare(second, manifest)


@pytest.mark.parametrize(
    ("batch_limit", "file_limit", "expected_label"),
    [
        ("MAX_SPOOL_BATCHES", "MAX_SPOOL_FILES", "gateway"),
        ("MAX_ROOM_SPOOL_BATCHES", "MAX_ROOM_SPOOL_FILES", "room"),
        ("MAX_MEMBER_SPOOL_BATCHES", "MAX_MEMBER_SPOOL_FILES", "member"),
    ],
)
@pytest.mark.parametrize("limited_dimension", ["batch", "file"])
def test_prepare_enforces_registration_count_quotas_after_idempotent_fast_path(
    tmp_path: Path,
    monkeypatch,
    batch_limit: str,
    file_limit: str,
    expected_label: str,
    limited_dimension: str,
):
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db", root=tmp_path / "spool"
    )
    manifest = _manifest()
    first = _dispatch(manifest)
    second = _dispatch(
        manifest,
        task_id="task-2",
        cancellation_scope_id="cancel-2",
        trace_id="trace-2",
    )
    monkeypatch.setattr(
        room_attachments,
        batch_limit,
        1 if limited_dimension == "batch" else 2,
    )
    monkeypatch.setattr(
        room_attachments,
        file_limit,
        1 if limited_dimension == "file" else 2,
    )

    spool.prepare(first, manifest)
    assert spool.prepare(first, manifest)["idempotent"] is True

    with pytest.raises(
        room_attachments.RoomAttachmentSpoolError,
        match=rf"RoomLink {expected_label} attachment registration quota is full",
    ):
        spool.prepare(second, manifest)


def test_prune_serializes_orphan_sweep_with_concurrent_batch_commit(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "spool"
    spool = room_attachments.RoomAttachmentSpool(tmp_path / "state.db", root=root)
    entered_sweep = threading.Event()
    release_sweep = threading.Event()
    writer_done = threading.Event()
    errors: list[BaseException] = []
    original_iterdir = Path.iterdir

    def blocking_iterdir(path: Path):
        if path == root and threading.current_thread().name == "stale-prune":
            entered_sweep.set()
            assert release_sweep.wait(5)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", blocking_iterdir)
    prune_thread = threading.Thread(target=spool.prune, name="stale-prune")
    prune_thread.start()
    assert entered_sweep.wait(5)

    manifest = _manifest()
    dispatch = _dispatch(manifest)
    claims = _claims(dispatch)

    def write_batch():
        try:
            spool.prepare(dispatch, manifest)
            spool.put(
                claims=claims,
                task_id=dispatch.task_id,
                execution_generation=dispatch.execution_generation,
                attachment_id=str(manifest[0]["attachment_id"]),
                data=b"hello",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            writer_done.set()

    writer_thread = threading.Thread(target=write_batch, name="batch-writer")
    writer_thread.start()
    assert not writer_done.wait(0.1)
    release_sweep.set()
    prune_thread.join(5)
    writer_thread.join(5)

    assert not errors
    assert writer_done.is_set()
    assert spool.require_complete(dispatch) == manifest


def test_complete_batch_uses_durable_flags_and_hashes_each_file_only_on_use(
    tmp_path: Path, monkeypatch
):
    first_data = b"first"
    second_data = b"second"
    manifest = [
        {
            "attachment_id": "att_0123456789abcdef0123456789abcdef",
            "kind": "file",
            "name": "first.txt",
            "size": len(first_data),
            "mime": "text/plain",
            "sha256": hashlib.sha256(first_data).hexdigest(),
        },
        {
            "attachment_id": "att_fedcba9876543210fedcba9876543210",
            "kind": "file",
            "name": "second.txt",
            "size": len(second_data),
            "mime": "text/plain",
            "sha256": hashlib.sha256(second_data).hexdigest(),
        },
    ]
    dispatch = _dispatch(manifest)
    claims = _claims(dispatch)
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db", root=tmp_path / "spool"
    )
    reads: list[Path] = []
    original = spool._read_verified

    def tracked(path, *, size, digest):
        reads.append(Path(path))
        return original(path, size=size, digest=digest)

    monkeypatch.setattr(spool, "_read_verified", tracked)
    spool.prepare(dispatch, manifest)
    for item, data in zip(manifest, (first_data, second_data), strict=True):
        spool.put(
            claims=claims,
            task_id=dispatch.task_id,
            execution_generation=dispatch.execution_generation,
            attachment_id=str(item["attachment_id"]),
            data=data,
        )

    assert reads == []
    assert spool.prepare(dispatch, manifest)["complete"] is True
    assert spool.require_complete(dispatch) == manifest
    assert len(reads) == 2

    reads.clear()
    materialized = spool.materialize(dispatch)
    assert [item["name"] for item in materialized] == ["first.txt", "second.txt"]
    assert len(reads) == 2


def test_legacy_spool_migration_requires_current_authority_retransmission(
    tmp_path: Path,
):
    db = tmp_path / "state.db"
    root = tmp_path / "spool"
    root.mkdir()
    manifest = _manifest()
    dispatch = _dispatch(manifest)
    key = _legacy_batch_key(dispatch)
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE roomlink_attachment_batches (
            batch_key TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            home_install_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            target_install_id TEXT NOT NULL,
            target_profile TEXT NOT NULL,
            task_id TEXT NOT NULL,
            execution_generation INTEGER NOT NULL,
            manifest_digest TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE roomlink_attachment_files (
            batch_key TEXT NOT NULL,
            attachment_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            stored INTEGER NOT NULL DEFAULT 0 CHECK (stored IN (0, 1)),
            PRIMARY KEY (batch_key, attachment_id),
            FOREIGN KEY (batch_key) REFERENCES roomlink_attachment_batches(batch_key)
                ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """INSERT INTO roomlink_attachment_batches
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            key,
            dispatch.room_id,
            dispatch.home_install_id,
            dispatch.member_id,
            dispatch.target_install_id,
            dispatch.target_profile,
            dispatch.task_id,
            dispatch.execution_generation,
            dispatch.attachment_manifest_digest,
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            100.0,
            1000.0,
        ),
    )
    conn.execute(
        "INSERT INTO roomlink_attachment_files VALUES (?, ?, ?, ?, 1)",
        (
            key,
            manifest[0]["attachment_id"],
            manifest[0]["sha256"],
            manifest[0]["size"],
        ),
    )
    conn.commit()
    conn.close()
    token = hashlib.sha256(
        f"{key}\0{manifest[0]['attachment_id']}".encode()
    ).hexdigest()
    (root / token).write_bytes(b"hello")

    spool = room_attachments.RoomAttachmentSpool(
        db,
        root=root,
        clock=lambda: 200.0,
    )

    with sqlite3.connect(db) as migrated:
        authority = migrated.execute(
            """SELECT authority_gateway_id, authority_epoch
                 FROM roomlink_attachment_batches WHERE batch_key=?""",
            (key,),
        ).fetchone()
    assert authority == ("legacy", 0)

    prepared = spool.prepare(dispatch, manifest)
    assert prepared["idempotent"] is False
    assert prepared["batch_key"] != key
    spool.put(
        claims=_claims(dispatch),
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    assert spool.require_complete(dispatch) == manifest
    assert Path(spool.materialize(dispatch)[0]["path"]).read_bytes() == b"hello"


def test_identical_attempt_coordinates_are_isolated_across_authority_epochs(
    tmp_path: Path,
):
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db",
        root=tmp_path / "spool",
    )
    manifest = _manifest()
    old = _dispatch(manifest, authority_gateway_id="gateway-old", authority_epoch=1)
    current = _dispatch(
        manifest,
        authority_gateway_id="gateway-current",
        authority_epoch=2,
    )

    old_key = room_attachments._batch_key(old)
    current_batch = spool.prepare(current, manifest)
    assert old_key != current_batch["batch_key"]

    with pytest.raises(room_attachments.RoomAttachmentSpoolError, match="unavailable"):
        spool.put(
            claims=_claims(old),
            task_id=current.task_id,
            execution_generation=current.execution_generation,
            attachment_id=str(manifest[0]["attachment_id"]),
            data=b"hello",
        )

    spool.put(
        claims=_claims(current),
        task_id=current.task_id,
        execution_generation=current.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    assert spool.require_complete(current) == manifest


def test_stale_grant_cleanup_preserves_current_epoch_batch_after_restart(
    tmp_path: Path,
):
    db = tmp_path / "state.db"
    root = tmp_path / "spool"
    spool = room_attachments.RoomAttachmentSpool(db, root=root)
    manifest = _manifest()
    old = _dispatch(manifest, authority_gateway_id="gateway-old", authority_epoch=1)
    current = _dispatch(
        manifest,
        authority_gateway_id="gateway-current",
        authority_epoch=2,
    )
    for dispatch in (old, current):
        spool.prepare(dispatch, manifest)
        spool.put(
            claims=_claims(dispatch),
            task_id=dispatch.task_id,
            execution_generation=dispatch.execution_generation,
            attachment_id=str(manifest[0]["attachment_id"]),
            data=b"hello",
        )

    assert spool.discard_scope(_claims(old)) == 1
    with pytest.raises(room_attachments.RoomAttachmentSpoolIncomplete):
        spool.require_complete(old)

    restarted = room_attachments.RoomAttachmentSpool(db, root=root)
    assert restarted.require_complete(current) == manifest
    assert Path(restarted.materialize(current)[0]["path"]).read_bytes() == b"hello"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_spool_rejects_conflict_incomplete_and_corrupt_batches(tmp_path: Path):
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db",
        root=tmp_path / "spool",
    )
    manifest = _manifest()
    dispatch = _dispatch(manifest)
    spool.prepare(dispatch, manifest)
    with pytest.raises(room_attachments.RoomAttachmentSpoolIncomplete):
        spool.require_complete(dispatch)
    claims = _claims(dispatch)
    with pytest.raises(room_attachments.RoomAttachmentSpoolConflict):
        spool.put(
            claims=claims,
            task_id=dispatch.task_id,
            execution_generation=dispatch.execution_generation,
            attachment_id=str(manifest[0]["attachment_id"]),
            data=b"wrong",
        )
    spool.put(
        claims=claims,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    next(path for path in spool.root.iterdir() if path.is_file()).write_bytes(b"bad")
    with pytest.raises(room_attachments.RoomAttachmentSpoolIncomplete):
        spool.require_complete(dispatch)
    repaired = spool.put(
        claims=claims,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    assert repaired == {
        "complete": True,
        "idempotent": True,
        "repaired": True,
    }
    assert spool.require_complete(dispatch) == manifest


def test_one_room_cannot_exhaust_the_target_attachment_spool(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(room_attachments, "MAX_ROOM_SPOOL_BYTES", 6)
    monkeypatch.setattr(room_attachments, "MAX_MEMBER_SPOOL_BYTES", 6)
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db",
        root=tmp_path / "spool",
    )
    first_data = b"one!"
    first_manifest = _manifest(first_data)
    first = _dispatch(first_manifest)
    claims = _claims(first)
    spool.prepare(first, first_manifest)
    spool.put(
        claims=claims,
        task_id=first.task_id,
        execution_generation=first.execution_generation,
        attachment_id=str(first_manifest[0]["attachment_id"]),
        data=first_data,
    )

    second_data = b"two!"
    second_manifest = _manifest(second_data)
    second_manifest[0]["attachment_id"] = "att_fedcba9876543210fedcba9876543210"
    second = _dispatch(
        second_manifest,
        task_id="task-2",
        attachment_manifest_digest=attachment_manifest_digest(second_manifest),
    )
    spool.prepare(second, second_manifest)
    with pytest.raises(room_attachments.RoomAttachmentSpoolError, match="quota"):
        spool.put(
            claims=claims,
            task_id=second.task_id,
            execution_generation=second.execution_generation,
            attachment_id=str(second_manifest[0]["attachment_id"]),
            data=second_data,
        )


def test_spool_expires_partial_or_stopped_attempt_data_after_restart(tmp_path: Path):
    now = [100.0]
    db = tmp_path / "state.db"
    root = tmp_path / "spool"
    manifest = _manifest()
    dispatch = _dispatch(manifest)
    claims = _claims(dispatch)
    spool = room_attachments.RoomAttachmentSpool(
        db,
        root=root,
        clock=lambda: now[0],
    )
    spool.prepare(dispatch, manifest)
    spool.put(
        claims=claims,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )
    now[0] += room_attachments.SPOOL_TTL_SECONDS + 1

    restarted = room_attachments.RoomAttachmentSpool(
        db,
        root=root,
        clock=lambda: now[0],
    )
    with pytest.raises(room_attachments.RoomAttachmentSpoolIncomplete):
        restarted.require_complete(dispatch)
    assert list(root.iterdir()) == []


def test_revoked_member_scope_removes_only_its_staged_batches(tmp_path: Path):
    spool = room_attachments.RoomAttachmentSpool(
        tmp_path / "state.db",
        root=tmp_path / "spool",
    )
    manifest = _manifest()
    dispatch = _dispatch(manifest)
    claims = _claims(dispatch)
    spool.prepare(dispatch, manifest)
    spool.put(
        claims=claims,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        attachment_id=str(manifest[0]["attachment_id"]),
        data=b"hello",
    )

    assert spool.discard_scope(claims) == 1
    with pytest.raises(room_attachments.RoomAttachmentSpoolIncomplete):
        spool.require_complete(dispatch)
    assert list(spool.root.iterdir()) == []
