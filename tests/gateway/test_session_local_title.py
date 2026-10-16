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


def test_reopen_fills_created_source_from_the_local_receipt(tmp_path):
    """Rows stored before the insert stamp pick up ``created_source`` from the receipt.

    ``unknown`` is not a surface and stays unset.
    """
    import json

    from hermes_state_local import POLICY_PREFIX, _CREATED_SOURCE_BACKFILL

    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    db.create_session("local-old", source="local", chat_id="local-old", user_id="human")
    db.create_session("local-unknown", source="local", chat_id="local-unknown", user_id="human")
    with db._lock:
        db._conn.executemany(
            "UPDATE sessions SET created_source=NULL WHERE id=?",
            [("local-old",), ("local-unknown",)])
        db._conn.executemany(
            "INSERT INTO state_meta(key, value) VALUES(?, ?)",
            [(POLICY_PREFIX + "local-old", json.dumps({"policy": {"source": "cli"}})),
             (POLICY_PREFIX + "local-unknown", json.dumps({"policy": {"source": "unknown"}}))])
        db._conn.execute("DELETE FROM state_meta WHERE key=?", (_CREATED_SOURCE_BACKFILL,))
        db._conn.commit()
    db.close()
    db = SessionDB(db_path=path)
    try:
        assert db.get_session("local-old")["created_source"] == "cli"
        assert db.get_session("local-unknown")["created_source"] is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_resume_latest_reaches_a_gateway_cli_session_behind_newer_local_rows(tmp_path, monkeypatch):
    """Owner round-trip for ``hermes -z --resume latest`` / ``chat -c`` on the transport client.

    ``session.create`` plus the routing-peer refresh stores the CLI row as ``source='local'``.
    Two hundred newer Desktop/TUI ``local`` rows sit in front of it. ``_resume_latest`` asks the
    owner for ``latest='cli'`` in this workspace, which resolves through ``resolve_local_target``.
    """
    from pathlib import Path

    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from hermes_cli.gateway_chat import _resume_latest
    from hermes_cli.gateway_client import GatewayClientError

    monkeypatch.setattr(run, "_load_gateway_config", lambda: {
        "model": {"default": "fixture"}, "platform_toolsets": {"cli": []}})
    monkeypatch.setattr(run, "_resolve_gateway_model", lambda config: "fixture")
    monkeypatch.setattr("hermes_cli.main._resolve_workspace_key", lambda: _WORKSPACE)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    runner = run.GatewayRunner.__new__(run.GatewayRunner)
    runner.adapters = {}
    runner.session_store = store
    runner._session_db = store._db
    runner._draining = False
    authority = await initialize_session_authority(
        runner, profile_id=str(Path(store._db.db_path).parent), instance_id="test")
    authority._schedule = lambda ref: None
    connection = AuthorityConnection(
        authority, SimpleNamespace(write=lambda frame: None), {"user_id": "owner"})

    class _Client:
        def __init__(self):
            self.calls = []

        async def rpc(self, method, **params):
            self.calls.append((method, params))
            reply = await connection.dispatch({"id": 1, "method": method, "params": params})
            if "error" in reply:
                raise GatewayClientError(reply["error"]["message"])
            return reply["result"]

    client = _Client()
    try:
        created = await connection.dispatch({
            "id": 1, "method": "session.create",
            "params": {"request_id": "chat", "source": "cli", "cwd": str(tmp_path)}})
        assert "result" in created, created
        sid = created["result"]["stored_session_id"]
        live = authority.sessions[sid]
        store._record_gateway_session_peer(sid, live.route, live.source)
        row = authority.db.get_session(sid)
        assert (row["source"], row["created_source"]) == ("local", "cli")
        with authority.db._lock:
            authority.db._conn.execute(
                "UPDATE sessions SET started_at=?, git_repo_root=? WHERE id=?",
                (1_000_000, _WORKSPACE, sid))
            authority.db._conn.commit()
        for index in range(200):
            nid = f"local-noise-{index:03d}"
            authority.db.create_session(nid, source="local", chat_id=nid, user_id="other")
        with authority.db._lock:
            authority.db._conn.executemany(
                "UPDATE sessions SET created_source=?, started_at=?, git_repo_root=? WHERE id=?",
                [(("desktop", "tui")[index % 2], 2_000_000 + index, _WORKSPACE, f"local-noise-{index:03d}")
                 for index in range(200)],
            )
            authority.db._conn.commit()
        args = SimpleNamespace(continue_last=False, resume="latest", in_dir=None)
        snapshot = await _resume_latest(client, args)
        assert snapshot["stored_session_id"] == sid
        assert client.calls[-1][0] == "session.resume"
        assert client.calls[-1][1]["latest"] == "cli"
        assert client.calls[-1][1]["workspace"] == _WORKSPACE
    finally:
        await connection.close()
        authority.db.close()
