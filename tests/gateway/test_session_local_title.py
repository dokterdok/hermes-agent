"""Authority-side title selection: main's classic rule, without leaking foreign titles."""
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_contract import Principal
from gateway.session_local_title import resolve_latest_session, resolve_titled_session
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch

_WORKSPACE = "/work/repo"


def _authority(db):
    epoch = begin_runtime_epoch(db, instance_id='current')
    return SessionAuthority(SimpleNamespace(_draining=False), profile_id='owned',
                            instance_id='current', db=db, epoch=epoch)


def _local(db, authority, sid, *, user, title):
    db.create_session(sid, source='cli', chat_id=sid, user_id=user)
    db.set_session_title(sid, title)
    source = SessionSource(platform=Platform.LOCAL, chat_id=sid, user_id=user, chat_type='dm')
    authority.sessions[sid] = LiveSession(source, 'route-' + sid)


def test_title_resolves_like_classic_cli_and_never_confirms_foreign_titles(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        authority = _authority(db)
        _local(db, authority, 'local-a', user='human', title='thread')
        _local(db, authority, 'local-a2', user='human', title='thread #2')
        _local(db, authority, 'local-b', user='stranger', title='private')
        # A non-local (messaging) session carrying a title is not resumable through the local client.
        db.create_session('tg-1', source='telegram', chat_id='chat', user_id='human')
        db.set_session_title('tg-1', 'telegram thread')
        human = Principal('human', 'owned', frozenset({'session:read'}), 'viewer')

        def resolve(title):
            return resolve_titled_session(authority, human, title).session_id
        # Latest "<title> #N" continuation wins over the bare title (classic lineage rule).
        assert resolve('thread') == 'local-a2'
        assert resolve('thread #2') == 'local-a2'
        # A literal id passed as title still resolves (main tries id before title).
        assert resolve('local-a') == 'local-a'
        for foreign in ('private', 'telegram thread', 'nope'):
            with pytest.raises(RuntimeStoreError, match='not_found'):
                resolve(foreign)
        assert resolve_titled_session(authority, human, 'nope', missing_ok=True) is None
        with pytest.raises(RuntimeStoreError, match='invalid_params'):
            resolve('')


def _stamp(db, rows):
    with db._lock:
        db._conn.executemany(
            "UPDATE sessions SET created_source=?, started_at=?, git_repo_root=? WHERE id=?",
            rows,
        )
        db._conn.commit()


def _remember(authority, sid):
    source = SessionSource(platform=Platform.LOCAL, chat_id=sid, user_id="human", chat_type="dm")
    authority.sessions[sid] = LiveSession(source, "route-" + sid)


@pytest.mark.parametrize(("family", "created", "noise"), [
    ("cli", "cli", ("desktop", "tui")),
    ("tui", "tui", ("desktop", "cli")),
])
def test_resolve_latest_finds_the_surface_behind_newer_local_rows(tmp_path, family, created, noise):
    """The family's own row stays visible behind 200 newer ``local`` rows of other surfaces.

    Gateway interactive rows are ``source='local'`` with the surface in ``created_source``.
    The lookup page is 50, so taking every local row hides this one.
    """
    sid = "local-" + family
    with SessionDB(db_path=tmp_path / "state.db") as db:
        authority = _authority(db)
        db.create_session(sid, source="local", chat_id=sid, user_id="human", git_repo_root=_WORKSPACE)
        stamped = [(created, 1_000_000, _WORKSPACE, sid)]
        for index in range(200):
            nid = f"local-noise-{index:03d}"
            db.create_session(
                nid, source="local", chat_id=nid, user_id="other", git_repo_root=_WORKSPACE)
            stamped.append((noise[index % 2], 2_000_000 + index, _WORKSPACE, nid))
        _stamp(db, stamped)
        _remember(authority, sid)
        actor = Principal("human", "owned", frozenset({"session:read"}), "viewer")
        assert resolve_latest_session(authority, actor, family, _WORKSPACE).session_id == sid
        if family != "cli":
            return
        # A newer finite CLI run is still this family, and it is stored as ``oneshot`` not ``local``.
        shot = "local-shot"
        db.create_session(
            shot, source="oneshot", chat_id=shot, user_id="human", git_repo_root=_WORKSPACE)
        _stamp(db, [(None, 9_000_000, _WORKSPACE, shot)])
        _remember(authority, shot)
        assert resolve_latest_session(authority, actor, "cli", _WORKSPACE).session_id == shot


def test_resolve_latest_on_a_pre_column_store_finds_the_legacy_cli_row(tmp_path):
    """A read-only store from before ``created_source`` still resolves ``source='cli'``."""
    path = tmp_path / "state.db"
    writer = SessionDB(db_path=path)
    writer.create_session(
        "local-legacy", source="cli", chat_id="local-legacy", user_id="human", git_repo_root=_WORKSPACE)
    epoch = begin_runtime_epoch(writer, instance_id="current")
    with writer._lock:
        writer._conn.execute("DROP INDEX IF EXISTS idx_sessions_local_created_started")
        writer._conn.execute("ALTER TABLE sessions DROP COLUMN created_source")
        writer._conn.commit()
    writer.close()

    db = SessionDB(db_path=path, read_only=True)
    try:
        authority = SessionAuthority(
            SimpleNamespace(_draining=False), profile_id="owned", instance_id="current", db=db, epoch=epoch)
        _remember(authority, "local-legacy")
        actor = Principal("human", "owned", frozenset({"session:read"}), "viewer")
        found = resolve_latest_session(authority, actor, "cli", _WORKSPACE)
        assert found.session_id == "local-legacy"
        columns = {row[1] for row in db._conn.execute("PRAGMA table_info(sessions)")}
        assert "created_source" not in columns
    finally:
        db.close()

