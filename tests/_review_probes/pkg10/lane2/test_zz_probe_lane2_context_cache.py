"""Review probe (lane 2, G4): the per-revision context-breakdown cache vs an in-place row rewrite.

``conversation_revision`` keys on (lineage ids, COUNT(*), MAX(id)) of active rows. The turn
prologue rewrites an already-written user row in place (``SessionDB.set_user_message_content``:
@-file expansion, multimodal context; ``agent/turn_context.py::_append_multimodal_context``),
which moves neither value. A breakdown cached before that rewrite keeps the old figures.
"""
from types import SimpleNamespace

import pytest

from tests.gateway.test_tui_session_reads import owner  # noqa: F401  (fixture)


def _patch_compute(monkeypatch, computed):
    from agent import context_breakdown, context_file_sources

    def compute(agent, messages):
        chars = sum(len(str(m.get('content') or '')) for m in messages)
        computed.append(chars)
        return {'categories': [{'id': 'conversation', 'label': 'Conversation', 'tokens': chars,
                                'color': '#fff'}], 'context_max': 128000, 'context_used': chars,
                'context_percent': 0, 'estimated_total': chars, 'context_estimated': True,
                'context_source': 'local_estimate', 'model': 'fake-model'}
    monkeypatch.setattr(context_breakdown, 'compute_session_context_breakdown', compute)
    monkeypatch.setattr(context_file_sources, 'context_file_sources_for_agent', lambda a: [])


@pytest.mark.asyncio
async def test_context_breakdown_follows_an_in_place_user_row_rewrite(owner, monkeypatch):  # noqa: F811
    sid = (await owner.call('session.create', request_id='r', source='cli', cwd=str(owner.workdir)))['session_id']
    agent = SimpleNamespace(model='fake-model', provider='custom')
    owner.authority.runner._cached_agent_for = lambda route: agent
    computed = []
    _patch_compute(monkeypatch, computed)
    from gateway.session_tui_reads import _stored_id
    stored = _stored_id(owner.authority, SimpleNamespace(session_id=sid))
    db = owner.authority.db
    row_id = db.append_message(stored, 'user', 'explain @file:big.py')
    before = await owner.call('session.context_breakdown', session_id=sid)
    # The prologue's in-place rewrite of that same active row (same id, same count).
    expanded = 'explain @file:big.py\n' + 'x' * 20000
    assert db.set_user_message_content(stored, row_id, expanded) == 1
    assert db.get_messages_as_conversation(stored, include_ancestors=True)[-1]['content'] == expanded
    after = await owner.call('session.context_breakdown', session_id=sid)
    assert after['estimated_total'] > before['estimated_total'], (
        f'cached breakdown survived an in-place rewrite: before={before["estimated_total"]} '
        f'after={after["estimated_total"]} computed={computed}')
