"""Probe (lane 4a review, G3): after a ROTATED compression whose child carries head and tail copies of
parent rows, the live (non-archived) rows of the session.resume snapshot must keep stored order.

Real authority, real SQLite, real ``publish_compression_child``; same fixture shape as
tests/gateway/test_local_resume_compression_child.py.
"""
import pytest

from tests.gateway.test_local_resume_compression_child import _resume, _runner


def _summary():
    from agent.context_compressor import COMPRESSED_SUMMARY_METADATA_KEY, SUMMARY_PREFIX, _SUMMARY_END_MARKER
    return {'role': 'user', 'content': SUMMARY_PREFIX + ' summary of Q1..Q2 ' + _SUMMARY_END_MARKER,
            COMPRESSED_SUMMARY_METADATA_KEY: True}


async def _rotated(tmp_path, monkeypatch, *, head=True):
    from gateway import run
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from gateway.session_local import create_local_session
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    authority = await initialize_session_authority(_runner(tmp_path), profile_id='fixture', instance_id='first')
    actor = Principal('owner', 'fixture', frozenset({'session:create', 'session:read'}), 'socket')
    ref = create_local_session(authority, actor, dict(request_id='probe', source='gui', cwd=str(tmp_path),
                                                      model='frozen', toolsets=[]))
    db = authority.db
    live = authority.sessions[ref.session_id]
    store = authority.runner.session_store
    parent = ref.session_id
    turns = [('user', 'Q1'), ('assistant', 'A1'), ('user', 'Q2'), ('assistant', 'A2'),
             ('user', 'Q3'), ('assistant', 'A3')]
    db.append_messages_batch(parent, [{'role': r, 'content': c} for r, c in turns])
    store._record_gateway_session_peer(parent, live.route, live.source)
    # The compressor's shape: protected head + standalone summary (user: head ends on assistant)
    # + verbatim tail starting on an assistant row. Copies carry no timestamp, so the publish
    # adopts the parent rows' timestamps (display identity).
    compressed = ([{'role': 'user', 'content': 'Q1'}, {'role': 'assistant', 'content': 'A1'}] if head else []) + [
        _summary(),
        {'role': 'assistant', 'content': 'A2'}, {'role': 'user', 'content': 'Q3'},
        {'role': 'assistant', 'content': 'A3'}]
    assert db.try_acquire_compression_lock(parent, 'fixture')
    try:
        db.publish_compression_child(parent_session_id=parent, child_session_id='child-1',
                                     source=db.get_session(parent)['source'], messages=compressed,
                                     compression_lock_holder='fixture')
    finally:
        db.release_compression_lock(parent, 'fixture')
    store._record_gateway_session_peer('child-1', live.route, live.source)
    return authority, ref


@pytest.mark.asyncio
@pytest.mark.parametrize('head', [True, False])
async def test_live_rows_of_resume_keep_stored_order_after_rotation(tmp_path, monkeypatch, head):
    authority, ref = await _rotated(tmp_path, monkeypatch, head=head)
    stored = [m['content'] for m in authority.db.get_messages('child-1')]
    reply = await _resume(authority, 'owner', ref.session_id)
    assert 'result' in reply, reply
    rows = reply['result']['messages']
    live = [m for m in rows if not m.get('archived')]
    # What the CLI keeps as conversation_history after /compress, /rollback (gateway_agent.py:504,
    # gateway_rollback.py:81) and what rewind_user_turn indexes user ordinals in.
    assert [m['content'] for m in live] == stored, [(m['content'], m.get('row_id'), m.get('archived')) for m in rows]


@pytest.mark.asyncio
async def test_cli_undo_prefix_keeps_the_summary(tmp_path, monkeypatch):
    """``GatewaySessionStore.rewind_user_turn`` without a warm view: the prefix before the newest user
    turn (Q3) must still hold the compaction summary the store keeps active."""
    authority, ref = await _rotated(tmp_path, monkeypatch)
    reply = await _resume(authority, 'owner', ref.session_id)
    from agent.context_compressor import history_before_user_originated_turn
    from hermes_state_rewind import _user_indices
    durable = [m for m in reply['result']['messages'] if not m.get('archived')]
    users = _user_indices(durable)
    prefix, live_view = history_before_user_originated_turn(durable, users[-1])
    assert live_view['content'] == 'Q3'
    assert any('[CONTEXT COMPACTION' in str(m.get('content')) for m in prefix), [m['content'][:20] for m in prefix]


@pytest.mark.asyncio
async def test_in_place_compaction_keeps_stored_order_control(tmp_path, monkeypatch):
    """Control: the same head + summary + tail shape compacted IN PLACE keeps stored order (the carried
    originals are removed rows, so they never set the logical message's first position)."""
    from gateway import run
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from gateway.session_local import create_local_session
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    authority = await initialize_session_authority(_runner(tmp_path), profile_id='fixture', instance_id='first')
    actor = Principal('owner', 'fixture', frozenset({'session:create', 'session:read'}), 'socket')
    ref = create_local_session(authority, actor, dict(request_id='probe', source='gui', cwd=str(tmp_path),
                                                      model='frozen', toolsets=[]))
    db = authority.db
    turns = [('user', 'Q1'), ('assistant', 'A1'), ('user', 'Q2'), ('assistant', 'A2'),
             ('user', 'Q3'), ('assistant', 'A3')]
    db.append_messages_batch(ref.session_id, [{'role': r, 'content': c} for r, c in turns])
    db.archive_and_compact(ref.session_id, [{'role': 'user', 'content': 'Q1'}, {'role': 'assistant', 'content': 'A1'},
                                            _summary(), {'role': 'assistant', 'content': 'A2'},
                                            {'role': 'user', 'content': 'Q3'}, {'role': 'assistant', 'content': 'A3'}],
                           tail_count=3)
    stored = [m['content'] for m in db.get_messages(ref.session_id)]
    reply = await _resume(authority, 'owner', ref.session_id)
    live = [m['content'] for m in reply['result']['messages'] if not m.get('archived')]
    assert live == stored
