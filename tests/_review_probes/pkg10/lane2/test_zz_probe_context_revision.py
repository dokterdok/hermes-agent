"""Review G4: a persisted user-row rewrite must change the next context read."""
from types import SimpleNamespace

import pytest

from tests.gateway import test_tui_session_reads

owner = test_tui_session_reads.owner


@pytest.mark.asyncio
async def test_context_count_tracks_an_in_place_prompt_expansion(owner, monkeypatch):
    from agent import system_prompt, context_file_sources
    from gateway.session_tui_reads import _stored_id

    sid = (await owner.call('session.create', request_id='rewrite', source='cli',
                            cwd=str(owner.workdir)))['session_id']
    agent = SimpleNamespace(model='fake-model', provider='custom', tools=[])
    owner.authority.runner._cached_agent_for = lambda route: agent
    # Keep the real tokenizer, database, authorization and context cache; only the
    # unrelated system prompt and file manifest are empty in this minimal agent.
    monkeypatch.setattr(system_prompt, 'build_system_prompt_parts', lambda a: {})
    monkeypatch.setattr(context_file_sources, 'context_file_sources_for_agent', lambda a: [])
    stored = _stored_id(owner.authority, SimpleNamespace(session_id=sid))
    row = owner.authority.db.append_message(stored, 'user', '@large.txt')
    before = await owner.call('session.context_breakdown', session_id=sid)
    expanded = ('expanded file contents ' * 2000).strip()
    assert owner.authority.db.set_user_message_content(stored, row, expanded) == 1
    assert owner.authority.db.get_messages_as_conversation(stored)[-1]['content'] == expanded
    after = await owner.call('session.context_breakdown', session_id=sid)
    assert after['estimated_total'] > before['estimated_total'] + 1000, (before, after)
