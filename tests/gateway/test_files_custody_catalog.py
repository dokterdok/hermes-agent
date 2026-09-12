"""Catalog invariants over the canonical append/retention boundary."""

import pytest

from gateway import hosted_rooms
from gateway.hosted_room_attachments import AttachmentError, HostedRoomAttachmentStore


def room_store(tmp_path):
    db = tmp_path / "state.db"
    hosted_rooms.create_room(
        db, room_id="room-1", name="Files", authority_gateway_id="home",
        members=[{"member_id": "writer", "profile": "writer", "handle": "writer"}],
    )
    return db, HostedRoomAttachmentStore(db)


def publish(db, store, version, *, viewer=True):
    saved = store.put(
        room_id="room-1", upload_id=f"upload-{version}", name="report.txt",
        kind="file", mime="text/plain", data=f"version {version}".encode(),
    )
    manifest = [{k: saved[k] for k in ("attachment_id", "kind", "name", "size", "mime")}]
    event = f"message-{version}"
    store.commit_message(
        room_id="room-1", event_id=event, manifest=manifest,
        recipient_member_ids=["writer"], viewer_access=viewer, hold_until_event=True,
    )
    hosted_rooms.append_event(
        db, room_id="room-1", event_id=event, kind="message.user",
        actor={"kind": "user", "id": "owner"},
        payload={"text": "Shared report", "attachments": manifest},
        authority_gateway_id="home", authority_epoch=1,
    )
    return saved, event


def test_catalog_versions_reopen_cursor_and_exact_download(tmp_path):
    db, store = room_store(tmp_path)
    first, first_event = publish(db, store, 1)
    second, _ = publish(db, store, 2)
    request = dict(room_id="room-1", authority_gateway_id="home", authority_epoch=1, limit=1)
    page = store.list_published(**request)
    assert page["items"][0]["attachment_id"] == second["attachment_id"]
    publish(db, store, 3)
    reopened = HostedRoomAttachmentStore(db)
    older = reopened.list_published(**request, cursor=page["next_cursor"])
    assert [x["attachment_id"] for x in older["items"]] == [first["attachment_id"]]
    saved = reopened.read_viewer(
        room_id="room-1", authority_gateway_id="home", authority_epoch=1,
        event_id=first_event, attachment_id=first["attachment_id"],
    )
    assert saved.data == b"version 1"
    with pytest.raises(AttachmentError):
        reopened.read_viewer(
            room_id="room-1", authority_gateway_id="home", authority_epoch=1,
            event_id=first_event, attachment_id=second["attachment_id"],
        )


def test_catalog_never_promotes_private_uploads_or_reuses_foreign_cursor(tmp_path):
    db, store = room_store(tmp_path)
    publish(db, store, 1)
    publish(db, store, 2)
    with pytest.raises(hosted_rooms.EventAttachmentConflictError):
        publish(db, store, "private", viewer=False)
    store.put(room_id="room-1", upload_id="unshared", name="report.txt",
              kind="file", mime="text/plain", data=b"never shared")
    request = dict(room_id="room-1", authority_gateway_id="home", authority_epoch=1)
    page = store.list_published(**request, limit=1)
    with pytest.raises(AttachmentError):
        store.list_published(**request, cursor=page["next_cursor"], query="another")
    with pytest.raises(AttachmentError):
        store.list_published(**{**request, "authority_epoch": 2})
    assert len(store.list_published(**request)["items"]) == 2
