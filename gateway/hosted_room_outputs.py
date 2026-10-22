"""A Bot's shared files in a gateway Group Chat: staged during its turn, published with its reply.

``share_group_file`` stages each file as a private, uncommitted upload in the attachment store of
the profile that runs the turn, under an upload id derived from the exact attempt
(task id + execution generation). The room owner publishes the files of a *settled* attempt with
its one member message, exactly like a user message's files: committed, then retained by the
event append. Nothing else ever commits them, so a stopped, failed, superseded or retried
attempt's files stay private and the store's uncommitted-upload expiry reclaims them.
"""

from __future__ import annotations

import hashlib
import mimetypes
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Any, Iterator, Mapping, Sequence

from gateway.hosted_room_attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_MESSAGE_ATTACHMENT_BYTES,
    AttachmentConflictError,
    AttachmentError,
    HostedRoomAttachmentStore,
)

_MEDIA_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
OUTPUT_UPLOAD_PREFIX = "out:"
# An attempt stages only on the profile process that runs it; serializing the per-attempt limit
# check with the put keeps concurrent tool calls of one turn inside one message's limits.
_STAGE_LOCK = threading.Lock()


class GroupFileError(ValueError):
    """A file the current turn cannot share; the message is safe to show the Bot."""


@dataclass(frozen=True)
class HostedTurn:
    """The exact hosted attempt executing on this profile, and the store its files stage in."""

    room_id: str
    task_id: str
    execution_generation: int
    db_path: Path


_TURN: ContextVar[HostedTurn | None] = ContextVar("hosted_room_turn", default=None)


def current_turn() -> HostedTurn | None:
    return _TURN.get()


def hosted_attempt(request_id: Any) -> tuple[str, str, int] | None:
    """``(room_id, task_id, execution_generation)`` of a hosted admission's request id, else None."""
    if not isinstance(request_id, str) or not request_id.startswith("hosted:"):
        return None
    try:
        task, generation = json.loads(request_id[len("hosted:"):])
        room_id, task_id = task["room_id"], task["task_id"]
    except (TypeError, ValueError, KeyError):
        return None
    if (not isinstance(room_id, str) or not room_id or not isinstance(task_id, str) or not task_id
            or type(generation) is not int or generation < 1):
        return None
    return room_id, task_id, generation


def hosted_turn_for(authority, ref, row) -> HostedTurn | None:
    """The hosted attempt an admission executes on this profile, if its files can be published.

    A member on another profile of this host is served through the owner transport; its files
    would stage in a store the room owner does not read, so it is not bound."""
    attempt = hosted_attempt(row["request_id"])
    if attempt is None:
        return None
    from gateway.session_hosted_transport import _BINDING
    with authority.db._read_ctx() as conn:
        if conn.execute("SELECT 1 FROM state_meta WHERE key=?", (_BINDING + ref.session_id,)).fetchone():
            return None
    return HostedTurn(*attempt, Path(authority.db.db_path))


@contextmanager
def hosted_turn_scope(turn: HostedTurn | None) -> Iterator[HostedTurn | None]:
    """Expose the hosted attempt to this execution only (tools see it through copied contexts)."""
    token = _TURN.set(turn)
    try:
        yield turn
    finally:
        _TURN.reset(token)


def output_prefix(task_id: str, execution_generation: int) -> str:
    """The upload-id prefix of one attempt's shared files."""
    digest = hashlib.sha256(f"{task_id}\0{execution_generation}".encode()).hexdigest()[:40]
    return f"{OUTPUT_UPLOAD_PREFIX}{digest}:"


def _upload_id(turn: HostedTurn, name: str) -> str:
    return output_prefix(turn.task_id, turn.execution_generation) + hashlib.sha256(
        name.encode("utf-8")).hexdigest()[:16]


