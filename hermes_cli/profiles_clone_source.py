"""The source generation a profile clone pins, and the check that it survived the copy.

A clone never holds its source's name across the copy (every cold SessionDB open of the source
takes that lease, and a ``--clone-all`` copy is unbounded): it pins the source's generation before
copying and re-checks it before publishing.
"""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Optional

from hermes_cli.profile_incarnation import ensure_profile_incarnation
from hermes_constants import named_profile_home_is_unavailable, profile_deletion_marker_path

Generation = tuple[int, int, Optional[str]]


def _clone_source_generation(source_dir: Path) -> Optional[Generation]:
    """Pin the generation a clone copies: the source's directory identity and incarnation. ``None``
    for a default/custom source, which is not a reusable lifecycle object.

    A legacy source gets its marker under a short lease, as any config load of it would (the clone's
    channel strip loads it); a source this user cannot write (a read-only template) is pinned as it
    stands, and nothing is written into it.
    """
    from hermes_cli.profiles import _profile_directory_identity

    if profile_deletion_marker_path(source_dir) is None:
        return None
    try:
        ensure_profile_incarnation(source_dir)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise
    return _profile_directory_identity(source_dir)


def _assert_clone_source_unchanged(source_dir: Path, generation: Generation) -> None:
    """Refuse to publish a clone whose copy overlapped a delete, rename or replacement of its source.

    Lock-free: only the target name is leased here (taking the source now could invert the name
    order). Delete and rename tombstone the source before removing or moving anything, and only a
    rollback that removed nothing or a published generation lifts it; so checking availability
    first and the pinned generation second proves nothing of it went away mid-copy.
    """
    from hermes_cli.profiles import _profile_directory_identity

    try:
        unchanged = (not named_profile_home_is_unavailable(source_dir)
                     and _profile_directory_identity(source_dir) == generation)
    except (OSError, RuntimeError):
        unchanged = False
    if not unchanged:
        raise FileNotFoundError(
            f"Source profile '{source_dir.name}' was deleted, renamed or replaced while it was "
            "being cloned; retry the clone."
        )
