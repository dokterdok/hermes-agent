"""#109338: raw maintenance must not consume runtime-owned history."""
import json

import pytest

from hermes_state import SessionDB


def _ended(db, sid, *, parent=None, delegate=False):
    db.create_session(sid, source='tui', parent_session_id=parent,
                      model_config={'_delegate_from': parent} if delegate else None)
    db.end_session(sid, 'complete')
    db._execute_write(lambda conn: conn.execute(
        'UPDATE sessions SET started_at=1, ended_at=2, last_activity_at=1 WHERE id=?', (sid,)))


def _ledger(conn, sid, kind):
    if kind.startswith('admission'):
        status = 'terminal' if kind.endswith('terminal') else 'queued'
        conn.execute('''INSERT INTO session_admissions(admission_id,request_id,principal_id,
            target_session_id,lineage_json,payload_json,payload_digest,intent,status,owner_epoch,outcome)
            VALUES(?,?,?,?,?,'{}',?,'queue',?,1,?)''',
            ('a-' + sid, 'r-' + sid, 'fixture', sid, json.dumps([sid]), 'a' * 64, status,
             'rejected' if status == 'terminal' else None))
    else:
        status = 'terminal' if kind.endswith('terminal') else 'registered'
        conn.execute('''INSERT INTO worker_executions(execution_id,session_id,kind,owner_epoch,
            generation,status,adoption_digest) VALUES(?,?,'compute',1,0,?,?)''',
            ('w-' + sid, sid, status, 'b' * 64))


def _snapshot(db):
    return {table: [dict(row) for row in db._read_all('SELECT * FROM ' + table)]
            for table in ('sessions', 'messages', 'session_admissions', 'worker_executions',
                          'worker_receipts', 'state_meta', 'gateway_routing')}


@pytest.mark.parametrize('kind', ['admission-live', 'admission-terminal', 'worker-live', 'worker-terminal'])
@pytest.mark.parametrize('operation', ['single', 'bulk', 'verified', 'delegate', 'arrival'])
def test_explicit_raw_delete_refuses_whole_closure_without_retirement(tmp_path, monkeypatch, kind, operation):
    with SessionDB(tmp_path / 'state.db') as db:
        _ended(db, 'protected')
        _ended(db, 'legacy')
        target = 'protected'
        if operation == 'delegate':
            _ended(db, 'delegate', parent='protected', delegate=True)
            target = 'delegate'
        for sid in ('protected', 'legacy', target):
            db.append_message(sid, 'user', 'history fixture')
            (tmp_path / (sid + '.json')).write_text('transcript fixture')
        expected = db.get_session_delete_targets('protected')
        if operation != 'arrival':
            db._execute_write(lambda conn: _ledger(conn, target, kind))
        else:
            original = db._execute_write
            def arriving(callback, **kwargs):
                def joined(conn):
                    _ledger(conn, target, kind)
                    return callback(conn)
                return original(joined, **kwargs)
            monkeypatch.setattr(db, '_execute_write', arriving)
        before = _snapshot(db)
        with pytest.raises(ValueError, match='owning gateway') as refused:
            if operation == 'bulk':
                db.delete_sessions(['legacy', 'protected', 'missing'], sessions_dir=tmp_path)
            else:
                db.delete_session('protected', sessions_dir=tmp_path,
                    expected_delete_ids=expected if operation == 'verified' else None)
        assert refused.value.reason == 'runtime_coordination_required'
        assert _snapshot(db) == before
        assert all((tmp_path / (sid + '.json')).read_text() == 'transcript fixture'
                   for sid in ('protected', 'legacy', target))


@pytest.mark.parametrize('kind', ['admission-live', 'admission-terminal', 'worker-live', 'worker-terminal'])
@pytest.mark.parametrize('operation', ['prune', 'empty', 'ghost', 'if-empty'])
def test_raw_sweep_preserves_owned_rows_and_commits_only_legacy_effects(tmp_path, kind, operation):
    with SessionDB(tmp_path / 'state.db') as db:
        _ended(db, 'protected')
        _ended(db, 'legacy')
        db._execute_write(lambda conn: _ledger(conn, 'protected', kind))
        before = _snapshot(db)
        for sid in ('protected', 'legacy'):
            (tmp_path / (sid + '.json')).write_text('transcript fixture')
        preview = {}
        assert db.count_empty_sessions(report=preview) == 1
        assert preview == {'skipped_protected': 1}
        assert [r['id'] for r in db.list_prune_candidates(
            older_than_days=None, exclude_ledger_owned=True, report=preview)] == ['legacy']
        assert {r['id'] for r in db.list_prune_candidates(older_than_days=None)} == {'protected', 'legacy'}
        report = {}
        if operation == 'prune':
            count = db.prune_sessions(older_than_days=None, sessions_dir=tmp_path, report=report)
        elif operation == 'empty':
            count = db.delete_empty_sessions(sessions_dir=tmp_path, report=report)
        elif operation == 'ghost':
            count = db.prune_empty_ghost_sessions(sessions_dir=tmp_path, report=report)
        else:
            assert not db.delete_session_if_empty('protected', sessions_dir=tmp_path, report=report)
            assert report == {'removed': 0, 'skipped_protected': 1}
            count = int(db.delete_session_if_empty('legacy', sessions_dir=tmp_path))
        assert count == 1
        if operation != 'if-empty':
            assert report == {'removed': 1, 'skipped_protected': 1}
        assert db.get_session('protected') == before['sessions'][0]
        assert db.get_session('legacy') is None
        for table in ('session_admissions', 'worker_executions', 'worker_receipts'):
            assert [dict(row) for row in db._read_all('SELECT * FROM ' + table)] == before[table]
        assert (tmp_path / 'protected.json').read_text() == 'transcript fixture'
        assert not (tmp_path / 'legacy.json').exists()
        assert db._read_all('PRAGMA foreign_key_check') == []


def test_recreated_retired_id_can_be_deleted_again_without_losing_other_routes(tmp_path):
    """A legacy/external same-ID reinsert must not make route retirement non-idempotent.

    The current create_session API intentionally refuses tombstoned IDs. Insert a
    legacy row at the SQLite boundary to exercise cleanup of an already-recreated
    state, not to claim ordinary creation can bypass that admission fence.
    """
    from hermes_state_mutation_retirement import RETIRED_PREFIX

    with SessionDB(tmp_path / 'state.db') as db:
        _ended(db, 'reused')
        _ended(db, 'other')
        assert db.delete_session('reused')
        # Model an already-present legacy/backfilled row; public create_session
        # correctly refuses a retired ID and is not the path under test.
        db._execute_write(lambda conn: conn.execute(
            'INSERT INTO sessions(id,source,started_at,last_activity_at) VALUES(?,?,1,1)',
            ('reused', 'tui')))
        def routes(conn):
            for sid in ('reused', 'other'):
                conn.execute('''INSERT INTO gateway_routing(scope,session_key,entry_json,updated_at)
                    VALUES('',?,?,1)''', ('route-' + sid, json.dumps({'session_id': sid})))
        db._execute_write(routes)
        assert db.delete_session('reused')
        assert db.get_session('reused') is None
        assert db.get_session('other') is not None
        assert [row['session_key'] for row in db._read_all(
            'SELECT session_key FROM gateway_routing ORDER BY session_key')] == ['route-other']
        assert [row['key'] for row in db._read_all(
            'SELECT key FROM state_meta WHERE key=?', (RETIRED_PREFIX + 'reused',))] == [
                RETIRED_PREFIX + 'reused']
        assert db._read_all('PRAGMA foreign_key_check') == []
