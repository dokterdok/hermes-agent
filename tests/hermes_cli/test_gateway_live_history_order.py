"""The classic CLI's working history after a rotated compression keeps stored order, summary included.

``session.resume`` answers in display order, which shows a message the compression child carried at its
first copy's place. With a protected head, the child's summary sits between carried head and tail rows, so
in display order it comes last. The CLI's ``conversation_history`` (after ``/compress`` and ``/rollback``)
and its ``/undo`` prefix are the live rows in stored order. The snapshot here is the real reply (store,
authority, ``publish_compression_child``); the CLI side talks to a scripted gateway that serves it.
"""
import asyncio
import types
from contextlib import asynccontextmanager

import pytest

from hermes_cli.gateway_connection import GatewayConnection
from tests.gateway.test_resume_display_history import SUMMARY, _append, _local_session, _resume

TURNS = ('Q1', 'A1', 'Q2', 'A2', 'Q3', 'A3')
# The compressor's shape: protected head + standalone summary + verbatim tail.
COMPRESSED = [('user', 'Q1'), ('assistant', 'A1'), ('user', SUMMARY), ('assistant', 'A2'),
              ('user', 'Q3'), ('assistant', 'A3')]
STORED = [text for _, text in COMPRESSED]


async def _rotated_snapshot(tmp_path, monkeypatch):
    authority, actor, ref, physical = await _local_session(tmp_path, monkeypatch)
    db = authority.db
    _append(db, physical, *TURNS)
    assert db.try_acquire_compression_lock(physical, 'fixture')
    try:
        db.publish_compression_child(parent_session_id=physical, child_session_id='child', source='gui',
                                     messages=[{'role': r, 'content': c} for r, c in COMPRESSED],
                                     compression_lock_holder='fixture')
    finally:
        db.release_compression_lock(physical, 'fixture')
    assert [m['content'] for m in db.get_messages('child')] == STORED
    snapshot = await _resume(authority, actor, ref)
    live = [m['content'] for m in snapshot['messages'] if not m.get('archived')]
    assert live != STORED  # the premise: display order moved the summary after the carried tail
    return {**snapshot, 'execution_generation': 0}


class _Gateway:
    def __init__(self, snapshot):
        self.events = asyncio.Queue()
        self.snapshot = snapshot
        self.calls = []

    async def rpc(self, method, *, _timeout=None, **params):
        self.calls.append((method, params))
        if method == 'session.resume':
            return {**self.snapshot, 'session_id': params['session_id']}
        if method == 'session.mutate' and params['operation'] == 'rewind':
            return {'rewound_count': 2, 'target_message': {'role': 'user', 'content': 'Q3'}}
        if method == 'rollback.restore':
            return {'success': True, 'restored_to': 'abc1234', 'reason': 'before write_file', 'history_removed': 2}
        return {'status': 'ok'}


@pytest.fixture
def connection():
    made = []

    def build(snapshot):
        gateway = _Gateway(snapshot)

        @asynccontextmanager
        async def connect():
            yield gateway
        conn = GatewayConnection(connect=connect)
        conn.start(5)
        made.append(conn)
        return conn, gateway
    yield build
    for conn in made:
        conn.close()


@pytest.mark.asyncio
async def test_rollback_history_is_the_stored_transcript(tmp_path, monkeypatch, connection):
    from hermes_cli import gateway_rollback
    conn, _ = connection(await _rotated_snapshot(tmp_path, monkeypatch))
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    cli = types.SimpleNamespace(session_id='local-rb', conversation_history=[])
    await asyncio.to_thread(gateway_rollback.rollback, cli, conn, '/rollback 1')
    assert [m['content'] for m in cli.conversation_history] == STORED


@pytest.mark.asyncio
async def test_undo_keeps_the_summary_in_its_prefix(tmp_path, monkeypatch, connection):
    from hermes_cli.gateway_session_store import GatewaySessionStore
    snapshot = await _rotated_snapshot(tmp_path, monkeypatch)
    conn, gateway = connection(snapshot)
    q3 = next(m['row_id'] for m in snapshot['messages'] if m['content'] == 'Q3' and not m.get('archived'))
    outcome = await asyncio.to_thread(GatewaySessionStore(conn).rewind_user_turn, 'local-x', -1)
    assert [m['content'] for m in outcome.prefix] == ['Q1', 'A1', SUMMARY, 'A2']
    assert [p for m, p in gateway.calls if m == 'session.mutate'][-1]['payload'] == {'target_message_id': q3}


@pytest.mark.asyncio
async def test_compress_returns_the_stored_transcript(tmp_path, monkeypatch, connection):
    from hermes_cli.gateway_agent import GatewayAgent
    conn, _ = connection(await _rotated_snapshot(tmp_path, monkeypatch))
    conn.state('local-x')
    agent = GatewayAgent(connection=conn, session_id='local-x')
    history, _ = await asyncio.to_thread(agent._compress_context, [], None)
    assert [m['content'] for m in history] == STORED
