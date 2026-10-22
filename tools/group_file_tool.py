"""``share_group_file``: hand one file to the current gateway Group Chat.

The file is staged privately for this exact turn; the room publishes it with the Bot's reply when
the turn completes, and never if the turn is stopped, fails or is superseded. Which paths may be
shared is main's own media-delivery policy (``validate_media_delivery_path``; remote terminal
backends through ``fetch_remote_media``), narrowed by main's read guard and by the secret and
private files of every Hermes home on this host (``_private``, ``_hard_linked``). ``bot_room``
toolset only; outside a Group Chat turn the tool refuses.
"""

from __future__ import annotations

import os
import posixpath
import re
import stat
from contextlib import suppress
from pathlib import Path
from typing import Callable

from tools.registry import registry, tool_error, tool_result

_UNAVAILABLE = "File sharing is available only during a Group Chat turn."
_UNSAFE_PATH = "That file cannot be shared. Share a file from your workspace or a Hermes media folder."
_PRIVATE_STORAGE = "hosted-room-attachments"
# Private trees of every Hermes home that a share never reads, whichever profile runs the turn:
# main's read-guarded directories (credential vault, MCP tokens, browser-profile copy) and its
# skill-hub cache, which the read guard covers for the active home and the root only, plus the
# Group Chat file store itself.
_PRIVATE_TREES = (("vault",), ("mcp-tokens",), ("browser-profile",), ("skills", ".hub"), (_PRIVATE_STORAGE,))
_REMOTE_COPY = re.compile(r"remote_[0-9a-f]{12}_(?P<name>.+)")


def _hermes_homes(turn) -> list[Path]:
    """Every Hermes home on this host, resolved: the active one, the root and each of its profiles
    (main's credential-denylist homes), plus the room owner's when it runs elsewhere."""
    from gateway.platforms.base import _credential_home_roots
    from hermes_constants import get_default_hermes_root, get_hermes_home
    root = get_default_hermes_root()
    homes = [get_hermes_home(), root, *_credential_home_roots(), Path(turn.db_path).parent]
    if turn.transport and turn.transport.get("source_home"):
        homes.append(Path(str(turn.transport["source_home"])))
    with suppress(OSError):
        homes.extend(path for path in (root / "profiles").iterdir() if path.is_dir())
    resolved: list[Path] = []
    for home in homes:
        with suppress(OSError, RuntimeError, ValueError):
            resolved.append(home.expanduser().resolve())
    return list(dict.fromkeys(resolved))


def _private(path: str, homes: list[Path]) -> bool:
    """Whether a host path, judged by its target, is a secret or private Hermes file: main's read
    guard, or a credential file or private tree of any Hermes home."""
    from agent.file_safety import _CREDENTIAL_FILE_NAMES, get_read_block_error
    if get_read_block_error(path):
        return True
    target = Path(path).resolve()
    if _PRIVATE_STORAGE in target.parts:
        return True
    for home in homes:
        with suppress(OSError, RuntimeError, ValueError):
            if any(target == (home / name).resolve() for name in _CREDENTIAL_FILE_NAMES) or any(
                    target.is_relative_to(home.joinpath(*tree).resolve()) for tree in _PRIVATE_TREES):
                return True
    return False


def _private_remote(path: str, copy: str) -> bool:
    """Whether a sandbox path is secret by the same rules, as far as a name can tell: the read
    guard's ``.env`` names, for the requested path and for the file it resolved to there (main's
    fetch keeps that name in the cache copy's), or a private tree inside a ``.hermes`` home."""
    from agent.file_safety import _BLOCKED_PROJECT_ENV_BASENAMES
    parts = [part.lower() for part in posixpath.normpath(path.replace("\\", "/")).split("/")]
    match = _REMOTE_COPY.fullmatch(Path(copy).name)
    names = {parts[-1], match.group("name").lower() if match else ""}
    inside = parts[parts.index(".hermes") + 1:] if ".hermes" in parts else []
    return bool(names & _BLOCKED_PROJECT_ENV_BASENAMES) or any(
        tuple(inside[i:i + len(tree)]) == tree for tree in _PRIVATE_TREES for i in range(len(inside)))


def _hard_linked(info: os.stat_result, homes: list[Path]) -> bool:
    """Whether an open file is a credential, private-tree or Group Chat store file of any Hermes
    home under another name (a hard link, which no path check sees)."""
    if info.st_nlink < 2:
        return False
    from agent.file_safety import _CREDENTIAL_FILE_NAMES
    from gateway.hosted_room_attachments import INCOMING_DIR, default_attachment_root

    def same(found: os.stat_result) -> bool:
        return (found.st_dev, found.st_ino) == (info.st_dev, info.st_ino)
    for home in homes:
        for name in _CREDENTIAL_FILE_NAMES:
            with suppress(OSError):
                if same(os.stat(home / name, follow_symlinks=False)):
                    return True
        store = default_attachment_root(home / "state.db")
        for folder in (*(home.joinpath(*tree) for tree in _PRIVATE_TREES), store / "blobs", store / INCOMING_DIR):
            with suppress(OSError):
                with os.scandir(folder) as entries:
                    if any(entry.inode() == info.st_ino and same(entry.stat(follow_symlinks=False))
                           for entry in entries):
                        return True
    return False


def _read_bounded(path: str, limit: int, refused: Callable[[os.stat_result], bool] = lambda info: False) -> bytes:
    """A regular file's bytes, opened without following a final symlink and fenced by size."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        info = os.fstat(descriptor)
        if refused(info):
            raise ValueError(_UNSAFE_PATH)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError(f"A shared file must be a non-empty file of at most {limit // 1_000_000} MB.")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) != info.st_size:
        raise ValueError("That file changed while it was being shared. Try again.")
    return data


def _requested_name(path: str) -> str:
    """The base name of the path as requested, for host and POSIX sandbox paths alike."""
    return os.path.basename(path.replace("\\", "/").rstrip("/"))


def _resolve(path: str) -> tuple[str, bool]:
    """``(host path, fetched copy)`` under the media-delivery policy, or a refusal."""
    from gateway.media_fetch import fetch_remote_media
    from gateway.platforms.base import validate_media_delivery_path
    from gateway.session_context import get_session_env
    safe = validate_media_delivery_path(path, session_key=get_session_env("HERMES_SESSION_KEY", ""))
    if safe is not None:
        return safe, False
    if _private_remote(path, ""):
        raise ValueError(_UNSAFE_PATH)
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
        homes = _hermes_homes(turn)
        resolved, fetched = _resolve(requested)
        try:
            # Secrets and private Hermes state are never shared, whatever name or link reaches them.
            if _private_remote(requested, resolved) if fetched else _private(resolved, homes):
                raise ValueError(_UNSAFE_PATH)
            data = _read_bounded(resolved, MAX_ATTACHMENT_BYTES, lambda info: _hard_linked(info, homes))
        finally:
            if fetched:
                Path(resolved).unlink(missing_ok=True)
        # The shared name is the one asked for, never the host path the bytes were read from
        # (a fetched remote file's cache copy, a resolved symlink): the same request is the same file.
        stored = stage(turn, name=(name or _requested_name(requested)).strip(), data=data)
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
