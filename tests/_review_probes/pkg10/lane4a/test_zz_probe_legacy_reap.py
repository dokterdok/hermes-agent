"""RT-43 sibling: a native-adopted pre-canonical owner does not start with local-."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_reap_protects_adopted_legacy_owner_after_compression(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from gateway.session_local_title import resolve_titled_session
    from gateway.session_local_recovery import reopen_local_session
    from hermes_state_runtime import admit_session_input, claim_session_input
    from tui_gateway import server

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    db.create_session('old-tui', source='tui', model='frozen', cwd=str(tmp_path))
    db.append_message('old-tui', 'user', 'old question')
    db.end_session('old-tui', 'tui_close')
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='review')
    actor = Principal('native', str(tmp_path), frozenset({'session:create', 'session:read'}), 'sock')
    authority._native_legacy_transports = {actor.transport_id: actor}
    ref = resolve_titled_session(authority, actor, 'old-tui')
    assert ref.session_id == 'old-tui'
    reopen_local_session(authority, ref)
    assert db.try_acquire_compression_lock(ref.session_id, 'review')
    try:
        db.publish_compression_child(parent_session_id=ref.session_id, child_session_id='child', source='tui',
            messages=[{'role': 'user', 'content': 'summary'}], compression_lock_holder='review')
    finally:
        db.release_compression_lock(ref.session_id, 'review')
    assert db.get_session('child')['chat_id'] == 'old-tui'
    admit_session_input(db, epoch=authority.epoch, principal_id=actor.subject,
        session_id=ref.session_id, request_id='running', payload={'text': 'continue'})
    claim_session_input(db, epoch=authority.epoch, session_id=ref.session_id)

    @contextmanager
    def session_db(_session):
        yield db
    monkeypatch.setattr(server, '_session_db', session_db)
    viewer = {'agent': SimpleNamespace(session_id='child', _end_session_on_close=True),
        'session_key': 'child', 'history': [], 'profile_home': str(tmp_path), 'source': 'tui'}
    server._finalize_session(viewer, end_reason='ws_orphan_reap')
    assert db.get_session('child')['ended_at'] is None, 'reaper ended the transcript of a started legacy owner'
