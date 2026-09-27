"""Files #98072: held-writer initialization without pre-admission housekeeping."""

import json
import sqlite3

import pytest

from gateway.hosted_room_attachments import (
    AttachmentError,
    HostedRoomAttachmentStore,
    default_attachment_root,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"held-writer-image"


def _manifest(item):
    return [{key: item[key] for key in ("attachment_id", "kind", "name", "size", "mime")}]


def test_import_initializer_requires_writer_before_creating_files_or_schema(tmp_path):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
        conn.execute("INSERT INTO unrelated VALUES ('retained')")
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(AttachmentError, match="writer transaction"):
            HostedRoomAttachmentStore.for_import_writer(conn, db, clock=lambda: 1)
        assert conn.execute("SELECT value FROM unrelated").fetchone()[0] == "retained"
    assert not default_attachment_root(db).exists()
    with sqlite3.connect(db) as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='hosted_room_attachments'"
        ).fetchone()


def test_import_initializer_uses_only_admitted_connection_and_rolls_back_ddl(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
    def forbidden(*args, **kwargs):
        raise AssertionError("held-writer initialization opened a second connection or ran startup housekeeping")
    with monkeypatch.context() as patch:
        patch.setattr(HostedRoomAttachmentStore, "_connect", forbidden)
        patch.setattr(HostedRoomAttachmentStore, "reconcile_room_events", forbidden)
        patch.setattr(HostedRoomAttachmentStore, "prune", forbidden)
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            store = HostedRoomAttachmentStore.for_import_writer(conn, db, clock=lambda: 1)
            assert store.db_path == db
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_attachments'").fetchone()
            conn.rollback()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("unrelated",)
        ]


def test_unpublished_output_cleanup_never_revokes_durable_publication(tmp_path):
    db = tmp_path / "state.db"
    store = HostedRoomAttachmentStore(db, clock=lambda: 1)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        staged = store.put_import(conn, room_id="room-1", upload_id="upload-1",
                                  kind="image", name="result.png", mime="image/png", data=PNG)
        manifest = _manifest(staged)
        store.commit_import_message(conn, room_id="room-1", event_id="event-1",
                                    manifest=manifest, recipient_member_ids=["reader"])
        conn.execute("CREATE TABLE hosted_room_events (room_id TEXT, event_id TEXT, kind TEXT, payload_json TEXT)")
        conn.commit()
    assert store.abort_unpublished_event(room_id="room-1", event_id="event-1") is True
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT state,event_id,viewer_access FROM hosted_room_attachments").fetchone() == (
            "uploaded", None, 0
        )
        conn.execute("UPDATE hosted_room_attachments SET state='committed', event_id='event-1', viewer_access=1")
        conn.execute("INSERT INTO hosted_room_events VALUES (?,?,?,?)",
                     ("room-1", "event-1", "message.member", json.dumps({"attachments": manifest})))
    assert store.abort_unpublished_event(room_id="room-1", event_id="event-1") is False
    assert store.read(room_id="room-1", attachment_id=staged["attachment_id"],
                      event_id="event-1", recipient_member_id="reader", viewer=True).data == PNG


def test_held_writer_commits_real_import_and_retained_bytes_with_normal_startup(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    # The normal constructor remains a startup reconciler/pruner; an unrelated
    # expired upload makes accidental housekeeping during the held writer visible.
    ordinary = HostedRoomAttachmentStore(db, clock=lambda: 1)
    old = ordinary.put(room_id="other", upload_id="old", kind="image", name="old.png",
                       mime="image/png", data=PNG + b"old")
    calls = []
    original_prune = HostedRoomAttachmentStore.prune
    def observed_prune(self):
        calls.append(self)
        return original_prune(self)
    monkeypatch.setattr(HostedRoomAttachmentStore, "prune", observed_prune)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        store = HostedRoomAttachmentStore.for_import_writer(conn, db, clock=lambda: 10000)
        assert calls == []
        staged = store.put_import(conn, room_id="room-1", upload_id="import-1",
                                  kind="image", name="history.png", mime="image/png", data=PNG)
        manifest = _manifest(staged)
        assert store.commit_import_message(conn, room_id="room-1", event_id="event-1",
                                           manifest=manifest, recipient_member_ids=["reader"]) == manifest
        conn.execute("CREATE TABLE hosted_room_events (room_id TEXT, event_id TEXT, kind TEXT, payload_json TEXT)")
        conn.execute("INSERT INTO hosted_room_events VALUES (?,?,?,?)",
                     ("room-1", "event-1", "history.imported", json.dumps({"attachments": manifest})))
        conn.commit()
    # Ordinary construction still performs its startup housekeeping; use a
    # pre-expiry clock so the witness survives and the published import is read.
    reopened = HostedRoomAttachmentStore(db, clock=lambda: 1)
    assert calls == [reopened]
    assert reopened.find_upload(room_id="other", upload_id="old")["attachment_id"] == old["attachment_id"]
    result = reopened.read(room_id="room-1", attachment_id=staged["attachment_id"],
                           event_id="event-1", recipient_member_id="reader", viewer=True)
    assert result.data == PNG
    assert result.attachment["attachment_id"] == staged["attachment_id"]
