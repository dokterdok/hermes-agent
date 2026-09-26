"""Loopback canonical Route admission under the registered owner.

Invitation, capability, and NEW run use the real HTTP handlers, SessionDB,
process ownership, and custody bootstrap. Downstream queue scheduling is the
only inert seam. Clearing the launch owner refuses a distinct NEW task.
Nulling the registry slot is a different failure: the logical index cannot
certify absence, so the response stays storage_unavailable.
"""
import asyncio
import hashlib

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_canonical_peer_target_setup import target  # noqa: F401


def _dispatch(catalog, task, prompt):
    from gateway.hosted_room_peer import PROTOCOL_VERSION, HostedMemberDispatch
    policy = catalog['execution_policy']
    digest = policy['policy_digest'] if isinstance(policy, dict) else policy.policy_digest
    return HostedMemberDispatch.from_mapping(dict(
        protocol_version=PROTOCOL_VERSION, room_id='room-one', home_install_id='home-install',
        authority_gateway_id='home-gateway', authority_epoch=1, member_id='member-one',
        target_install_id=catalog['installation_id'], target_profile='default', task_id=task,
        execution_generation=1, source_event_seq=1, cancellation_scope_id='cancel-one',
        prompt=prompt, prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(),
        capability_digest=catalog['catalog_digest'], execution_policy_digest=digest,
        trace_id='trace-one'))


@pytest.mark.asyncio
async def test_registered_owner_http_admits_new_run_and_ownerless_refuses(target, monkeypatch):
    from gateway import run
    from gateway.hosted_room_input_custody import initialize_input_custody
    from gateway.hosted_room_input_reclamation import initialize_working_copies
    from gateway.platforms.api_server_authority_runs import raw_run_admission
    from gateway.platforms.api_server_room_grants import _http_routes
    from gateway.runtime_ownership import process_ownership

    monkeypatch.setattr(run, '_hermes_home', target.home)
    process_ownership.reserve([target.home])
    try:
        from hermes_state_logical_attempts import prepare_logical_attempt_index
        initialize_input_custody(target.db)
        initialize_working_copies(target.db, epoch=target.authority.epoch)
        # NEW room replay treats an unprepared logical index as unavailable.
        # Production installs that coverage in initialize_session_authority.
        prepare_logical_attempt_index(target.db, batch_size=128)
        scheduled = []
        monkeypatch.setattr(target.authority, '_schedule', scheduled.append)
        app = web.Application()
        for method, path, handler in _http_routes(target.adapter):
            app.router.add_route(method, path, handler)
        app.router.add_post('/v1/runs', target.adapter._handle_runs)
        bearer = {'Authorization': 'Bearer synthetic-target-api-key'}
        async with TestClient(TestServer(app)) as http:
            try:
                invitation = await http.post('/v1/room-members/invitations', headers=bearer, json={
                    'room_id': 'room-one', 'home_install_id': 'home-install',
                    'authority_gateway_id': 'home-gateway', 'authority_epoch': 1, 'member_id': 'member-one',
                    'ttl_seconds': 600, 'status_ttl_seconds': 1200,
                })
                body = await invitation.json()
                assert invitation.status == 201, body
                catalog = body['catalog']
                grant = body['grant']
                capabilities = await http.get(
                    '/v1/room-members/capabilities', headers={'Authorization': f'HermesRoom {grant}'})
                assert capabilities.status == 200, await capabilities.text()
                first = _dispatch(catalog, 'task-one', 'hello')
                started = await http.post('/v1/runs', headers={
                    'Authorization': f'HermesRoom {grant}',
                    'Idempotency-Key': f'room:{first.task_id}:{first.execution_generation}',
                }, json={'input': first.prompt, 'hosted_room_dispatch': first.as_mapping()})
                started_body = await started.json()
                assert started.status == 202, started_body
                owned = raw_run_admission(target.adapter, started_body['run_id'])
                assert owned is not None and owned[0] is target.authority
                session = target.db.get_session(owned[1]['target_session_id'])
                assert session['source'] == 'bot_room'
                assert owned[1]['status'] in {'queued', 'started'}
                assert len(scheduled) == 1
                # Launch pointer only. The registry slot must stay so absence is
                # still certified; root_target then refuses the unregistered owner.
                target.runner.session_authority = None
                second = _dispatch(catalog, 'task-two', 'other')
                refused = await http.post('/v1/runs', headers={
                    'Authorization': f'HermesRoom {grant}',
                    'Idempotency-Key': f'room:{second.task_id}:{second.execution_generation}',
                }, json={'input': second.prompt, 'hosted_room_dispatch': second.as_mapping()})
                refused_body = await refused.json()
                assert refused.status == 403, refused_body
                assert refused_body['error']['message'] == 'canonical_room_peer_unsupported'
                assert len(scheduled) == 1
                target.runner.session_authorities._by_key[target.runner.session_authorities.launch_key] = None
                third = _dispatch(catalog, 'task-three', 'again')
                unavailable = await http.post('/v1/runs', headers={
                    'Authorization': f'HermesRoom {grant}',
                    'Idempotency-Key': f'room:{third.task_id}:{third.execution_generation}',
                }, json={'input': third.prompt, 'hosted_room_dispatch': third.as_mapping()})
                unavailable_body = await unavailable.json()
                assert unavailable.status == 503, unavailable_body
                assert unavailable_body['error']['code'] == 'storage_unavailable'
                assert len(scheduled) == 1
            finally:
                pending = [task for task in target.adapter._active_run_tasks.values() if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
    finally:
        process_ownership.release(target.home)
