"""Probe (lane 4a review, G2): a live row appended between ``prepare_history`` (off-loop) and the locked
``local_history`` keeps the prepared archive budget, so the archived rows no longer fit "what the live
rows leave" of RESUME_HISTORY_BYTES and an Ink-style resume (``display_projection``) passes the clients'
8 MiB frame although a fresh read of the same state fits.

Real store, authority, local session and ``AuthorityConnection.resume`` (fixture shape of
tests/gateway/test_resume_display_history.py); the concurrent append is a real ``append_message``.
"""
import json
from types import SimpleNamespace

import pytest

FRAME = 8 * 1024 * 1024


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


async def _resume(authority, actor, ref, **params):
    from gateway.session_controls import AuthorityConnection
    conn = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    conn.actor = actor
    authority.events[actor.transport_id] = object()
    return await conn.resume(ref, {'session_id': ref.session_id, **params})


@pytest.mark.asyncio
@pytest.mark.parametrize('projection', [True, False], ids=['ink_display_projection', 'python_client'])
async def test_a_row_appended_during_prepare_does_not_overflow_the_frame(tmp_path, monkeypatch, projection):
    authority, actor, ref, physical = await _local_session(tmp_path, monkeypatch)
    db = authority.db
    chunk = 'h' * (100 * 1024)
    for i in range(28):  # ~2.8 MiB of history, then summarized away in place (all archived)
        db.append_message(physical, 'user' if i % 2 == 0 else 'assistant', f'{i}:{chunk}')
    db.archive_and_compact(physical, [{'role': 'user', 'content': '[CONTEXT SUMMARY] earlier turns'}])
    big = 'n' * (2 * 1024 * 1024)

    from gateway import session_local_recovery as recovery
    real = recovery.prepare_history
    raced = {'done': False}

    async def prepare_then_append(authority_, ref_):
        prepared = await real(authority_, ref_)
        if not raced['done']:
            raced['done'] = True
            # A running turn (another viewer's, or a tool result) lands while this resume prepared.
            db.append_message(physical, 'assistant', big)
        return prepared
    monkeypatch.setattr(recovery, 'prepare_history', prepare_then_append)
    reply = await _resume(authority, actor, ref, display_projection=projection)
    assert raced['done']
    raced_size = len(json.dumps(reply, default=str))
    fresh = await _resume(authority, actor, ref, display_projection=projection)
    fresh_size = len(json.dumps(fresh, default=str))
    print(f'projection={projection} raced={raced_size} fresh={fresh_size}' + chr(10), end='')
    assert fresh_size < FRAME, fresh_size  # the same state, read without the race, fits
    assert raced_size < FRAME, (raced_size, fresh_size)
