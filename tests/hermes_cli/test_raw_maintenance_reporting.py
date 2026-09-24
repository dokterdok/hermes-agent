"""Maintenance caller reporting/refusal against temporary real stores (#109338/1403ca51)."""
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_state import SessionDB
from tests.hermes_state.test_raw_history_ledger import _ended, _ledger, _snapshot


def _history_snapshot(db):
    # Reopening a writer may upgrade FTS bookkeeping; raw history must not change.
    return {key: rows for key, rows in _snapshot(db).items() if key != 'state_meta'}


def _store(path, kind='admission-terminal'):
    with SessionDB(db_path=path) as db:
        _ended(db, 'protected')
        _ended(db, 'legacy')
        db._execute_write(lambda conn: _ledger(conn, 'protected', kind))
        return _history_snapshot(db)


def _cli_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr('hermes_state.DEFAULT_DB_PATH', tmp_path / 'state.db')


@pytest.mark.parametrize('dry_run', [True, False])
def test_cli_prune_reports_only_eligible_and_committed_skips(tmp_path, monkeypatch, capsys, dry_run):
    from hermes_cli.sessions_cmd import cmd_sessions
    _cli_home(tmp_path, monkeypatch)
    _store(tmp_path / 'state.db')
    args = SimpleNamespace(sessions_action='prune', yes=True, dry_run=dry_run,
                           source='tui', older_than=None, never_active=False,
                           include_pinned=False, force=True)
    cmd_sessions(args)
    output = capsys.readouterr().out
    assert 'protected' not in output
    assert 'Skipped 1 session(s)' in output
    assert ('Dry run — nothing deleted.' if dry_run else 'Pruned 1 session(s).') in output
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert db.get_session('protected') is not None
        assert (db.get_session('legacy') is None) is not dry_run


def test_cli_explicit_delete_refuses_owned_history_without_swallowing_other_errors(tmp_path, monkeypatch, capsys):
    from hermes_cli import sessions_cmd
    _cli_home(tmp_path, monkeypatch)
    before = _store(tmp_path / 'state.db')
    args = SimpleNamespace(sessions_action='delete', session_id='protected', yes=True)
    assert sessions_cmd.cmd_sessions(args) == 1
    output = capsys.readouterr().out
    assert 'Refused:' in output and 'owning gateway' in output
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert _history_snapshot(db) == before
    monkeypatch.setitem(sessions_cmd._DB_HANDLERS, 'delete',
                        lambda db, args: (_ for _ in ()).throw(RuntimeError('unrelated failure')))
    with pytest.raises(RuntimeError, match='unrelated failure'):
        sessions_cmd.cmd_sessions(args)


def test_cli_archive_still_includes_protected_rows_without_deletion(tmp_path, monkeypatch, capsys):
    from hermes_cli.sessions_cmd import cmd_sessions
    _cli_home(tmp_path, monkeypatch)
    _store(tmp_path / 'state.db')
    args = SimpleNamespace(sessions_action='archive', yes=True, dry_run=False,
                           source='tui', older_than=None, include_pinned=False)
    cmd_sessions(args)
    assert 'Archived 2 session(s).' in capsys.readouterr().out
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert db.get_session('protected')['archived'] == 1
        assert db.get_session('legacy')['archived'] == 1
        assert db._read_one('SELECT COUNT(*) FROM session_admissions')[0] == 1


