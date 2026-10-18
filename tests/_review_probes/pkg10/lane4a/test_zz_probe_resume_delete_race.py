"""Probe (lane 4a review, G4): ``SessionAuthority.attach`` now awaits ``prepare_history`` between
``authorize`` and ``self.sessions[ref.session_id]``. A delete committed by another connection inside
that await retires the live entry; the resume must answer a named refusal, never raise KeyError past
the dispatcher (an internal error on the wire).

Real store, authority, local session; the delete is a real ``session.mutate delete`` dispatched by a
second ``AuthorityConnection`` while the first resume is inside ``prepare_history``.
"""
from types import SimpleNamespace

import pytest


async def _local_session(tmp_path, monkeypatch):
    from gateway import run, session_local
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    monkeypatch.setattr(run, '_load_gateway_config', dict)
    monkeypatch.setattr(session_local, 'local_session_info', lambda *a: {})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda *a, **k: None, _evict_cached_agent=lambda *a, **k: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='first')
    runner.session_authority = authority  # one process owns both writes of a delete
    actor = Principal('owner', 'fixture', frozenset({'session:create', 'session:read', 'session:control'}), 'sock')
    ref = session_local.create_local_session(authority, actor, dict(
        request_id='resume', source='gui', cwd=str(tmp_path), model='frozen', toolsets=[]))
    authority.db.append_message(ref.session_id, 'user', 'hello')
    return authority, ref


def _connection(authority):
    from gateway.session_controls import AuthorityConnection
    return AuthorityConnection(authority, object(), {
        'provider': 'local', 'user_id': 'owner', 'profile_id': 'fixture', 'instance_id': authority.instance_id,
        'capabilities': ['session:create', 'session:read', 'session:control']})


@pytest.mark.asyncio
async def test_a_delete_during_resume_prepare_is_a_named_refusal(tmp_path, monkeypatch):
    authority, ref = await _local_session(tmp_path, monkeypatch)
    from gateway import session_local_recovery as recovery
    real = recovery.prepare_history
    deleted = {}

    async def prepare_then_delete(authority_, ref_):
        prepared = await real(authority_, ref_)
        if not deleted:
            row = authority.db.get_session(ref.session_id)
            other = _connection(authority)
            deleted['reply'] = await other.dispatch({'id': 9, 'method': 'session.mutate', 'params': {
                'session_id': ref.session_id, 'request_id': 'delete-1', 'expected_revision': row['runtime_revision'],
                'expected_generation': row['runtime_generation'], 'operation': 'delete', 'payload': {}}})
        return prepared
    monkeypatch.setattr(recovery, 'prepare_history', prepare_then_delete)
    viewer = _connection(authority)
    reply = await viewer.dispatch({'id': 1, 'method': 'session.resume', 'params': {'session_id': ref.session_id}})
    assert 'result' in deleted['reply'], deleted['reply']  # the delete really committed inside the window
    assert ref.session_id not in authority.sessions
    assert reply.get('error', {}).get('message') == 'not_found', reply
