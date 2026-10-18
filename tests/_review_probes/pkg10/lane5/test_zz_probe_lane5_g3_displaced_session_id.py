"""G3 probe: after MSG-21 moves an API-held Bot Chat title aside, the peer DM / peer run reply
names the chat that received the message (the minted local Bot Chat), not the displaced API row.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from tests.waiting import async_wait_until

AUTHOR = {"id": "bot:cto", "name": "cto", "is_bot": True}


@pytest_asyncio.fixture
async def rig(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from hermes_state import SessionDB

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config',
                        lambda *_path: {'platform_toolsets': {'cli': []}, 'model': {'default': 'fixture'}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    store._db = SessionDB(db_path=tmp_path / 'state.db')
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='fixture')
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter.gateway_runner = runner
    adapter._session_db = authority.db
    runner.adapters[Platform.API_SERVER] = adapter
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    executed = []

    async def execute(authority, ref, row):
        executed.append((ref.session_id, row['payload']['text']))
        return 'pong'

    monkeypatch.setattr('gateway.session_finite.execute_finite_admission', execute)
    app = web.Application()
    app.router.add_post("/api/sessions/{session_id}/chat", adapter._handle_session_chat)
    app.router.add_post("/v1/runs", adapter._handle_runs)
    app.router.add_get("/v1/runs/{run_id}", adapter._handle_get_run)
    try:
        async with TestClient(TestServer(app)) as cli:
            yield SimpleNamespace(cli=cli, authority=authority, executed=executed)
    finally:
        adapter._response_store.close()
        adapter._run_idempotency_store.close()
        store._db.close()


def _api_bot_chat(db, sid='api_1_stale'):
    """A canonical Bot Chat an older gateway minted through the plain API create (#140769)."""
    db.create_session(sid, 'api_server')
    db.set_session_title(sid, 'Bot Chat')
    db.append_message(sid, 'user', 'old history')
    return sid


@pytest.mark.asyncio
async def test_peer_dm_reply_names_the_chat_that_received_it(rig):
    db = rig.authority.db
    old = _api_bot_chat(db)
    resp = await rig.cli.post(f"/api/sessions/{old}/chat", json={"message": "one", "author": AUTHOR})
    body = await resp.json()
    assert resp.status == 200, body
    new = db.get_session_by_title('Bot Chat')['id']
    assert new != old and rig.executed == [(new, 'one')]
    # The message ran in ``new``; the reply must say so.
    assert (body['session_id'], resp.headers.get('X-Hermes-Session-Id')) == (new, new), (old, new, body)


@pytest.mark.asyncio
async def test_peer_run_record_names_the_chat_that_received_it(rig):
    db = rig.authority.db
    old = _api_bot_chat(db)
    resp = await rig.cli.post("/v1/runs", json={"input": "one", "session_id": old, "author": AUTHOR})
    assert resp.status in (200, 202), await resp.text()
    run_id = (await resp.json())['run_id']
    import asyncio
    import time
    final, deadline = {}, time.monotonic() + 15
    while time.monotonic() < deadline:
        final = await (await rig.cli.get(f"/v1/runs/{run_id}")).json()
        if final.get('status') in ('completed', 'failed', 'cancelled'):
            break
        await asyncio.sleep(0.05)
    new = db.get_session_by_title('Bot Chat')['id']
    assert final['status'] == 'completed' and rig.executed == [(new, 'one')], (final, rig.executed)
    assert final.get('session_id') == new, (old, new, final)


@pytest.mark.asyncio
async def test_continuing_with_the_returned_id_reaches_the_bot_chat(rig, monkeypatch):
    """A client that continues with the id the reply named must reach the chat it talked to."""
    db = rig.authority.db
    old = _api_bot_chat(db)
    first = await (await rig.cli.post(f"/api/sessions/{old}/chat", json={"message": "one", "author": AUTHOR})).json()
    new = db.get_session_by_title('Bot Chat')['id']
    ran_as_api = []

    async def api_turn(self, **kwargs):
        ran_as_api.append(kwargs.get('session_id'))
        return {'final_response': 'api reply', 'session_id': kwargs.get('session_id')}, {}
    monkeypatch.setattr(APIServerAdapter, '_run_agent', api_turn)
    resp = await rig.cli.post(f"/api/sessions/{first['session_id']}/chat", json={"message": "two", "author": AUTHOR})
    assert resp.status == 200, await resp.text()
    assert (ran_as_api, [t for _, t in rig.executed]) == ([], ['one', 'two']), (old, new, ran_as_api, rig.executed)


@pytest.mark.asyncio
async def test_b3_refusal_never_promises_a_move_that_cannot_happen(rig):
    """A Desktop Bot Chat a served remote login stamped (``github:alice``) is local history the authority
    never moves (``displaceable_bot_chat``: LEGACY_LOCAL_SOURCES). The peer's refusal must not tell it the
    chat "is moved aside on the next delivery": every resend is refused the same way."""
    db = rig.authority.db
    db.create_session('desk_alice', 'desktop', user_id='github:alice')
    db.set_session_title('desk_alice', 'Bot Chat')
    bodies = []
    for text in ('one', 'two'):
        resp = await rig.cli.post("/api/sessions/desk_alice/chat", json={"message": text, "author": AUTHOR})
        bodies.append((resp.status, (await resp.json())['error']))
    assert db.get_session_by_title('Bot Chat')['id'] == 'desk_alice'  # never moved
    assert [status for status, _ in bodies] == [502, 502], bodies
    assert not any('moved aside' in err['message'] for _, err in bodies), bodies


@pytest.mark.asyncio
async def test_b4_two_concurrent_peer_dms_both_land_in_the_bot_chat(rig, monkeypatch):
    """Two peers DM the API-held Bot Chat at once (both resolved its id by title first). Both messages
    belong in the one Bot Chat the owner mints; none may run as an API turn on the moved-aside row."""
    import asyncio
    db = rig.authority.db
    old = _api_bot_chat(db)
    ran_as_api = []

    async def api_turn(self, **kwargs):
        ran_as_api.append(kwargs.get('session_id'))
        return {'final_response': 'api reply', 'session_id': kwargs.get('session_id')}, {}
    monkeypatch.setattr(APIServerAdapter, '_run_agent', api_turn)
    responses = await asyncio.gather(*(rig.cli.post(f"/api/sessions/{old}/chat",
                                                    json={"message": text, "author": AUTHOR})
                                       for text in ('one', 'two')))
    statuses = [r.status for r in responses]
    assert (ran_as_api, sorted(t for _, t in rig.executed)) == ([], ['one', 'two']), (statuses, ran_as_api, rig.executed)
