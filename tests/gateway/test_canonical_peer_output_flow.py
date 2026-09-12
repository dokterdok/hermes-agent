"""Explicit peer output crosses the exact Run and home publication boundaries."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_canonical_peer_inputs import grant_for


@pytest.mark.asyncio
async def test_peer_tool_run_history_and_home_copy_precede_ack(api, owner, tmp_path, monkeypatch):
    from gateway import hosted_rooms, hosted_room_driver as tasks
    from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactScope
    from gateway.hosted_room_peer import GatewayRoomCatalog
    from gateway.platforms import api_server_runs
    from gateway.platforms.api_server_authority_runs import run_admission, run_projection
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.run import _profile_runtime_scope
    from gateway.session_contract import SessionRef, Principal
    from gateway.session_finite import execute_finite_admission
    from gateway.session_group_files import dispatch_group_files
    from gateway.session_hosted_output import current_output_binding
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from gateway.session_results import finish_result
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch, claim_session_input
    from tui_gateway.hosted_room_driver import _find_terminal_receipt
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    from tui_gateway.hosted_room_peer_transport import PeerMemberRoute, build_member_dispatch
    from tools import hosted_room_artifact  # noqa: F401
    from tools.registry import registry

    owner.profile_id = str(tmp_path)
    # Two installation roots in one test process; all state and HTTP remain real.
    from hermes_cli import install_identity
    from hermes_constants import get_hermes_home
    monkeypatch.setattr(install_identity, 'get_default_hermes_root', get_hermes_home)
    target_id = hosted_rooms.local_authority_gateway_id()
    policy, catalog = _local_room_catalog(api, 'default', target_id)
    home = tmp_path / 'coordinator'
    home.mkdir()
    async def queued(*args, **kwargs):
        pass
    monkeypatch.setattr(api_server_runs, '_execute_run', queued)
    app = web.Application(middlewares=[api._make_profile_prefix_middleware()])
    for method, path, handler in api._http_route_table():
        app.router.add_route(method, path, handler)
        app.router.add_route(method, '/p/{profile}' + path, handler)
    with SessionDB(home / 'state.db') as db:
        coordinator = SimpleNamespace(profile_id=str(home), db=db,
            epoch=begin_runtime_epoch(db, instance_id='coordinator'))
        with _profile_runtime_scope(home):
            home_id = hosted_rooms.local_authority_gateway_id()
            assert home_id != target_id
            service = CanonicalHostedRoomService(coordinator, asyncio.get_running_loop())
            service.local_profiles = lambda: ('default',)
            service.authorize_room('human', 'room', create=True)
            service.create_room(room_id='room', name='Report team', members=[
                dict(member_id='local', profile='default', handle='local'),
                dict(member_id='peer', profile='default', handle='peer', target={
                    'kind': 'peer', 'installation_id': target_id, 'peer_id': target_id,
                    'profile': 'default', 'capability_digest': catalog['catalog_digest']})])
            service.send(room_id='room', event_id='request', payload=dict(thread_id='thread', text='@peer Write a report.'))
            task, = tasks.list_tasks(db.db_path, room_id='room')
            room_binding, = service.bindings()
            lease = tasks.acquire_lease(db.db_path, room_id='room', gateway_id=home_id, authority_epoch=1,
                process_generation='driver', ttl_seconds=60, clock=time.time)
            attempt = tasks.start_task(db.db_path, task['identity'], lease, expected_cancel_generation=0, clock=time.time)
        route_args = dict(home_install_id=home_id, member_id='peer', target_install_id=target_id,
            target_profile='default', capability_digest=catalog['catalog_digest'],
            execution_policy_digest=policy['policy_digest'], cancellation_scope_id='cancel', trace_id='trace', attachments=True)
        draft = PeerMemberRoute(**route_args, grant='pending')
        dispatch = build_member_dispatch(binding=room_binding, route=draft, room_id='room',
            task_id=task['identity'].task_id, target_profile='default', execution_generation=attempt.execution_generation,
            source_event_seq=task['payload']['source_event_seq'], prompt=task['payload']['prompt'], trace_id='trace')
        grant = grant_for(api, dispatch)
        route = PeerMemberRoute(**route_args, grant=grant)
        async with TestClient(TestServer(app)) as http:
            client = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='', receipt_db_path=db.db_path)
            with _profile_runtime_scope(home):
                service.register_peer_route(room_id='room', member_id='peer', route=route, client=client,
                    target_url=client.base_url, catalog=GatewayRoomCatalog.from_mapping(catalog))
                rpc = service._resolve_member_transport(room_binding, tasks.get_task(db.db_path, task['identity']))
                coords = dict(profile='default', source='bot_room')
                sid = (await asyncio.to_thread(rpc.create, **coords, title='Group: room'))['session_id']
                accepted = await asyncio.to_thread(rpc.submit, **coords, session_id=sid, task=task['identity'],
                    prompt=task['payload']['prompt'], execution_generation=attempt.execution_generation, on_terminal=lambda _: None)
            _, row = run_admission(api, accepted['run_id'])
            row = claim_session_input(owner.db, epoch=owner.epoch, session_id=row['target_session_id'])
            output = tmp_path / 'cache' / 'report.txt'
            output.parent.mkdir(exist_ok=True)
            output.write_bytes(b'Explicit peer output for the report team.\n')
            bindings = []
            async def handle(event):
                binding = current_output_binding()
                assert binding is not None and binding.authority is owner
                bindings.append(binding)
                shared = json.loads(await asyncio.to_thread(registry.dispatch, 'share_group_file', {'path': str(output)}))
                assert shared.get('ok'), shared
                return 'Report attached.'
            owner.runner._handle_message = handle
            ref = SessionRef(owner.profile_id, row['target_session_id'])
            response = await execute_finite_admission(owner, ref, row)
            finish_result(owner.db, epoch=owner.epoch, row=row, response=response, outcome='completed',
                          result=owner.pending_results.pop(row['admission_id']))
            assert current_output_binding() is None and not bindings[0].active
            projected = run_projection(api, accepted['run_id'])
            scope = RoomArtifactScope.from_mapping(projected['room_artifact_scope'])
            outbox = RoomArtifactOutbox(owner.db.db_path)
            assert not outbox.retirement_complete(scope)
            with _profile_runtime_scope(home):
                history = await asyncio.to_thread(rpc.history, **coords, session_id=sid)
                receipt = _find_terminal_receipt(history, task['identity'], attempt.execution_generation)
                assert receipt.result['peer_run_id'] == accepted['run_id']
                assert receipt.result['artifacts'] == projected['artifacts']
                tasks.settle_task(db.db_path, attempt, status=receipt.status, settlement_id=receipt.settlement_id,
                                  result=receipt.result, clock=time.time)
                await asyncio.to_thread(service.prepare_room, room_binding)
                actor = Principal('human', str(home), frozenset({'session:read'}), 'viewer')
                item, = dispatch_group_files(service, actor, 'groups.attachment.list', {'room_id': 'room'})['items']
                saved = dispatch_group_files(service, actor, 'groups.attachment.download',
                    dict(room_id='room', event_id=item['event_id'], attachment_id=item['attachment_id']))
                import base64
                assert base64.b64decode(saved['data_base64']) == output.read_bytes()
                before = service._events('room')
                await asyncio.to_thread(service.prepare_room, room_binding)
                assert service._events('room') == before
                assert len([event for event in service._events('room') if event['kind'] == 'message.member']) == 1
            assert outbox.retirement_complete(scope)


def test_peer_output_needs_retained_consent_and_exact_started_run(api, owner, tmp_path):
    from tests.gateway.test_canonical_peer_artifacts import retained_output
    from gateway.session_peer_output import peer_output_binding
    from gateway.session_contract import SessionRef
    from gateway.hosted_room_artifacts import RoomArtifactError
    dispatch, scope, outbox, item, manifest, row = retained_output(api, owner, tmp_path)
    ref = SessionRef(owner.profile_id, row['target_session_id'])
    # A completed or unrelated Run never exposes a producer, even if its file is readable.
    with pytest.raises(RoomArtifactError, match='admission changed'):
        peer_output_binding(owner, ref, row)
    for change in ({'room_artifact_publication': False}, {'room_dispatch': None}):
        modified = {**row, 'payload': {'api_turn_v1': {'settings': {
            **row['payload']['api_turn_v1']['settings'], **change}}}}
        assert peer_output_binding(owner, ref, modified) is None
