"""Review cf983f7812f: archived groups must stay with their actual live anchor."""
import pytest

from tests.gateway.test_resume_display_history import SUMMARY, _local_session, _resume


@pytest.mark.asyncio
async def test_rotated_resume_keeps_archived_tool_output_after_its_call(tmp_path, monkeypatch):
    authority, actor, ref, physical = await _local_session(tmp_path, monkeypatch)
    db = authority.db
    db.append_messages_batch(physical, [
        {'role': 'user', 'content': 'OLD_Q'}, {'role': 'assistant', 'content': 'OLD_A'},
        {'role': 'user', 'content': 'Read the file'},
        {'role': 'assistant', 'content': 'Reading', 'tool_calls': [
            {'id': 'call-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
        {'role': 'tool', 'content': 'ORIGINAL_FULL_OUTPUT', 'tool_call_id': 'call-1', 'tool_name': 'read_file'},
        {'role': 'assistant', 'content': 'Answer'},
    ])
    pruned = db.get_messages_as_conversation(physical, include_row_ids=True)
    pruned[4]['content'] = 'Pruned output'
    # The normal prune publication preserves original full outputs for display, as the existing
    # test_display_dedupe_pruned_tool_payload.py contract requires.
    db.archive_and_compact(physical, pruned)
    tail = db.get_messages_as_conversation(physical, include_row_ids=True)[-4:]
    assert db.try_acquire_compression_lock(physical, 'fixture')
    try:
        db.publish_compression_child(parent_session_id=physical, child_session_id='child', source='gui',
            messages=[{'role': 'user', 'content': SUMMARY}, *tail], compression_lock_holder='fixture')
    finally:
        db.release_compression_lock(physical, 'fixture')

    rows = (await _resume(authority, actor, ref))['messages']
    contents = [m['content'] for m in rows]
    call_index = next(i for i, m in enumerate(rows) if m.get('tool_calls'))
    full_index = contents.index('ORIGINAL_FULL_OUTPUT')
    assert call_index < full_index, contents
