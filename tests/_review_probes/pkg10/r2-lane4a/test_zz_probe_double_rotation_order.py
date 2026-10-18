"""Probe (claude, r2 lane4a): after two rotated compressions whose protected tail carries the same turn
twice, the archived first summary must not be painted between the live prompt and its answer."""
import pytest

from tests.gateway.test_resume_display_history import SUMMARY, _append, _local_session, _resume

S2 = SUMMARY + ' (second)'


def _rotate(db, parent, child, summary):
    compacted = [{'role': 'user', 'content': summary}, {'role': 'user', 'content': 'TAIL_Q'},
                 {'role': 'assistant', 'content': 'TAIL_A'}]
    assert db.try_acquire_compression_lock(parent, 'fixture')
    try:
        db.publish_compression_child(parent_session_id=parent, child_session_id=child, source='gui',
                                     messages=compacted, compression_lock_holder='fixture')
    finally:
        db.release_compression_lock(parent, 'fixture')


@pytest.mark.asyncio
async def test_archived_summary_never_splits_a_live_turn(tmp_path, monkeypatch):
    authority, actor, ref, physical = await _local_session(tmp_path, monkeypatch)
    db = authority.db
    _append(db, physical, 'OLD_Q', 'OLD_A', 'TAIL_Q', 'TAIL_A')
    _rotate(db, physical, 'child', SUMMARY)
    _rotate(db, 'child', 'child2', S2)
    rows = (await _resume(authority, actor, ref))['messages']
    got = [(r['content'][-10:], bool(r.get('archived'))) for r in rows]
    print(got)
    live = [r['content'] for r in rows if not r.get('archived')]
    assert live == [m['content'] for m in db.get_messages('child2')]
    contents = [r['content'] for r in rows]
    # the live turn TAIL_Q -> TAIL_A stays adjacent: no archived row painted between them
    assert contents.index('TAIL_A') == contents.index('TAIL_Q') + 1, got
