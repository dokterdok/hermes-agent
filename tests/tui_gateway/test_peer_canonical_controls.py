"""Peer control translation through real HTTP handlers and canonical SQLite rows."""
import asyncio
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_roomlink_review_grants import target  # noqa: F401


@pytest_asyncio.fixture
async def peer_target(target, tmp_path):
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import GatewayRoomCatalog
    from tui_gateway.hosted_room_driver import HostedRoomBinding
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    from tui_gateway.hosted_room_peer_transport import PeerMemberRoute

    target.app.router.add_get('/p/{profile}/v1/runs/{run_id}', target.adapter._handle_get_run)
    target.app.router.add_post('/p/{profile}/v1/runs/{run_id}/approval', target.adapter._handle_run_approval)
    async with TestClient(TestServer(target.app)) as http:
        gateway = hosted_rooms.local_authority_gateway_id()
        response = await http.post(f'/p/{target.profile}/v1/room-members/invitations',
            headers={'Authorization': 'Bearer test-key'}, json={
                'room_id': 'room', 'home_install_id': gateway, 'authority_gateway_id': gateway,
                'authority_epoch': 1, 'member_id': 'helper'})
        invitation = await response.json()
        assert response.status == 201, invitation
        catalog = GatewayRoomCatalog.from_mapping(invitation['catalog'])
        route = PeerMemberRoute(home_install_id=gateway, member_id='helper',
            target_install_id=catalog.installation_id, target_profile=target.profile,
            capability_digest=catalog.catalog_digest,
            execution_policy_digest=catalog.execution_policy.policy_digest,
            cancellation_scope_id='cancel', trace_id='trace', grant=invitation['grant'])
        binding = HostedRoomBinding('room', gateway, 1)
        peer = PeerRunsHTTPClient(base_url=str(http.make_url('')), api_key='',
            target_profile=target.profile, receipt_db_path=tmp_path / 'source.db')
        try:
            yield SimpleNamespace(target=target, http=http, peer=peer,
                authority=target.authority, route=route, binding=binding, catalog=catalog)
        finally:
            observers = list(target.adapter._active_run_tasks.values())
            for observer in observers:
                observer.cancel()
            if observers:
                await asyncio.gather(*observers, return_exceptions=True)


@pytest_asyncio.fixture
async def peer_run(peer_target):
    from gateway.platforms.api_server_authority_runs import run_admission
    from tui_gateway.hosted_room_peer_transport import build_member_dispatch
    p = peer_target
    p.dispatch = build_member_dispatch(binding=p.binding, route=p.route, room_id='room', task_id='task',
        target_profile=p.target.profile, execution_generation=7, source_event_seq=1,
        prompt='Inspect fixture', trace_id='trace')
    p.receipt = await asyncio.to_thread(p.peer.dispatch, dispatch=p.dispatch.as_mapping(), grant=p.route.grant)
    p.authority, p.row = run_admission(p.target.adapter, p.receipt['run_id'])
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize('observe_first', [True, False])
async def test_peer_projects_and_answers_exact_canonical_prompt(peer_run, observe_first):
    from hermes_state_runtime import claim_session_input
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError

    p = peer_run
    row = claim_session_input(p.authority.db, epoch=p.authority.epoch,
                               session_id=p.row['target_session_id'])
    assert row['generation'] != p.dispatch.execution_generation
    live = p.authority.sessions[row['target_session_id']]
    resolved = []
    p.authority.register_approval(row['target_session_id'], row['generation'], live.route,
        {'request_id': 'approval-one', 'command': 'fixture command'})
    live.controls.remote_responders['approval-one'] = lambda *args: resolved.append(args)
    if observe_first:
        status = await asyncio.to_thread(p.peer.status, room_id='room', profile=p.target.profile,
            session_id=p.receipt['session_id'], grant=p.route.grant)
        assert status['status'] == 'waiting_for_approval'
        assert status['execution_generation'] == 7
        assert status['approval']['request_id'] == 'approval-one'
        assert status['approval']['execution_generation'] == row['generation']
        assert status['approval']['choices'] == ['once', 'deny']
    args = dict(task_id='task', execution_generation=7, request_id='approval-one',
                choice='once', grant=p.route.grant)
    for change in ({'choice': 'always'}, {'request_id': 'another-prompt'}):
        with pytest.raises(PeerRunsHTTPError):
            await asyncio.to_thread(p.peer.approve_receipt, **{**args, **change})
        assert not resolved
    with pytest.raises(PeerRunsHTTPError):
        await asyncio.to_thread(p.peer.approve_receipt, **{**args, 'execution_generation': True})
    assert await asyncio.to_thread(p.peer.approve_receipt, **{**args, 'execution_generation': 8}) is None
    assert not resolved
    response = await asyncio.to_thread(p.peer.approve_receipt, **args)
    assert response['status'] == 'resolved'
    assert resolved == [('approval', 'approval-one', 'once')]


