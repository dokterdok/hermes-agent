"""Shipped history that carries attachments must use the Files import writer.

The store methods live on Files tip b353ab32. This fixture is red until that
tip's attachment-store blobs are checked out beside this Runtime tree.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3

import pytest

from gateway import hosted_rooms as rooms
from gateway.hosted_room_attachments import (
    AttachmentError,
    AttachmentNotFoundError,
    HostedRoomAttachmentStore,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"image-payload"
NOW = 1_800_000_000


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _group(*, image: bytes = PNG, mime: str = "image/png") -> dict:
    return {
        "room_id": "release-room-files",
        "name": "Release",
        "source_id": "hermes.plugin.hermes-bots.group-chats:release-room-files",
        "members": [
            {
                "source_member_id": "local-sentinel",
                "name": "Sentinel",
                "profile": "default",
                "handle": "sentinel",
                "connection_id": "desktop-local",
                "remote_source": False,
            },
        ],
        "history": [
            {
                "source_entry_id": "shipped-history-1",
                "at_ms": 1_700_000_000_000,
                "author_kind": "user",
                "author_name": "You",
                "text": "Keep this shipped file",
                "thread_id": "thread-1",
                "attachments": [
                    {"kind": "image", "name": "history.png", "data": _data_url(image, mime)},
                ],
            },
        ],
        "held_work": [],
    }


def _import(db, group: dict | None = None, **overrides):
    payload = _group() if group is None else group
    params = dict(
        local_profiles=("default",),
        authority_gateway_id="gateway-a",
        now=NOW,
    )
    params.update(overrides)
    return rooms.import_shipped_group_history(db, **payload, **params)


def _published(db):
    page = rooms.read_events(db, room_id="release-room-files", since_seq=0, limit=20)
    event = page["events"][0]
    return event, event["payload"]["attachments"][0]


def test_import_commits_history_bytes_for_members_and_viewers_only(tmp_path):
    db = tmp_path / "shared-state.db"
    result = _import(db)
    repeated = _import(db)

    assert result["idempotent"] is False
    assert repeated["idempotent"] is True
    assert result["imported_history"] == 1
    event, manifest = _published(db)
    assert event["kind"] == "history.imported"
    assert manifest["kind"] == "image"
    assert manifest["name"] == "history.png"
    assert manifest["mime"] == "image/png"
    assert manifest["size"] == len(PNG)
    member_id = result["room"]["members"][0]["member_id"]

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT state, viewer_access, event_id, sha256 FROM hosted_room_attachments"
        ).fetchone()
    assert row["state"] == "committed"
    assert row["viewer_access"] == 1
    assert row["event_id"] == event["event_id"]
    assert row["sha256"] == hashlib.sha256(PNG).hexdigest()

    store = HostedRoomAttachmentStore(db, clock=lambda: NOW)
    assert store.stats()["attachments"] == 1
    assert store.stats()["blobs"] == 1
    assert store.read(
        room_id="release-room-files",
        attachment_id=manifest["attachment_id"],
        recipient_member_id=member_id,
        event_id=event["event_id"],
    ).data == PNG
    with pytest.raises(AttachmentNotFoundError):
        store.read(
            room_id="release-room-files",
            attachment_id=manifest["attachment_id"],
            recipient_member_id="foreign-member",
            event_id=event["event_id"],
        )
    with pytest.raises(AttachmentNotFoundError):
        store.read(
            room_id="release-room-files",
            attachment_id=manifest["attachment_id"],
            recipient_member_id=member_id,
            event_id="history:not-this-event",
        )
    assert store.read_viewer(
        room_id="release-room-files",
        attachment_id=manifest["attachment_id"],
        event_id=event["event_id"],
        authority_gateway_id=result["room"]["authority_gateway_id"],
        authority_epoch=result["room"]["authority_epoch"],
    ).data == PNG

    changed = _group(image=PNG + b"changed")
    with pytest.raises(rooms.RoomConflictError, match="different import content"):
        _import(db, changed)
    assert store.read(
        room_id="release-room-files",
        attachment_id=manifest["attachment_id"],
        recipient_member_id=member_id,
        event_id=event["event_id"],
    ).data == PNG


def test_import_rejects_mime_mismatch_without_leaving_a_room_or_blob(tmp_path):
    db = tmp_path / "shared-state.db"
    with pytest.raises(AttachmentError, match="MIME|mime|kind|bytes"):
        _import(db, _group(image=b"not a png"))
    assert rooms.list_rooms(db) == []
    blobs = db.parent / "hosted-room-attachments" / "blobs"
    assert not blobs.exists() or list(blobs.iterdir()) == []
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "hosted_room_attachments" in tables:
            assert conn.execute("SELECT COUNT(*) FROM hosted_room_attachments").fetchone() == (0,)
        if "hosted_room_attachment_blobs" in tables:
            assert conn.execute("SELECT COUNT(*) FROM hosted_room_attachment_blobs").fetchone() == (0,)


def test_import_interruption_rolls_back_custody_and_recovers_orphan_blobs(tmp_path, monkeypatch):
    db = tmp_path / "shared-state.db"
    written: list = []
    original = HostedRoomAttachmentStore._write_blob

    def record_write(self, target, data):
        written.append(target)
        return original(self, target, data)

    monkeypatch.setattr(HostedRoomAttachmentStore, "_write_blob", record_write)
    monkeypatch.setattr(
        rooms,
        "_history_import_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(InterruptedError("simulated interruption")),
    )
    with pytest.raises(InterruptedError, match="simulated interruption"):
        _import(db)
    assert written
    assert all(not path.exists() for path in written)
    assert rooms.list_rooms(db) == []
    blobs = db.parent / "hosted-room-attachments" / "blobs"
    assert blobs.is_dir()
    assert list(blobs.iterdir()) == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM hosted_room_attachments").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM hosted_room_attachment_blobs").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM hosted_room_history_imports").fetchone() == (0,)