def classify(name: str, data: bytes) -> tuple[str, str]:
    """Kind and MIME by content, exactly as the room's attachment store validates them."""
    from gateway.hosted_room_attachments import _sniff_known_mime, _validate_mime_and_kind
    sniffed = _sniff_known_mime(data)
    guessed = (mimetypes.guess_type(name)[0] or "application/octet-stream").lower()
    if sniffed == "application/pdf":
        kind, mime = "pdf", sniffed
    elif sniffed in _MEDIA_IMAGES or (sniffed == "image/bmp" and guessed == "image/bmp"):
        kind, mime = "image", sniffed
    elif guessed.startswith("image/") or guessed == "application/pdf":
        raise GroupFileError("That file's contents do not match its type.")
    else:
        kind, mime = "file", guessed
        if mime.startswith("text/") and sniffed != "text/plain":
            mime = "application/octet-stream"  # not clean UTF-8: opaque bytes, never text
    try:
        _validate_mime_and_kind(data, kind=kind, mime=mime)
    except AttachmentError as exc:
        raise GroupFileError(str(exc)) from exc
    return kind, mime


def stage(turn: HostedTurn, *, name: str, data: bytes) -> dict[str, Any]:
    """Stage one file for this attempt; the same name and bytes again is the same file."""
    kind, mime = classify(name, data)
    store = HostedRoomAttachmentStore(turn.db_path)
    prefix = output_prefix(turn.task_id, turn.execution_generation)
    upload_id = _upload_id(turn, name)
    with _STAGE_LOCK:
        staged = store.staged_uploads(room_id=turn.room_id, upload_prefix=prefix)
        if not any(item["upload_id"] == upload_id for item in staged):
            if len(staged) >= MAX_ATTACHMENTS_PER_MESSAGE:
                raise GroupFileError(f"A turn can share at most {MAX_ATTACHMENTS_PER_MESSAGE} files.")
            if sum(item["size"] for item in staged) + len(data) > MAX_MESSAGE_ATTACHMENT_BYTES:
                raise GroupFileError(
                    f"A turn can share at most {MAX_MESSAGE_ATTACHMENT_BYTES // 1_000_000} MB of files.")
        try:
            return store.put(room_id=turn.room_id, upload_id=upload_id, kind=kind, name=name, mime=mime,
                             data=data)
        except AttachmentConflictError as exc:
            raise GroupFileError(f"A different file named {name} was already shared in this turn.") from exc
        except AttachmentError as exc:
            raise GroupFileError(str(exc)) from exc


def manifest(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: item[key] for key in ("attachment_id", "kind", "name", "size", "mime")} for item in items]


def _message_event_id(task_id: str) -> str:
    return "dmessage:" + task_id.removeprefix("dtask:")


def attempt_outputs(service, room: Mapping[str, Any], task: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The manifest of the files a settled attempt shared, as uploads in the room owner's store."""
    identity = task["identity"]
    prefix = output_prefix(identity.task_id, int(task["execution_generation"]))
    store = HostedRoomAttachmentStore(service.db_path)
    return manifest(store.staged_uploads(
        room_id=identity.room_id, upload_prefix=prefix, event_id=_message_event_id(identity.task_id)))


def commit_outputs(service, room: Mapping[str, Any], events: Sequence[Any]) -> None:
    """Commit the planned member message's files to it, visible to viewers and the other members.

    The append that follows retains them; a reply that is never appended (superseded, or a
    failed append retried later) leaves only a bounded hold the store's expiry reclaims."""
    message = next((event for event in events
                    if event.kind == "message.member" and event.payload.get("attachments")), None)
    if message is None:
        return
    author = message.payload["member_id"]
    HostedRoomAttachmentStore(service.db_path).commit_message(
        room_id=room["room_id"], event_id=message.event_id, manifest=message.payload["attachments"],
        recipient_member_ids=[m["member_id"] for m in room["members"] if m["member_id"] != author],
        viewer_access=True, hold_until_event=True)
