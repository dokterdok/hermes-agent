"""Independent lane4a review: real store/authority, deterministic concurrency barriers."""
import asyncio
import json
import tracemalloc
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB


async def local(tmp_path, monkeypatch):
    from gateway import run, session_local
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', dict)
    monkeypatch.setattr(session_local, 'local_session_info', lambda *args: {})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    evictions = []
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
        _cached_agent_for=lambda *a, **kw: None, _evict_cached_agent=evictions.append)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='first')
    actor = Principal('owner', 'fixture', frozenset({'session:create', 'session:read', 'session:control'}), 'sock')
    ref = session_local.create_local_session(authority, actor, dict(
        request_id='review', source='gui', cwd=str(tmp_path), model='frozen', toolsets=[]))
    return authority, actor, ref, evictions


def append(db, sid, *texts):
    for role, content in zip(('user', 'assistant') * len(texts), texts):
        db.append_message(sid, role, content)


def rotate(db, sid):
    from agent.context_compressor import SUMMARY_PREFIX
    summary = SUMMARY_PREFIX + '\nsummary of earlier turns'
    assert db.try_acquire_compression_lock(sid, 'review')
    try:
        db.publish_compression_child(parent_session_id=sid, child_session_id='child', source='gui',
            messages=[{'role': 'user', 'content': summary}, {'role': 'user', 'content': 'TAIL_Q'},
                      {'role': 'assistant', 'content': 'TAIL_A'}], compression_lock_holder='review')
    finally:
        db.release_compression_lock(sid, 'review')
    return summary


def connection(authority, actor):
    from gateway.session_controls import AuthorityConnection
    conn = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    conn.actor = actor
    return conn


def mutation(authority, sid, operation, payload):
    row = authority.db.get_session(sid)
    return dict(session_id=sid, request_id='review-' + operation, operation=operation, payload=payload,
                expected_revision=row['runtime_revision'], expected_generation=row['runtime_generation'])


