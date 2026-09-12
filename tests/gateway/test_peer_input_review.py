"""Independent ordinary HTTP/SQLite input adapter boundary checks."""
import asyncio
import hashlib

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_canonical_peer_inputs import grant_for
from tests.gateway.test_roomlink_input_spool import _dispatch, _manifest


@pytest.mark.asyncio
async def test_staging_must_obey_source_route_preflight(api, owner):
    from gateway import hosted_rooms
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.platforms.api_server_room_attachments import _http_routes, _default_spool
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    from tui_gateway.hosted_room_peer_status import _RouteStatusPeerClient

    install = hosted_rooms.local_authority_gateway_id()
    policy, catalog = _local_room_catalog(api, 'default', install)
    manifest = _manifest()
    dispatch = _dispatch(manifest, target_install_id=install, capability_digest=catalog['catalog_digest'],
                         execution_policy_digest=policy['policy_digest'])
    grant = grant_for(api, dispatch)
    checked = []

    def refuse_changed_source_route(token):
        checked.append(token)
        raise RuntimeError('source route no longer current')

    app = web.Application()
    for method, path, handler in _http_routes(api):
        app.router.add_route(method, path, handler)
    async with TestClient(TestServer(app)) as http:
        raw = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='')
        tracked = _RouteStatusPeerClient(raw, grant=grant, before_admission=refuse_changed_source_route,
            capability_digest=catalog['catalog_digest'], execution_policy_digest=policy['policy_digest'],
            on_ready=lambda **kwargs: None, on_reauthorization=lambda **kwargs: None,
            on_unavailable=lambda **kwargs: None, on_refreshed=lambda *args, **kwargs: None)
        # Positive control: the same retained guard does protect dispatch.
        with pytest.raises(RuntimeError, match='source route no longer current'):
            await asyncio.to_thread(tracked.dispatch, dispatch=dispatch.as_mapping(), grant=grant)
        checked.clear()
        denied = False
        try:
            await asyncio.to_thread(tracked.stage_attachments, dispatch=dispatch.as_mapping(),
                attachments=[{**manifest[0], 'data': b'hello'}], grant=grant)
        except RuntimeError as exc:
            assert 'source route no longer current' in str(exc)
            denied = True
        reached_target = False
        try:
            reached_target = bool(_default_spool().require_complete(dispatch))
        except ValueError:
            pass
        assert denied and checked and not reached_target, {
            'rejected': denied, 'source_preflight_calls': len(checked), 'target_has_bytes': reached_target}


@pytest.mark.asyncio
async def test_exact_accepted_run_retry_does_not_require_expired_spool(api, owner, tmp_path, monkeypatch):
    from gateway import hosted_rooms
    from gateway.platforms import api_server_runs, api_server_room_attachments
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.platforms.api_server_authority_runs import run_admission
    from gateway.session_contract import SessionRef
    from gateway.session_api_turn import prepare_api_execution
    from gateway.hosted_room_peer import issue_room_grant, decode_room_grant
    from gateway.hosted_room_grant_state import reserve_grant_state, grant_state_db_paths
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    import time

    owner.profile_id = str(tmp_path)
    install = hosted_rooms.local_authority_gateway_id()
    policy, catalog = _local_room_catalog(api, 'default', install)
    manifest = _manifest()
    dispatch = _dispatch(manifest, target_install_id=install, capability_digest=catalog['catalog_digest'],
                         execution_policy_digest=policy['policy_digest'])
    start = time.time()
    monkeypatch.setattr(time, 'time', lambda: start)
    monkeypatch.setattr(api_server_room_attachments._default_spool(), 'clock', lambda: time.time())
    fields = ('room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch',
              'member_id', 'target_install_id', 'target_profile', 'execution_policy_digest')
    grant = issue_room_grant(api._room_grant_secret(), grant_id='input-lifetime',
        **{key: getattr(dispatch, key) for key in fields}, ttl_seconds=86400, status_ttl_seconds=3 * 86400)
    claims = decode_room_grant(api._room_grant_secret(), grant, permission='status')
    reserve_grant_state(grant_state_db_paths(), claims=claims, expires_at=claims['status_expires_at'])
    async def queued(*args, **kwargs):
        pass
    monkeypatch.setattr(api_server_runs, '_execute_run', queued)
    app = web.Application()
    for method, path, handler in api._http_route_table():
        app.router.add_route(method, path, handler)
    headers = {'Authorization': 'HermesRoom ' + grant,
               'Idempotency-Key': f'room:{dispatch.task_id}:{dispatch.execution_generation}'}
    body = {'input': dispatch.prompt, 'hosted_room_dispatch': dispatch.as_mapping()}
    async with TestClient(TestServer(app)) as http:
        client = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='')
        await asyncio.to_thread(client.stage_attachments, dispatch=dispatch.as_mapping(),
            attachments=[{**manifest[0], 'data': b'hello'}], grant=grant)
        response = await http.post('/v1/runs', headers=headers, json=body)
        assert response.status == 202, await response.text()
        first = await response.json()
        # Ordinary clock advancement, with a still-valid observation horizon.
        monkeypatch.setattr(time, 'time', lambda: start + 23 * 3600)
        refresh = await http.post('/v1/room-members/grants/refresh',
            headers={'Authorization': 'HermesRoom ' + grant}, json={})
        assert refresh.status == 200, await refresh.text()
        renewed = await refresh.json()
        headers['Authorization'] = 'HermesRoom ' + renewed['grant']
        monkeypatch.setattr(time, 'time', lambda: start + api_server_room_attachments.SPOOL_TTL_SECONDS + 1)
        api_server_room_attachments._default_spool().prune()
        _, row = run_admission(api, first['run_id'])
        prepared = prepare_api_execution(owner, SessionRef(owner.profile_id, row['target_session_id']), row['payload'])
        assert hashlib.sha256(b'hello').hexdigest() in prepared['content']
        observed = await http.get('/v1/runs/' + first['run_id'],
            headers={'Authorization': headers['Authorization']})
        assert observed.status == 200, await observed.text()
        assert (await observed.json())['run_id'] == first['run_id']
        repeated = await http.post('/v1/runs', headers=headers, json=body)
        repeat_body = await repeated.json()
        assert repeated.status == 202, repeat_body
        assert repeat_body['run_id'] == first['run_id'] and repeat_body['replayed'] is True
