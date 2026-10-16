"""``session.resume latest='cli'`` finds the CLI's own gateway sessions.

The owner stores a CLI-created session as ``source='local'`` with ``created_source='cli'``.
``resolve_latest_session`` searched ``source IN ('cli', 'oneshot')`` only, so ``-z --resume
latest``, ``chat -c --output-format stream-json`` and ``chat -c --safe-mode`` (all routed
through ``gateway_chat._resume_latest``) reported "No previous CLI session" for every session
the gateway had created.
"""
import json
from types import SimpleNamespace

import pytest

from gateway.session_policy import SURFACES


def _owner(home, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    import hermes_state_runtime as rt
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text(json.dumps({'model': {'default': 'm'}}))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(run, '_hermes_home', home)
    store = SessionStore(home / 'sessions', GatewayConfig())
    db = store._db
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                             _handle_message=None, _peek_session_state=lambda key: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(runner, profile_id='owned', instance_id='owner', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner'))
    runner.session_authority = authority
    return store, authority, AuthorityConnection(authority, object(), {'user_id': 'human'})


async def _call(owner, method, **params):
    response = await owner.dispatch({'id': params.get('request_id', method), 'method': method, 'params': params})
    if 'error' in response:
        return response['error']['data']['reason']
    return response['result']


@pytest.mark.asyncio
async def test_resume_latest_matches_the_owners_local_rows_by_creating_surface(tmp_path, monkeypatch):
    store, authority, owner = _owner(tmp_path / 'home', monkeypatch)
    try:
        created = {}
        # Desktop last, so it is the newest row: it must never be the CLI's or TUI's "latest".
        for surface in ('cli', 'tui', 'gui'):
            result = await _call(owner, 'session.create', request_id=surface, source=surface,
                                 cwd=str(tmp_path), toolsets=[], model='m')
            created[surface] = sid = result['session_id']
            # The first turn's two writers: the agent's lazy create stamps ``created_source`` with
            # the agent surface (run_agent; gui runs as ``desktop``), then the peer refresh relabels
            # the row ``local`` (session_a2a.storage_source).
            authority.db.create_session(sid, source=SURFACES[surface])
            live = authority.sessions[sid]
            store._record_gateway_session_peer(sid, live.route, live.source)
        rows = {sid: authority.db.get_session(sid) for sid in created.values()}
        assert {(row['source'], row['created_source']) for row in rows.values()} == {
            ('local', 'cli'), ('local', 'tui'), ('local', 'desktop')}
        authority.sessions.clear()

        for family in ('cli', 'tui'):
            result = await _call(owner, 'session.resume', latest=family)
            assert isinstance(result, dict) and result['session_id'] == created[family], (family, result)
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_token_accounting_before_the_agent_create_keeps_the_cli_surface(tmp_path, monkeypatch):
    """An accounting write before the agent's create must not freeze ``created_source`` as ``unknown``.

    The normal first turn stamps ``cli`` in ``create_session`` and only then refreshes the peer.
    ``update_token_counts`` can land first (#139049). Its placeholder source is ``unknown``, and
    ``created_source`` never moves once set, so ``-c`` / ``--resume latest`` then miss the row.
    """
    store, authority, owner = _owner(tmp_path / 'home', monkeypatch)
    try:
        created = await _call(owner, 'session.create', request_id='chat', source='cli',
                              cwd=str(tmp_path), toolsets=[], model='m')
        sid = created['session_id']
        authority.db.update_token_counts(sid, input_tokens=3, output_tokens=1, model='m', api_call_count=1)
        authority.db.create_session(sid, source=SURFACES['cli'])
        live = authority.sessions[sid]
        store._record_gateway_session_peer(sid, live.route, live.source)
        authority.sessions.clear()
        resumed = await _call(owner, 'session.resume', latest='cli')
        assert isinstance(resumed, dict) and resumed['session_id'] == sid, resumed
        row = authority.db.get_session(sid)
        assert (row['source'], row['created_source']) == ('local', 'cli')
    finally:
        await owner.close()
        store.close_all_db_handles()

