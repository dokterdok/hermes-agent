"""Review of c72966db57c: a durable user row can grow without changing its id."""
import json

import pytest

from tests.gateway.test_resume_display_history import SUMMARY, _local_session, _resume


@pytest.mark.asyncio
async def test_rewritten_live_row_counts_against_archive_budget(tmp_path, monkeypatch):
    from gateway import session_local_recovery as recovery

    authority, actor, ref, physical = await _local_session(tmp_path, monkeypatch)
    db = authority.db
    for n in range(28):
        db.append_message(physical, 'user' if n % 2 == 0 else 'assistant', f'{n}:' + 'h' * 100 * 1024)
    db.archive_and_compact(physical, [{'role': 'user', 'content': SUMMARY}])
    prompt_id = db.append_message(physical, 'user', '@large-file.txt')
    prepare = recovery.prepare_history
    rewritten = []

    async def prepare_then_expand(authority_, ref_):
        prepared = await prepare(authority_, ref_)
        if not rewritten:
            # Same production writer used by turn_context to expand accepted @files/native image parts.
            assert db.set_user_message_content(physical, prompt_id, 'expanded:' + 'n' * 2 * 1024 * 1024) == 1
            rewritten.append(prompt_id)
        return prepared

    monkeypatch.setattr(recovery, 'prepare_history', prepare_then_expand)
    raced = await _resume(authority, actor, ref, display_projection=True)
    fresh = await _resume(authority, actor, ref, display_projection=True)
    raced_bytes, fresh_bytes = len(json.dumps(raced)), len(json.dumps(fresh))
    assert fresh_bytes < 8 * 1024 * 1024
    assert raced['messages'][-1]['row_id'] == prompt_id
    assert raced['messages'][-1]['content'].startswith('expanded:')
    assert raced_bytes < 8 * 1024 * 1024, (raced_bytes, fresh_bytes)
