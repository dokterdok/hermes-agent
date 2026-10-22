"""Read-only inspection needs no writable lifecycle metadata and cannot bind a successor."""
from pathlib import Path
import shutil

import pytest

import hermes_state
from hermes_cli.profile_incarnation import ensure_profile_incarnation, write_fresh_profile_incarnation
from hermes_state import SessionDB


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    home = root / "profiles" / "reader"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", hermes_state._IMPORT_DEFAULT_DB_PATH)
    ensure_profile_incarnation(home)
    with SessionDB(home / "state.db") as db:
        db.create_session("original", "tui")
    return home


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("existing_locks", [False, True])
def test_readonly_profile_does_not_require_writable_locks(home, existing_locks):
    locks = home.parent / ".locks"
    if existing_locks:
        for path in locks.iterdir():
            path.chmod(0o400)
        locks.chmod(0o500)
    else:
        shutil.rmtree(locks)
    home.parent.chmod(0o500)
    try:
        with SessionDB(home / "state.db", read_only=True) as db:
            assert db.get_session("original")["id"] == "original"
        assert locks.exists() is existing_locks
    finally:
        home.parent.chmod(0o700)
        if locks.exists():
            locks.chmod(0o700)
            for path in locks.iterdir():
                path.chmod(0o600)


@pytest.mark.parametrize("marked", [False, True])
def test_readonly_open_rejects_generation_replaced_before_connect(home, tmp_path, monkeypatch, marked):
    if not marked:
        (home / ".profile-incarnation").unlink()
    # Both fixture databases are quiescent before this simulated publication.
    successor = tmp_path / "successor"
    successor.mkdir()
    with SessionDB(successor / "state.db") as db:
        db.create_session("successor", "tui")
    real_connect = SessionDB._connect_read_only
    opened = []

    def replace_then_connect(self, timeout):
        home.rename(tmp_path / "retired")
        successor.rename(home)
        write_fresh_profile_incarnation(home)
        connection = real_connect(self, timeout)
        opened.append(connection)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(SessionDB, "_connect_read_only", replace_then_connect)
        with pytest.raises(FileNotFoundError):
            SessionDB(home / "state.db", read_only=True)
    assert opened
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    with SessionDB(home / "state.db", read_only=True) as fresh:
        assert fresh.get_session("successor")["id"] == "successor"
        assert fresh.get_session("original") is None


def test_readonly_open_survives_a_first_marker_backfill(home, monkeypatch):
    """Another process's first ``load_config`` backfills a legacy profile's marker while this one
    opens it read-only (a sidebar refresh, ``session_search``): one re-admission, bound to it."""
    from hermes_cli.config_home import _adopt_profile_incarnation
    from hermes_cli.profile_incarnation import read_profile_incarnation
    (home / ".profile-incarnation").unlink()  # a profile created before markers
    real_open, opens = SessionDB._open_read_only, []

    def open_while_backfilled(self):
        if not opens:
            _adopt_profile_incarnation(home)
        opens.append(1)
        return real_open(self)

    monkeypatch.setattr(SessionDB, "_open_read_only", open_while_backfilled)
    with SessionDB(home / "state.db", read_only=True) as db:
        assert db.get_session("original")["id"] == "original"
        assert db.expected_profile_incarnation == read_profile_incarnation(home)
    assert len(opens) == 2 and read_profile_incarnation(home) is not None


@pytest.mark.parametrize("successor_marker", ["fresh", "copied"])
def test_readonly_retry_never_binds_a_replacement(home, monkeypatch, successor_marker):
    """The retry is bound to the directory and marker the backfill check saw: a profile replaced in
    between (its first handle, the holder, was just closed) fails closed instead of binding it."""
    from hermes_cli.config_home import _adopt_profile_incarnation
    (home / ".profile-incarnation").unlink()
    retired = home.with_name("retired")
    real_open, real_close, opens = SessionDB._open_read_only, SessionDB._close_connection_quietly, []

    def open_while_backfilled(self):
        if not opens:
            _adopt_profile_incarnation(home)
        opens.append(1)
        return real_open(self)

    def close_then_replace(conn):
        real_close(conn)
        if conn is not None and opens and not retired.exists():
            home.rename(retired)
            shutil.copytree(retired, home)  # the same files in another directory
            if successor_marker == "fresh":
                write_fresh_profile_incarnation(home)

    monkeypatch.setattr(SessionDB, "_open_read_only", open_while_backfilled)
    monkeypatch.setattr(SessionDB, "_close_connection_quietly", staticmethod(close_then_replace))
    with pytest.raises(FileNotFoundError):
        SessionDB(home / "state.db", read_only=True)
    assert len(opens) == 1
