"""Probe (lane 4a review, sibling path of RT-39): the owner's whole-tree /rollback history rewind
(``gateway/session_tui_ops.py::_rewind_last_turn``) picks the newest user turn of ``local_history``.
That history now carries archived rows; with no live user turn left (the last one undone after a
compression) it must rewind nothing, not aim the owner's rewind at an archived row the store refuses
(the files are already restored when this runs, so the RPC would report a failed rollback).

Same scenario as David's follow-up commit 84527993e2 (not salvaged into lane 4a). Real store,
authority, local session, compaction verbs and mutate_session.
"""
from types import SimpleNamespace

import pytest

from agent.context_compressor import SUMMARY_PREFIX

SUMMARY = SUMMARY_PREFIX + '\nearlier turns summarized'


async def _local_session(tmp_path, monkeypatch):
    from gateway import run, session_local
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from hermes_state_local import local_receipt
    monkeypatch.setattr(run, '_load_gateway_config', dict)
    monkeypatch.setattr(session_local, 'local_session_info', lambda *a: {})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda *a, **k: None, _evict_cached_agent=lambda *a, **k: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='first')
    actor = Principal('owner', 'fixture', frozenset({'session:create', 'session:read', 'session:control'}), 'sock')
    ref = session_local.create_local_session(authority, actor, dict(
        request_id='resume', source='gui', cwd=str(tmp_path), model='frozen', toolsets=[]))
    return authority, actor, ref, local_receipt(authority.db, ref.session_id)['entry']['session_id']


def _compact(db, physical, mode):
    compacted = [{'role': 'user', 'content': SUMMARY}, {'role': 'user', 'content': 'TAIL_Q'},
                 {'role': 'assistant', 'content': 'TAIL_A'}]
    if mode == 'in_place':
        db.archive_and_compact(physical, compacted, tail_count=2)
        return physical
    assert db.try_acquire_compression_lock(physical, 'fixture')
    try:
        db.publish_compression_child(parent_session_id=physical, child_session_id='child', source='gui',
                                     messages=compacted, compression_lock_holder='fixture')
    finally:
        db.release_compression_lock(physical, 'fixture')
    return 'child'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['in_place', 'rotated'])
async def test_rollback_turn_rewind_never_targets_an_archived_turn(tmp_path, monkeypatch, mode):
    from gateway.session_mutations import mutate_session
    from gateway.session_tui_ops import _rewind_last_turn
    authority, actor, ref, physical = await _local_session(tmp_path, monkeypatch)
    db = authority.db
    for role, text in (('user', 'OLD_Q'), ('assistant', 'OLD_A'), ('user', 'TAIL_Q'), ('assistant', 'TAIL_A')):
        db.append_message(physical, role, text)
    tip = _compact(db, physical, mode)
    handle = authority._handle(ref)
    await mutate_session(authority, actor, ref, {
        'session_id': ref.session_id, 'request_id': 'undo', 'operation': 'rewind',
        'expected_revision': handle.revision, 'expected_generation': handle.execution_generation,
        'payload': {'target_message_id': next(m['id'] for m in db.get_messages(tip) if m['content'] == 'TAIL_Q')}})
    live = [m['content'] for m in db.get_messages(tip)]
    assert await _rewind_last_turn(SimpleNamespace(authority=authority, actor=actor), ref) == 0
    assert [m['content'] for m in db.get_messages(tip)] == live == [SUMMARY]