def test_g1_removed_tool_copies_do_not_dominate_display_read_memory(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.create_session('s', source='gui')
        db.append_message('s', 'user', 'question')
        db.append_message('s', 'assistant', '', tool_calls=[
            {'id': 'tool-1', 'type': 'function', 'function': {'name': 'terminal', 'arguments': '{}'}}])
        db.append_message('s', 'tool', 'x' * (512 * 1024), tool_call_id='tool-1')
        for n in range(32):
            tail = db.get_messages_as_conversation('s', include_row_ids=True)[-2:]
            db.archive_and_compact('s', [{'role': 'user', 'content': f'summary {n}'}, *tail], tail_count=2)
        assert db.assert_resume_safe('s', max_messages=40) <= 40
        tracemalloc.start()
        try:
            rows = db.get_messages_as_conversation('s', include_ancestors=True, include_compacted=True)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert sum(len(str(r.get('content', ''))) for r in rows) < 1024 * 1024
        assert peak < 8 * 1024 * 1024, f'display read allocated {peak:,} bytes for <1 MiB visible history'


@pytest.mark.asyncio
async def test_g2_concurrent_append_keeps_resume_under_frame_limit(tmp_path, monkeypatch):
    import gateway.session_local_recovery as recovery
    authority, actor, ref, _ = await local(tmp_path, monkeypatch)
    db = authority.db
    for n in range(34):
        append(db, ref.session_id, f'OLD{n} ' + 'x' * 100_000, f'answer {n}')
    append(db, ref.session_id, 'TAIL_Q', 'TAIL_A')
    rotate(db, ref.session_id)
    ready, release = asyncio.Event(), asyncio.Event()
    if hasattr(recovery, 'prepare_history'):
        original = recovery.prepare_history
        async def paused(*args):
            prepared = await original(*args)
            ready.set()
            await release.wait()
            return prepared
        monkeypatch.setattr(recovery, 'prepare_history', paused)
    else:
        # Main has no off-loop preparation window; the new message is already live when it reads.
        append(db, 'child', 'NEW ' + 'y' * (2 * 1024 * 1024))
        ready.set()
    conn = connection(authority, actor)
    task = asyncio.create_task(conn.resume(ref, {'session_id': ref.session_id, 'display_projection': True}))
    await asyncio.wait_for(ready.wait(), 20)
    if hasattr(recovery, 'prepare_history'):
        append(db, 'child', 'NEW ' + 'y' * (2 * 1024 * 1024))
    release.set()
    reply = await task
    size = len(json.dumps(reply).encode())
    assert any(m['content'].startswith('NEW ') for m in reply['messages'])
    assert size < 8 * 1024 * 1024, f'resume frame is {size:,} bytes'


@pytest.mark.asyncio
async def test_g3_cli_rewind_returns_the_summary_retained_by_store(tmp_path, monkeypatch):
    from hermes_cli.gateway_session_store import GatewaySessionStore
    from gateway.session_mutations import mutate_session
    authority, actor, ref, _ = await local(tmp_path, monkeypatch)
    append(authority.db, ref.session_id, 'OLD_Q', 'OLD_A', 'TAIL_Q', 'TAIL_A')
    summary = rotate(authority.db, ref.session_id)
    conn = connection(authority, actor)
    loop = asyncio.get_running_loop()
    async def rpc(method, params):
        if method == 'session.resume':
            return await conn.resume(ref, params)
        assert method == 'session.mutate'
        return await mutate_session(authority, actor, ref, params)
    def call(method, **params):
        return asyncio.run_coroutine_threadsafe(rpc(method, params), loop).result(timeout=20)
    store = GatewaySessionStore(SimpleNamespace(call=call))
    outcome = await asyncio.to_thread(store.rewind_user_turn, ref.session_id, -1)
    durable = authority.db.get_messages_as_conversation('child')
    assert [m['content'] for m in durable] == [summary]
    assert [m['content'] for m in outcome.prefix] == [summary], 'CLI lost the summary while the gateway retained it'


@pytest.mark.asyncio
async def test_g4_delete_during_attach_returns_named_refusal(tmp_path, monkeypatch):
    import gateway.session_local_recovery as recovery
    from gateway.session_mutations import mutate_session
    from hermes_state_runtime import RuntimeStoreError
    authority, actor, ref, _ = await local(tmp_path, monkeypatch)
    append(authority.db, ref.session_id, 'question', 'answer')
    ready, release = asyncio.Event(), asyncio.Event()
    if hasattr(recovery, 'prepare_history'):
        original = recovery.prepare_history
        async def paused(*args):
            prepared = await original(*args)
            ready.set()
            await release.wait()
            return prepared
        monkeypatch.setattr(recovery, 'prepare_history', paused)
    else:
        asyncio.get_running_loop().call_soon(ready.set)
    task = asyncio.create_task(authority.attach(actor, ref))
    await asyncio.wait_for(ready.wait(), 20)
    await mutate_session(authority, actor, ref, mutation(authority, ref.session_id, 'delete', {}))
    release.set()
    try:
        await task
    except RuntimeStoreError as exc:
        assert exc.reason == 'not_found'


@pytest.mark.asyncio
@pytest.mark.parametrize('busy', [False, True], ids=['idle-cache', 'started-owner'])
async def test_child_rewind_preserves_owner_fences_and_cache(tmp_path, monkeypatch, busy):
    from gateway.session_contract import SessionRef
    from gateway.session_mutations import mutate_session
    import hermes_state_runtime as rt
    authority, actor, ref, evictions = await local(tmp_path, monkeypatch)
    append(authority.db, ref.session_id, 'OLD_Q', 'OLD_A', 'TAIL_Q', 'TAIL_A')
    rotate(authority.db, ref.session_id)
    target = next(m['id'] for m in authority.db.get_messages('child') if m['content'] == 'TAIL_Q')
    if busy:
        rt.admit_session_input(authority.db, epoch=authority.epoch, principal_id=actor.subject,
            session_id=ref.session_id, request_id='running', payload={'text': 'next turn'})
        rt.claim_session_input(authority.db, epoch=authority.epoch, session_id=ref.session_id)
    before = authority.db.get_active_message_ids('child')
    try:
        await mutate_session(authority, actor, SessionRef('fixture', 'child'),
            mutation(authority, 'child', 'rewind', {'target_message_id': target}))
    except rt.RuntimeStoreError:
        # A named refusal is also safe: no physical transcript mutation may escape owner fencing.
        assert authority.db.get_active_message_ids('child') == before
        return
    if busy:
        assert authority.db.get_active_message_ids('child') == before, 'rewind changed a started owner transcript'
    else:
        assert evictions == [authority.sessions[ref.session_id].route], 'rewind did not evict the owner cached agent'
