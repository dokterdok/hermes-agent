"""7e31a901123 splits display rows and tombstones across separate read snapshots."""
import pytest

from hermes_state import SessionDB


@pytest.mark.parametrize('reader', ['conversation', 'resume', 'paged_lineage'])
def test_compaction_between_display_reads_never_drops_preserved_tail(tmp_path, monkeypatch, reader):
    with SessionDB(tmp_path / 'state.db') as db:
        db.create_session('parent', source='test')
        db.append_messages_batch('parent', [
            {'role': 'user', 'content': 'OLD_Q'}, {'role': 'assistant', 'content': 'OLD_A'},
            {'role': 'user', 'content': 'TAIL_Q'}, {'role': 'assistant', 'content': 'TAIL_A'},
        ])
        assert db.try_acquire_compression_lock('parent', 'fixture')
        try:
            db.publish_compression_child(parent_session_id='parent', child_session_id='tip', source='test',
                messages=[{'role': 'user', 'content': 'SUMMARY'}, {'role': 'user', 'content': 'TAIL_Q'},
                          {'role': 'assistant', 'content': 'TAIL_A'}], compression_lock_holder='fixture')
        finally:
            db.release_compression_lock('parent', 'fixture')
        tail = db.get_messages_as_conversation('tip', include_row_ids=True)[-2:]
        read_all = db._read_all
        committed = []

        def read_then_compact(sql, params=()):
            rows = read_all(sql, params)
            if not committed and 'FROM messages WHERE session_id IN' in sql:
                committed.append(True)
                # The display SELECT finished. An owner compaction can commit before the next SELECT.
                db.archive_and_compact('tip', [{'role': 'user', 'content': 'SUMMARY_2'}, *tail], tail_count=2)
            return rows

        monkeypatch.setattr(db, '_read_all', read_then_compact)
        if reader == 'conversation':
            rows = db.get_messages_as_conversation('tip', include_ancestors=True, include_compacted=True)
        elif reader == 'resume':
            model, rows = db.get_resume_conversations('tip')
            assert any('TAIL_Q' in m['content'] for m in model)
        else:
            rows = db.get_messages('tip', include_ancestors=True, include_compacted=True, limit=100)
        assert committed
        contents = [m['content'] for m in rows]
        assert 'TAIL_Q' in contents and 'TAIL_A' in contents, contents
