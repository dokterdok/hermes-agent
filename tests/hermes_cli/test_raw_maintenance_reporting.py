"""Maintenance caller reporting/refusal against temporary real stores (#109338/1403ca51)."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_state import SessionDB
from tests.hermes_state.test_never_active_keyed_prune import _insert
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
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert db.get_session('protected') is not None
        assert db.get_session('legacy') is None


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


def _never_active_pair(path):
    """One sweep-eligible keyed row and one ledger-owned twin, both routed."""
    with SessionDB(db_path=path) as db:
        _insert(db, 'junk', age_days=45)
        _insert(db, 'owned', age_days=45)
        db._execute_write(lambda conn: _ledger(conn, 'owned', 'admission-terminal'))
        db.save_gateway_routing_entry(
            'agent:main:telegram:dm:junk', json.dumps({'session_id': 'junk'}))
        db.save_gateway_routing_entry(
            'agent:main:telegram:dm:owned', json.dumps({'session_id': 'owned'}))


def _routing_ids(db):
    return {
        json.loads(row['entry_json']).get('session_id')
        for row in db._read_all('SELECT entry_json FROM gateway_routing')
    }


def test_never_active_prune_skips_ledger_rows_and_keeps_their_routes(tmp_path):
    path = tmp_path / 'state.db'
    _never_active_pair(path)
    with SessionDB(db_path=path) as db:
        preview = {}
        listed = db.list_never_active_keyed_sessions(
            older_than_days=30, exclude_ledger_owned=True, report=preview)
        assert [row['id'] for row in listed] == ['junk']
        assert preview['skipped_protected'] == 1
        assert {row['id'] for row in db.list_never_active_keyed_sessions(older_than_days=30)} == {
            'junk', 'owned'}
        report = {}
        deleted, routing_deleted = db.prune_never_active_keyed_sessions(
            older_than_days=30, report=report)
        assert (deleted, routing_deleted) == (1, 1)
        assert report == {'skipped_protected': 1, 'removed': 1}
        assert db.get_session('junk') is None
        assert db.get_session('owned') is not None
        assert _routing_ids(db) == {'owned'}


def test_cli_never_active_preview_excludes_ledger_rows(tmp_path, monkeypatch, capsys):
    from hermes_cli.sessions_cmd import cmd_sessions
    _cli_home(tmp_path, monkeypatch)
    _never_active_pair(tmp_path / 'state.db')
    args = SimpleNamespace(sessions_action='prune', yes=True, dry_run=True, force=True,
                           older_than=None, never_active=True)
    cmd_sessions(args)
    output = capsys.readouterr().out
    assert 'junk' in output
    assert 'owned' not in output
    assert 'Skipped 1 session(s)' in output
    assert 'Dry run — nothing deleted.' in output
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert db.get_session('junk') is not None
        assert db.get_session('owned') is not None
        assert _routing_ids(db) == {'junk', 'owned'}


def test_cli_never_active_commit_skips_without_raising(tmp_path, monkeypatch, capsys):
    from hermes_cli.sessions_cmd import cmd_sessions
    _cli_home(tmp_path, monkeypatch)
    _never_active_pair(tmp_path / 'state.db')
    args = SimpleNamespace(sessions_action='prune', yes=True, dry_run=False, force=True,
                           older_than=None, never_active=True)
    assert cmd_sessions(args) is None
    output = capsys.readouterr().out
    assert 'Refused:' not in output
    assert 'Deleted 1 never-active session(s)' in output
    assert 'Skipped 1 session(s)' in output
    assert 'owned' not in output
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        assert db.get_session('junk') is None
        assert db.get_session('owned') is not None
        assert _routing_ids(db) == {'owned'}


def test_browse_and_exit_delete_surface_ledger_refusal(tmp_path):
    from hermes_cli.cli_tui_runtime_mixin import exit_delete_outcome
    from hermes_cli.sessions_cmd_browse import delete_browsed_session
    path = tmp_path / 'state.db'
    with SessionDB(db_path=path) as db:
        _ended(db, 'owned')
        db._execute_write(lambda conn: _ledger(conn, 'owned', 'admission-terminal'))
        deleted, refusal = delete_browsed_session(db, 'owned')
        assert deleted is False
        assert refusal and 'owning gateway' in refusal
        outcome, detail = exit_delete_outcome(db, 'owned')
        assert outcome == 'refused' and 'owning gateway' in detail
        assert db.get_session('owned') is not None

        class Boom:
            def delete_session(self, *args, **kwargs):
                raise RuntimeError('unrelated failure')

        with pytest.raises(RuntimeError, match='unrelated failure'):
            exit_delete_outcome(Boom(), 'owned')
        assert delete_browsed_session(Boom(), 'owned') == (False, None)


def test_explicit_delete_refusal_is_mappable_and_leaves_the_row(tmp_path):
    from hermes_state_raw_delete import SessionLedgerProtectedError, protected_delete_refusal

    assert protected_delete_refusal(RuntimeError('disk full')) is None
    path = tmp_path / 'state.db'
    with SessionDB(db_path=path) as db:
        _ended(db, 'owned')
        db._execute_write(lambda conn: _ledger(conn, 'owned', 'worker-terminal'))
        with pytest.raises(SessionLedgerProtectedError) as raised:
            db.delete_session('owned')
        message, code = protected_delete_refusal(raised.value)
        assert code == 'runtime_coordination_required'
        assert 'owning gateway' in message
        assert db.get_session('owned') is not None


def test_tui_session_delete_maps_ledger_refusal_and_not_other_errors(monkeypatch):
    from hermes_state_raw_delete import SessionLedgerProtectedError
    import tui_gateway.server as server

    class Owned:
        def delete_session(self, sid, sessions_dir=None):
            raise SessionLedgerProtectedError()

    monkeypatch.setattr(server, '_get_db', lambda: Owned())
    refused = server.handle_request(
        {'id': '1', 'method': 'session.delete', 'params': {'session_id': 'owned'}})
    assert refused['error']['code'] == 4033
    assert refused['error']['data']['code'] == 'runtime_coordination_required'
    assert 'owning gateway' in refused['error']['message']

    class Boom:
        def delete_session(self, sid, sessions_dir=None):
            raise RuntimeError('disk full')

    monkeypatch.setattr(server, '_get_db', lambda: Boom())
    failed = server.handle_request(
        {'id': '1', 'method': 'session.delete', 'params': {'session_id': 'x'}})
    assert failed['error']['code'] == 5036
    assert 'disk full' in failed['error']['message']


def test_auto_prune_reports_committed_protection_without_changing_selection(tmp_path):
    path = tmp_path / 'state.db'
    _store(path)
    with SessionDB(db_path=path) as db:
        result = db.maybe_auto_prune_and_vacuum(retention_days=1, min_interval_hours=0, vacuum=False)
        assert result['pruned'] == 1
        assert result['skipped_protected'] == 1
        assert db.get_session('protected') is not None and db.get_session('legacy') is None
