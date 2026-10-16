"""Title-keyed local session selection, resolved read-only by the authority.

``hermes chat --resume <title>`` / ``-c <title> [--create-if-missing]`` used to open
SessionDB in the client; the zero-writer client now sends the name and the owner
applies the classic CLI rule (``hermes_cli.main._resolve_session_by_name_or_id``).
"""
from __future__ import annotations

from hermes_state_runtime import RuntimeStoreError


def resolve_titled_session(authority, actor, name, *, missing_ok=False):
    """Exact session id first, then title (latest ``"<title> #N"`` continuation wins over
    the bare title), projected forward to the compression tip. Only a local session the
    actor may read is returned; a foreign or non-local match is ``not_found`` so another
    principal's titles are never confirmed."""
    if not isinstance(name, str) or not name.strip():
        raise RuntimeStoreError('invalid_params')
    db = authority.db
    row = db.get_session(name)
    sid = row['id'] if row else db.resolve_session_by_title(name)
    tip = db.get_session(db.get_compression_tip(sid) or sid) if sid else None
    if tip is None:
        if missing_ok:
            return None
        raise RuntimeStoreError('not_found')
    try:
        from gateway.session_local_migration import resolve_local_target
        ref = resolve_local_target(authority, actor, tip['id'])
    except RuntimeStoreError as exc:
        if exc.reason == 'permission_denied':
            raise RuntimeStoreError('not_found') from None
        raise
    return ref


def _db_has_created_source(db) -> bool:
    """Read-only opens do not migrate, so a store from before the column has none.

    A lock or I/O error propagates (``_read_all`` retries); it is not proof the column is absent.
    """
    return any(row[1] == 'created_source' for row in db._read_all('PRAGMA table_info(sessions)'))


def resolve_latest_session(authority, actor, family, workspace=None):
    """The most recently active session of a surface family (``cli`` = classic CLI incl. its finite
    one-shot runs, ``tui``), scoped to ``workspace`` (git root / cwd) first and then global: the
    classic ``hermes -c`` / ``--resume latest`` rule (``hermes_cli.main._resolve_last_session``),
    applied by the owner so the client opens no database. Only a local session the actor may read
    counts; another principal's sessions are skipped, never confirmed.

    Gateway interactive rows are stored as ``source='local'`` with the creating surface in
    ``created_source`` (``storage_source``). ``oneshot`` keeps its own source, so it is not a local
    surface. A pre-column store has no ``local`` rows; naming the column would raise.
    """
    from agent.session_source import CLI_FAMILY_SOURCES
    families = {'cli': sorted(CLI_FAMILY_SOURCES), 'tui': ['tui']}
    # Only the labels ``storage_source`` rewrites to ``local``. ``oneshot`` stays ``oneshot``.
    local_surfaces = {'cli': ['cli'], 'tui': ['tui']}
    if family not in families or (workspace is not None and (not isinstance(workspace, str) or not workspace)):
        raise RuntimeStoreError('invalid_params')
    from gateway.session_local_migration import resolve_local_target
    sources = list(families[family])
    created = None
    if _db_has_created_source(authority.db):
        sources = sorted(families[family]) + ['local']
        created = local_surfaces[family]
    for scope in ([workspace] if workspace else []) + [None]:
        for row in authority.db.search_sessions(
                source=sources, limit=50, workspace_key=scope, local_created_sources=created):
            tip = authority.db.get_compression_tip(row['id']) or row['id']
            try:
                return resolve_local_target(authority, actor, tip)
            except RuntimeStoreError as exc:
                if exc.reason not in {'permission_denied', 'not_found', 'profile_mismatch'}:
                    raise
    raise RuntimeStoreError('not_found')


def validate_title(title):
    from hermes_state import SessionDB
    try:
        cleaned = SessionDB.sanitize_title(title) if isinstance(title, str) else None
    except ValueError:
        cleaned = None
    if cleaned is None:
        raise RuntimeStoreError('invalid_params')
    return cleaned


def title_new_session(authority, ref, title):
    """User-provenance title on a just-created local session (auto-titling never overwrites it).
    A concurrent creator that won the unique title is a conflict, not a silent orphan."""
    try:
        authority.db.set_session_title(ref.session_id, title)
    except ValueError:
        raise RuntimeStoreError('admission_conflict') from None
