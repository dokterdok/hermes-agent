"""Probe (lane 4a review, G1): the resume guard (``assert_resume_safe`` / ``get_resume_message_count``)
is documented to count "exactly what a resume loads" (``_resume_lineage_ids``). Repeated in-place
compaction leaves the carried tail's superseded originals as removed rows (``active=0, compacted=0``).
The display read must not load bodies the guard never counted. Real SessionDB, real archive_and_compact.
"""
from hermes_state import SessionDB


def _compacted_repeatedly(db, rounds, big):
    db.create_session('s', source='test')
    db.append_messages_batch('s', [{'role': 'user', 'content': 'U1'}, {'role': 'assistant', 'content': 'A1'},
                                   {'role': 'user', 'content': 'U2'}, {'role': 'assistant', 'content': big}])
    for n in range(rounds):
        # /compress with nothing new since: the same verbatim tail (U2 + the big reply) is carried again.
        db.archive_and_compact('s', [{'role': 'user', 'content': f'[CONTEXT SUMMARY] round {n}'},
                                     {'role': 'user', 'content': 'U2'}, {'role': 'assistant', 'content': big}],
                               tail_count=2)


def test_display_read_loads_only_rows_the_resume_guard_counts(tmp_path, monkeypatch):
    big = 'x' * 100_000
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        _compacted_repeatedly(db, 6, big)
        fetched = []
        real = db._fetch_conversation_rows

        def spy(*args, **kwargs):
            rows = real(*args, **kwargs)
            fetched.extend(rows)
            return rows
        monkeypatch.setattr(db, '_fetch_conversation_rows', spy)
        shown = db.get_messages_as_conversation('s', include_ancestors=True, include_compacted=True,
                                                include_row_ids=True)
        # The visible history shows the big reply once.
        assert sum(1 for m in shown if m.get('content') == big) == 1
        counted = db.get_resume_message_count('s')
        big_bodies = sum(1 for r in fetched if r['content'] is not None and len(r['content']) >= len(big))
        assert len(fetched) == counted, (len(fetched), counted, big_bodies)