@pytest.mark.asyncio
async def test_canonical_unknown_is_not_a_terminal_peer_receipt(peer_run):
    from hermes_state_runtime import claim_session_input

    p = peer_run
    row = claim_session_input(p.authority.db, epoch=p.authority.epoch,
                               session_id=p.row['target_session_id'])
    # Seed retained unknown evidence for projection; no owner is killed or restarted.
    p.authority.db._execute_write(lambda conn: conn.execute(
        "UPDATE session_admissions SET status='unknown' WHERE admission_id=?", (row['admission_id'],)))
    args = dict(room_id='room', profile=p.target.profile,
                session_id=p.receipt['session_id'], grant=p.route.grant)
    status = await asyncio.to_thread(p.peer.status, **args)
    assert status['status'] == 'unknown'
    assert status['active'] is False
    assert await asyncio.to_thread(p.peer.history, **args) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('remote_state', ['cancelled', 'failed', 'queued', 'unknown', 'missing', 'closing'])
async def test_peer_deferred_controls_refuse_unproven_retry(peer_run, remote_state):
    from gateway import hosted_room_driver as tasks
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from hermes_state import SessionDB
    from hermes_state_runtime import (
        RuntimeStoreError, begin_runtime_epoch, cancel_session_input, claim_session_input,
        get_session_admission, list_session_admissions, settle_session_input,
    )

    p = peer_run
    if remote_state in {'cancelled', 'closing'}:
        cancel_session_input(p.authority.db, epoch=p.authority.epoch, admission_id=p.row['admission_id'])
    elif remote_state in {'completed', 'failed'}:
        row = claim_session_input(p.authority.db, epoch=p.authority.epoch,
                                   session_id=p.row['target_session_id'])
        settle_session_input(p.authority.db, epoch=p.authority.epoch,
            admission_id=row['admission_id'], generation=row['generation'], outcome=remote_state,
            result={'result': {'final_response': 'retained result'}, 'usage': {}})
    elif remote_state == 'unknown':
        claim_session_input(p.authority.db, epoch=p.authority.epoch, session_id=p.row['target_session_id'])
        p.authority.db._execute_write(lambda conn: conn.execute(
            "UPDATE session_admissions SET status='unknown' WHERE admission_id=?", (p.row['admission_id'],)))
    remote_before = get_session_admission(p.authority.db, admission_id=p.row['admission_id'])
    with SessionDB(p.peer.receipt_db_path) as db:
        authority = SimpleNamespace(db=db, profile_id=str(p.peer.receipt_db_path.parent),
            epoch=begin_runtime_epoch(db, instance_id='source'))
        service = CanonicalHostedRoomService(authority, asyncio.get_running_loop())
        service.authorize_room('alice', 'room', create=True)
        service.create_room(room_id='room', name='Room', members=[
            {'member_id': 'host', 'profile': 'default', 'handle': 'host'},
            {'member_id': 'helper', 'profile': p.target.profile, 'handle': 'helper', 'target': {
                'kind': 'peer', 'installation_id': p.catalog.installation_id,
                'peer_id': p.catalog.installation_id, 'profile': p.target.profile,
                'capability_digest': p.catalog.catalog_digest}}])
        service.register_peer_route(room_id='room', member_id='helper', route=p.route,
            client=p.peer, target_url=p.peer.base_url, catalog=p.catalog)
        identity = tasks.TaskIdentity('room', 'task', 'thread', 'turn')
        tasks.admit_task(db.db_path, identity, payload={
            'target_profile': p.target.profile, 'target_member_id': 'helper',
            'source_event_seq': 1, 'prompt': p.dispatch.prompt}, clock=time.time)
        # A retained deferred task is input to this control test, not a simulated host fault.
        db._execute_write(lambda conn: conn.execute(
            "UPDATE hosted_room_driver_tasks SET status='deferred', execution_generation=7 "
            "WHERE room_id='room' AND task_id='task'"))
        if remote_state == 'missing':
            p.peer._runs.clear()
            db._execute_write(lambda conn: conn.execute('DELETE FROM hosted_room_remote_runs'))
        before = tasks.get_task(db.db_path, identity)
        args = dict(room_id='room', member_id='helper', task_id='task', execution_generation=7)
        if remote_state == 'closing':
            service.begin_room_disband('room')
            with pytest.raises(tasks.RoomUnavailableError, match='being disbanded'):
                await asyncio.to_thread(service.retry_room_task, **args)
            assert tasks.get_task(db.db_path, identity) == before
        else:
            reason = ('session_busy' if remote_state == 'queued' else
                      'unsupported_operation' if remote_state in {'cancelled', 'failed'} else 'unknown_execution')
            with pytest.raises(RuntimeStoreError, match=reason):
                await asyncio.to_thread(service.retry_room_task, **args)
            assert tasks.get_task(db.db_path, identity) == before
        # This permission is deliberately not added for remote unknown work.
        with pytest.raises(RuntimeStoreError, match='unsupported_operation'):
            await asyncio.to_thread(service.discard_room_task, **args)
    assert get_session_admission(p.authority.db, admission_id=p.row['admission_id']) == remote_before
    assert len(list_session_admissions(p.authority.db,
        session_id=p.row['target_session_id'], pending_only=False)) == 1
