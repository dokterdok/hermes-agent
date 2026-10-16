"""``hermes sessions list|browse --source cli`` must see gateway rows stored as ``local``."""
from argparse import Namespace

from hermes_state import SessionDB


def test_list_and_browse_source_cli_include_the_gateway_row(tmp_path, capsys, monkeypatch):
    from hermes_cli.sessions_cmd import _cmd_browse, _cmd_list

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("cli-session", source="local")
    db.create_session("desk-session", source="local")
    with db._lock:
        db._conn.executemany(
            "UPDATE sessions SET created_source=?, started_at=? WHERE id=?",
            [("cli", 1, "cli-session"), ("desktop", 2, "desk-session")],
        )
        db._conn.commit()

    _cmd_list(db, Namespace(limit=20, source="cli", workspace=None))
    listed = capsys.readouterr().out
    assert "cli-session" in listed
    assert "desk-session" not in listed
    assert "No sessions found." not in listed

    offered = {}

    def picker(sessions, session_db=None):
        offered["ids"] = [row["id"] for row in sessions]

    monkeypatch.setattr("hermes_cli.sessions_cmd._session_browse_picker", picker)
    _cmd_browse(db, Namespace(limit=20, source="cli"))
    assert offered["ids"] == ["cli-session"]
    assert "No sessions found." not in capsys.readouterr().out


def test_stats_counts_the_same_gateway_cli_rows_the_list_shows(tmp_path, capsys):
    from hermes_cli.sessions_cmd import _cmd_list, _cmd_stats

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("cli-session", source="local")
    with db._lock:
        db._conn.execute("UPDATE sessions SET created_source=? WHERE id=?", ("cli", "cli-session"))
        db._conn.commit()
    _cmd_list(db, Namespace(limit=20, source="cli", workspace=None))
    assert "cli-session" in capsys.readouterr().out
    _cmd_stats(db, Namespace())
    stats = capsys.readouterr().out
    assert "cli: 1 sessions" in stats
