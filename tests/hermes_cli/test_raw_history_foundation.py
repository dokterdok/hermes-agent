"""Verified display-history deletion with real compacted SQLite rows."""
from types import SimpleNamespace

import pytest
from hermes_state import SessionDB
from tests.hermes_state.test_raw_history_ledger import _ended, _ledger, _snapshot

def _compacted_store(path):
    """Accepted compacted transcript fixture, local to this owner regression."""
    db = SessionDB(db_path=path)
    db.create_session('s1', 'telegram')
    for i in range(1, 7):
        db.append_message('s1', 'user', f'question {i}')
        db.append_message('s1', 'assistant', f'answer {i}')
    tail = [{'role': 'user', 'content': 'question 6'},
            {'role': 'assistant', 'content': 'answer 6'}]
    db.archive_and_compact(
        's1', [{'role': 'user', 'content': '[CONTEXT COMPACTION] summary'}, *tail],
        watermark=db.get_active_message_watermark('s1'), tail_count=len(tail))
    return db



@pytest.mark.parametrize('protected', [False, True])
@pytest.mark.parametrize('drift', ['none', 'transcript', 'delegate'])
def test_verified_delete_keeps_transcript_target_and_ledger_fences(tmp_path, monkeypatch, protected, drift):
    from hermes_state_raw_delete import SessionLedgerProtectedError

    with _compacted_store(tmp_path / 'state.db') as db:
        if protected:
            db._execute_write(lambda conn: _ledger(conn, 's1', 'admission-terminal'))
        targets = db.get_session_delete_targets('s1')
        exported = db.export_session('s1', include_compacted=True)
        assert exported is not None
        expected = exported['messages']
        transcript = tmp_path / 's1.json'
        transcript.write_text('retained transcript')
        if drift == 'delegate':
            _ended(db, 'late-child', parent='s1', delegate=True)
        before = _snapshot(db)
        entered = []
        if drift == 'transcript':
            write = db._execute_write

            def write_with_late_history(callback, **kwargs):
                monkeypatch.setattr(db, '_execute_write', write)
                def joined(conn):
                    changed = conn.execute(
                        "UPDATE messages SET content=? WHERE session_id=? AND content=?",
                        ('changed after export', 's1', 'answer 1'))
                    assert changed.rowcount == 1
                    entered.append(True)
                    return callback(conn)
                return write(joined, **kwargs)

            monkeypatch.setattr(db, '_execute_write', write_with_late_history)
        def delete():
            return db.delete_session('s1', sessions_dir=tmp_path, expected_delete_ids=targets,
                                     expected_display_messages={'s1': expected})

        if protected and drift == 'none':
            with pytest.raises(SessionLedgerProtectedError):
                delete()
            assert _snapshot(db) == before
        else:
            removed = delete()
            assert removed is (drift == 'none')
        if drift != 'none' or protected:
            assert db.get_session('s1') is not None
            assert transcript.read_text() == 'retained transcript'
            assert _snapshot(db)['session_admissions'] == before['session_admissions']
            if drift == 'transcript':
                assert entered == [True]
                retained = db.export_session('s1', include_compacted=True)
                assert retained is not None
                assert any(row['content'] == 'changed after export'
                           for row in retained['messages'])
        else:
            assert db.get_session('s1') is None
            assert not transcript.exists()
        assert db._read_all('PRAGMA foreign_key_check') == []


@pytest.mark.parametrize('mode', ['unchanged', 'late-message', 'protected'])
def test_verified_markdown_export_composes_display_snapshot_and_raw_guard(tmp_path, monkeypatch, mode):
    from hermes_cli import session_export_md, sessions_cmd
    from hermes_state_raw_delete import SessionLedgerProtectedError

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    args = SimpleNamespace(format='md', output=str(tmp_path / 'exports'), force=False,
                           delete_after_verified=True, yes=True, session_id='s1', lineage='single')
    with _compacted_store(tmp_path / 'state.db') as db:
        if mode == 'protected':
            db._execute_write(lambda conn: _ledger(conn, 's1', 'worker-terminal'))
        verify = session_export_md.verify_export_file
        verified = []

        def verify_then_arrive(path, data):
            result = verify(path, data)
            assert result[0]
            verified.append(path)
            if mode == 'late-message':
                db.append_message('s1', 'user', 'arrived after verified export')
            return result

        monkeypatch.setattr(session_export_md, 'verify_export_file', verify_then_arrive)
        def export():
            sessions_cmd._export_markdown(db, args, {}, session_export_md.redact_session_data)

        if mode == 'protected':
            with pytest.raises(SessionLedgerProtectedError):
                export()
        else:
            export()
        assert len(verified) == 1
        exported_text = verified[0].read_text()
        assert 'answer 1' in exported_text and 'answer 6' in exported_text
        assert (db.get_session('s1') is None) is (mode == 'unchanged')
        if mode == 'late-message':
            assert any(row['content'] == 'arrived after verified export' for row in db.get_messages('s1'))
        if mode == 'protected':
            worker = db._read_one('SELECT status FROM worker_executions WHERE session_id=?', ('s1',))
            assert worker is not None and worker[0] == 'terminal'
