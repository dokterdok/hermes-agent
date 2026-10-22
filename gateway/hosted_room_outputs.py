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
    OUTPUT_UPLOAD_PREFIX,
    AttachmentConflictError,
    AttachmentError,
    HostedRoomAttachmentStore,
)

_MEDIA_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
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


def task_prefix(task_id: str) -> str:
    """The upload-id prefix of every attempt's shared files for one task."""
    return f"{OUTPUT_UPLOAD_PREFIX}{hashlib.sha256(task_id.encode()).hexdigest()[:32]}:"


def output_prefix(task_id: str, execution_generation: int) -> str:
    """The upload-id prefix of one attempt's shared files."""
    return f"{task_prefix(task_id)}{execution_generation}:"


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
                             data=data, output=True)
        except AttachmentConflictError as exc:
            raise GroupFileError(f"A different file named {name} was already shared in this turn.") from exc
        except AttachmentError as exc:
            raise GroupFileError(str(exc)) from exc


def manifest(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: item[key] for key in ("attachment_id", "kind", "name", "size", "mime")} for item in items]


def _message_event_id(task_id: str) -> str:
    return "dmessage:" + task_id.removeprefix("dtask:")


def bind_outputs(service, room: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a settled attempt's shared files to its member message, before its publication is
    planned; returns the result fields that publication reads: ``attachments`` (the message's
    manifest) and ``unshared`` (names of staged files the message cannot carry).

    Each staged file is committed to the message here, so publishing never reads a byte: a file
    whose bytes are gone or changed is named instead and stays an unpublished upload. Once the
    message is in the room log (only its terminal event is missing), the logged manifest is the
    outcome, so finishing that publication repeats it exactly."""
    identity = task["identity"]
    prefix = output_prefix(identity.task_id, int(task["execution_generation"]))
    message_id = _message_event_id(identity.task_id)
    store = HostedRoomAttachmentStore(service.db_path)
    staged = manifest(store.staged_uploads(room_id=identity.room_id, upload_prefix=prefix, event_id=message_id))
    kept = store.logged_manifest(room_id=identity.room_id, event_id=message_id)
    if kept is not None:
        logged = {entry["attachment_id"] for entry in kept}
        unshared = [item["name"] for item in staged if item["attachment_id"] not in logged]
    else:
        author = task["payload"]["target_member_id"]
        recipients = [member["member_id"] for member in room["members"] if member["member_id"] != author]
        kept, unshared = [], []
        for item in staged:
            try:
                store.commit_message(room_id=identity.room_id, event_id=message_id, manifest=[item],
                                     recipient_member_ids=recipients, viewer_access=True)
            except AttachmentError:
                unshared.append(item["name"])
            else:
                kept.append(item)
    return {**({"attachments": kept} if kept else {}), **({"unshared": unshared} if unshared else {})}


def release_outputs(service, room: Mapping[str, Any], task: Mapping[str, Any]) -> None:
    """After a terminal task's outcome is in the room log, end the hold on every file of the task
    that outcome does not carry.

    A settled reply's files are its message's (``bind_outputs``), retained by the event append.
    Every other file of the task (a failed, stopped or superseded attempt, an earlier attempt of a
    retried task, a file the reply could not carry) loses its hold and is reclaimed like an
    abandoned upload. Until this runs, nothing expires a held file, so a publication that has to
    be retried finds the same files."""
    store = HostedRoomAttachmentStore(service.db_path)
    store.release_staged(room_id=room["room_id"], upload_prefix=task_prefix(task["identity"].task_id))
