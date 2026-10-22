"""``share_group_file``: hand one file to the current gateway Group Chat.

The file is staged privately for this exact turn; the room publishes it with the Bot's reply when
the turn completes, and never if the turn is stopped, fails or is superseded. Which paths may be
shared is main's own media-delivery policy (``validate_media_delivery_path``; remote terminal
backends through ``fetch_remote_media``). ``bot_room`` toolset only; outside a Group Chat turn the
tool refuses.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tools.registry import registry, tool_error, tool_result

_UNAVAILABLE = "File sharing is available only during a Group Chat turn."
_UNSAFE_PATH = "That file cannot be shared. Share a file from your workspace or a Hermes media folder."
_PRIVATE_STORAGE = "hosted-room-attachments"


def _read_bounded(path: str, limit: int) -> bytes:
    """A regular file's bytes, opened without following a final symlink and fenced by size."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError(f"A shared file must be a non-empty file of at most {limit // 1_000_000} MB.")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) != info.st_size:
        raise ValueError("That file changed while it was being shared. Try again.")
    return data


def _resolve(path: str) -> tuple[str, bool]:
    """``(host path, fetched copy)`` under the media-delivery policy, or a refusal."""
    from gateway.media_fetch import fetch_remote_media
    from gateway.platforms.base import validate_media_delivery_path
    from gateway.session_context import get_session_env
    safe = validate_media_delivery_path(path, session_key=get_session_env("HERMES_SESSION_KEY", ""))
    if safe is not None:
        return safe, False
    fetched = fetch_remote_media(path)
    if fetched is None:
        raise ValueError(_UNSAFE_PATH)
    return fetched, True


def share_group_file(path: str, name: str | None = None) -> str:
    from gateway.hosted_room_attachments import MAX_ATTACHMENT_BYTES
    from gateway.hosted_room_outputs import GroupFileError, current_turn, stage
    turn = current_turn()
    if turn is None:
        return tool_error(_UNAVAILABLE)
    requested = str(path or "").strip()
    if not requested or not os.path.isabs(os.path.expanduser(requested)):
        return tool_error("Give the absolute path of the file to share.")
    try:
        resolved, fetched = _resolve(requested)
        try:
            # The room's own private file store is never re-shared by path.
            if _PRIVATE_STORAGE in Path(resolved).parts:
                raise ValueError(_UNSAFE_PATH)
            data = _read_bounded(resolved, MAX_ATTACHMENT_BYTES)
        finally:
            if fetched:
                Path(resolved).unlink(missing_ok=True)
        stored = stage(turn, name=(name or Path(resolved).name).strip(), data=data)
    except (GroupFileError, ValueError) as exc:
        return tool_error(str(exc))
    except OSError:
        return tool_error("That file could not be read. Check the path and try again.")
    return tool_result(shared=True, name=stored["name"], size=stored["size"], sha256=stored["sha256"],
                       message=f"{stored['name']} will be shared with this Group Chat when your turn completes.")


SHARE_GROUP_FILE_SCHEMA = {
    "name": "share_group_file",
    "description": (
        "Share one file with the current Group Chat. It is published with your reply when your turn "
        "completes, and the user and the other Bots can open that exact version; it is never published "
        "if your turn is stopped or fails. Never share credentials or secrets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path of the file to share."},
            "name": {"type": "string", "description": "Optional file name shown in the Group Chat."},
        },
        "required": ["path"],
    },
}


registry.register(
    name="share_group_file",
    toolset="bot_room",
    schema=SHARE_GROUP_FILE_SCHEMA,
    handler=lambda args, **kw: share_group_file(args.get("path", ""), name=args.get("name")),
    emoji="📎",
)