def test_http_prune_empty_and_bulk_return_committed_reports_and_precise_conflict(tmp_path, monkeypatch):
    from hermes_cli.web_routers.sessions import manage_router
    _cli_home(tmp_path, monkeypatch)
    before = _store(tmp_path / 'state.db', kind='worker-terminal')
    app = FastAPI()
    app.include_router(manage_router)
    with TestClient(app) as client:
        refusal = client.post('/api/sessions/bulk-delete', json={'ids': ['legacy', 'protected']})
        assert refusal.status_code == 409
        assert refusal.json()['detail']['code'] == 'runtime_coordination_required'
        with SessionDB(db_path=tmp_path / 'state.db', read_only=True) as db:
            assert _history_snapshot(db) == before
        count = client.get('/api/sessions/empty/count')
        assert count.status_code == 200
        assert count.json() == {'count': 1, 'skipped_protected': 1}
        dry = client.post('/api/sessions/prune', json={'older_than_days': 1, 'dry_run': True})
        assert dry.status_code == 200
        preview = dry.json()
        assert preview['matched'] == 1 and preview['skipped_protected'] == 1
        assert [r['id'] for r in preview['sessions']] == ['legacy']
        deleted = client.delete('/api/sessions/empty')
        assert deleted.status_code == 200
        assert deleted.json() == {'ok': True, 'deleted': 1, 'skipped_protected': 1}
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert db.get_session('protected') == before['sessions'][0]
        assert db.get_session('legacy') is None


def test_http_prune_commit_reports_transaction_protection(tmp_path, monkeypatch):
    from hermes_cli.web_routers.sessions import manage_router
    _cli_home(tmp_path, monkeypatch)
    _store(tmp_path / 'state.db')
    app = FastAPI()
    app.include_router(manage_router)
    with TestClient(app) as client:
        response = client.post('/api/sessions/prune', json={'older_than_days': 1})
    assert response.status_code == 200
    assert response.json()['removed'] == 1
    assert response.json()['skipped_protected'] == 1


def test_served_http_app_keeps_owner_refusal_as_409(tmp_path, monkeypatch):
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    _cli_home(tmp_path, monkeypatch)
    before = _store(tmp_path / 'state.db')
    with TestClient(app) as client:
        response = client.post('/api/sessions/bulk-delete', json={'ids': ['legacy', 'protected']},
                               headers={_SESSION_HEADER_NAME: _SESSION_TOKEN})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'runtime_coordination_required'
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert _history_snapshot(db) == before


def test_http_count_reporting_is_profile_scoped_across_a_b_a(tmp_path, monkeypatch):
    from hermes_cli.web_routers.sessions import manage_router
    home = tmp_path / '.hermes'
    other = home / 'profiles' / 'worker'
    other.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    _cli_home(home, monkeypatch)
    _store(home / 'state.db')
    with SessionDB(db_path=other / 'state.db') as db:
        _ended(db, 'other')
    app = FastAPI()
    app.include_router(manage_router)
    with TestClient(app) as client:
        a = client.get('/api/sessions/empty/count', params={'profile': 'default'})
        b = client.get('/api/sessions/empty/count', params={'profile': 'worker'})
        again = client.get('/api/sessions/empty/count', params={'profile': 'default'})
    assert a.status_code == b.status_code == again.status_code == 200
    assert a.json() == again.json() == {'count': 1, 'skipped_protected': 1}
    assert b.json() == {'count': 1, 'skipped_protected': 0}


def test_http_unrelated_failure_is_not_mapped_to_coordination_conflict(tmp_path, monkeypatch):
    from hermes_cli import web_server_sessions
    from hermes_cli.web_routers.sessions import manage_router
    _cli_home(tmp_path, monkeypatch)
    closed = []

    class BrokenStore:
        def delete_sessions(self, ids):
            raise RuntimeError('unrelated failure')

        def close(self):
            closed.append(True)

    monkeypatch.setattr(web_server_sessions, '_open_session_db_for_profile',
                        lambda *args, **kwargs: BrokenStore())
    app = FastAPI()
    app.include_router(manage_router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/api/sessions/bulk-delete', json={'ids': ['x']})
    assert response.status_code == 500
    assert closed == [True]


def test_auto_prune_reports_committed_protection_without_changing_selection(tmp_path):
    path = tmp_path / 'state.db'
    _store(path)
    with SessionDB(db_path=path) as db:
        result = db.maybe_auto_prune_and_vacuum(retention_days=1, min_interval_hours=0, vacuum=False)
        assert result['pruned'] == 1
        assert result['skipped_protected'] == 1
        assert db.get_session('protected') is not None and db.get_session('legacy') is None
