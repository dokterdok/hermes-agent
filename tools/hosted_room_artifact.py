"""Owner-local explicit Group Chat sharing, ported from #99159."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path, PurePosixPath

from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactScope, open_room_artifact_path, validate_open_room_artifact_path
from gateway.session_hosted_output import current_output_binding
from tools.registry import registry

logger = logging.getLogger(__name__)


_PRIVATE_ROOM_STORAGE_NAMES = frozenset({
    "hosted-room-artifact-outbox",
    "hosted-room-attachments",
    "roomlink-attachment-spool",
})


SHARE_GROUP_FILE_SCHEMA = {
    "name": "share_group_file",
    "description": (
        "Share one local file with the current Group Chat. The file is copied "
        "into private room storage and becomes available to the user and the "
        "other Bots in this Group Chat. Never use it for credentials or secrets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path of the file to share.",
            },
            "name": {
                "type": "string",
                "description": "Optional filename shown in the Group Chat.",
            },
        },
        "required": ["path"],
    },
}


def _requested_file_path(value: str) -> Path:
    candidate = str(value or "").strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "`\"'":
        candidate = candidate[1:-1].strip()
    candidate = candidate.lstrip("`\"'").rstrip("`\"',.;:)}]")
    return Path(candidate).expanduser()


def _canonical_macos_alias_path(path: Path) -> Path:
    """Rewrite only macOS' OS-owned root aliases before no-follow open."""

    if sys.platform != "darwin":
        return path
    normalized = os.path.normpath(str(path))
    for alias, target in (("/tmp", "/private/tmp"), ("/var", "/private/var")):
        if normalized == alias or normalized.startswith(alias + "/"):
            return Path(target + normalized[len(alias):])
    return path


def _portable_path_parts(path: Path | str) -> tuple[str, ...]:
    normalized = str(path).replace("\\", "/")
    return tuple(part.casefold() for part in PurePosixPath(normalized).parts)


def _is_private_room_storage_path(path: Path | str) -> bool:
    return any(part in _PRIVATE_ROOM_STORAGE_NAMES for part in _portable_path_parts(path))


def _store_open_group_file(
    *,
    scope: RoomArtifactScope,
    path: Path,
    descriptor: int,
    session_key: str,
    name: str | None,
) -> dict[str, object]:
    from agent.file_safety import get_read_block_error
    from gateway.platforms.base import validate_media_delivery_path
    from hermes_constants import get_default_hermes_root, get_hermes_home

    safe_path = validate_media_delivery_path(str(path), session_key=session_key)
    if safe_path is None:
        raise RoomArtifactError(
            "That file cannot be shared. Move it to the workspace or a Hermes media folder and try again."
        )
    resolved = Path(safe_path)
    binding = current_output_binding()
    if binding is None or binding.scope != scope:
        raise RoomArtifactError("Group Chat file sharing requires a live owner admission.")
    outbox = binding.outbox()
    if _is_private_room_storage_path(resolved):
        raise RoomArtifactError("Private Group Chat storage cannot be shared.")
    active_home = Path(get_hermes_home()).resolve(strict=False)
    profiles_root = Path(get_default_hermes_root()).resolve(strict=False) / "profiles"
    try:
        resolved.relative_to(profiles_root)
    except ValueError:
        pass
    else:
        try:
            resolved.relative_to(active_home)
        except ValueError as exc:
            raise RoomArtifactError(
                "Files owned by another Hermes profile cannot be shared."
            ) from exc
        if active_home == profiles_root.parent:
            raise RoomArtifactError(
                "Files owned by another Hermes profile cannot be shared."
            )
    if get_read_block_error(str(resolved)):
        raise RoomArtifactError(
            "Hermes credential and internal state files cannot be shared."
        )
    resolved = validate_open_room_artifact_path(resolved, descriptor)
    return outbox.put_open_file(
        scope=scope,
        descriptor=descriptor,
        source_name=resolved.name,
        name=name,
    )


def share_group_file(
    path: str,
    *,
    name: str | None = None,
    task_id: str = "default",
) -> str:
    """Copy a safe file into the current hosted-room output outbox."""

    binding = current_output_binding()
    scope = binding.scope if binding is not None else None
    if scope is None:
        return json.dumps({
            "ok": False,
            "error": "File sharing requires an active owner-local Group Chat turn; this execution scope is unsupported.",
        })
    try:
        from gateway.platforms.base import validate_media_delivery_path
        from gateway.session_context import get_session_env
        from tools.file_tools_paths import _terminal_env_type_for_task

        requested = _requested_file_path(path)
        if not requested.is_absolute():
            raise RoomArtifactError(
                "That file cannot be shared. Move it to the workspace or a Hermes media folder and try again."
            )
        if _terminal_env_type_for_task(task_id) != "local":
            raise RoomArtifactError("Group Chat output from this execution environment is not supported yet.")
        session_key = get_session_env("HERMES_SESSION_KEY", "")
        safe_path = validate_media_delivery_path(
            str(requested),
            session_key=session_key,
        )
        if safe_path is None:
            raise RoomArtifactError(
                "That file cannot be shared. Move it to the workspace or a Hermes media folder and try again."
            )
        open_candidate = _canonical_macos_alias_path(requested)
        with open_room_artifact_path(open_candidate) as (opened_path, descriptor):
            stored = _store_open_group_file(
                scope=scope,
                path=opened_path,
                descriptor=descriptor,
                session_key=session_key,
                name=name,
            )
        return json.dumps({
            "ok": True,
            "artifact_id": stored["artifact_id"],
            "name": stored["name"],
            "size": stored["size"],
            "sha256": stored["sha256"],
            "message": f"{stored['name']} will be shared with this Group Chat when your turn completes.",
        })
    except RoomArtifactError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    except (OSError, RuntimeError, ValueError):
        logger.warning("Group Chat file sharing failed", exc_info=True)
        return json.dumps({
            "ok": False,
            "error": "That file could not be shared. Check the file and try again.",
        })


def _handle_share_group_file(args, **kwargs):
    return share_group_file(
        args.get("path", ""),
        name=args.get("name"),
        task_id=kwargs.get("task_id") or "default",
    )


registry.register(
    name="share_group_file",
    toolset="bot_room",
    schema=SHARE_GROUP_FILE_SCHEMA,
    handler=_handle_share_group_file,
    
)
