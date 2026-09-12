"""Named producer -> real owner sockets -> root custody and canonical publication."""
import asyncio
import base64
from dataclasses import replace
import json
import time

import pytest

from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_session_hosted_transport import _server


@pytest.mark.asyncio
@pytest.mark.parametrize('single_socket', [False, True], ids=['separate-sockets', 'multiplex-socket'])
async def test_named_output_uses_root_custody_and_exact_terminal_publication(tmp_path, monkeypatch, single_socket):
    from gateway import hosted_room_driver as tasks
    from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactScope
    from gateway.session_authorities import SessionAuthorities, owner_scope
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from gateway.session_group_files import dispatch_group_files
    from gateway.session_hosted_output import current_output_binding
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from gateway.session_hosted_transport import install_hosted_transport, owner_request
    from gateway.session_results import admission_result
    from hermes_state import SessionDB
    from hermes_state_runtime import RuntimeStoreError
    from tools import hosted_room_artifact  # noqa: F401
    from tools.registry import registry

    async with owner(tmp_path, monkeypatch) as (root, service, runner):
        home = tmp_path / 'profiles' / 'reviewer'
        home.chmod(0o700)
        runner.config.multiplex_profiles = True
        authorities = runner.session_authorities = SessionAuthorities(tmp_path)
        authorities.add(tmp_path, root)
        runner._adapter_for_source = lambda source: (
            runner._profile_adapters.get(source.profile, {}) if source.profile else runner.adapters
        ).get(source.platform)
        with SessionDB(home / 'state.db') as db:
            target = await initialize_session_authority(runner, profile_id=str(home), instance_id='named', db=db, register=False)
            authorities.add(home, target, name='reviewer')
            with owner_scope(target):
                target.hosted_room_service = CanonicalHostedRoomService(target, asyncio.get_running_loop())
            servers = [_server(tmp_path)]
            if single_socket:
                servers[0]._handlers['identify']()['served_profiles'] = authorities.served_profiles()
            else:
                servers.append(_server(home))
            for server, authority in zip(servers, (root, target)):
                install_hosted_transport(server, authority, asyncio.get_running_loop(), attest=authority.hosted_room_service.attest)
                assert await server.start()
            observed = []
            source_errors = []
            original_handler = servers[0].private_handlers['hosted-attest']
            def traced(params, peer):
                try:
                    return original_handler(params, peer)
                except Exception:
                    import traceback
                    source_errors.append(traceback.format_exc())
                    raise
            servers[0].private_handlers['hosted-attest'] = traced
            original_outbox_init = RoomArtifactOutbox.__init__
            def owned_outbox(outbox, *args, **kwargs):
                from hermes_constants import get_hermes_home
                assert get_hermes_home() == tmp_path, 'named authority must never open the root outbox'
                original_outbox_init(outbox, *args, **kwargs)
            monkeypatch.setattr(RoomArtifactOutbox, '__init__', owned_outbox)
            try:
                output = home / 'cache' / 'report.txt'
                output.parent.mkdir()
                data = b'named profile explicit output\n' * 2000
                output.write_bytes(data)
                async def handle(event):
                    binding = current_output_binding()
                    assert binding is not None and binding.authority is target
                    observed.append(binding)
                    # Existing default output and named output consume one quota,
                    # not two profile-specific budgets.
                    other_scope = replace(binding.scope, task_id='other-task', member_id='writer', target_profile='default')
                    with owner_scope(root):
                        root_box = RoomArtifactOutbox(root.db.db_path)
                        root_box.put_bytes(scope=other_scope, data=b'root reserved bytes', source_name='root.txt')
                    import gateway.hosted_room_artifacts as artifacts
                    with monkeypatch.context() as quota:
                        quota.setattr(artifacts, 'MAX_GATEWAY_BLOB_BYTES', len(data))
                        denied = json.loads(await asyncio.to_thread(registry.dispatch, 'share_group_file', {'path': str(output)}))
                        assert denied['ok'] is False
                        assert 'gateway room artifact quota exceeded' in source_errors[-1]
                    with owner_scope(root):
                        root_box.discard_durably(other_scope)
                    shared = json.loads(await asyncio.to_thread(registry.dispatch, 'share_group_file', {'path': str(output)}))
                    assert shared.get('ok') is True, shared
                    repeated = json.loads(await asyncio.to_thread(registry.dispatch, 'share_group_file', {'path': str(output)}))
                    assert repeated['artifact_id'] == shared['artifact_id']
                    # Borrowed snapshots expire on each explicit promotion return.
                    assert not binding.snapshots
                    with pytest.raises(RuntimeStoreError, match='permission_denied'):
                        await asyncio.to_thread(owner_request, home, 'hosted-output-source', dict(
                            source_home=str(tmp_path), token=binding.token, snapshot='expired', offset=0))
                    # A live handle must still be tied to the actual admission.
                    original = binding.row['generation']
                    binding.row['generation'] = original + 1
                    try:
                        with pytest.raises(RuntimeStoreError):
                            await asyncio.to_thread(owner_request, home, 'hosted-output-source', dict(
                                source_home=str(tmp_path), token=binding.token))
                    finally:
                        binding.row['generation'] = original
                    return 'Shared named-profile report.'
                runner._handle_message = handle
                service.send(room_id='room', event_id='named-request', payload=dict(thread_id='thread', text='@reviewer Write a report'))
                task, = tasks.list_tasks(service.db_path, room_id='room', status='queued')
                room_binding = service.bindings()[0]
                lease = tasks.acquire_lease(service.db_path, room_id='room', gateway_id=room_binding.gateway_id,
                    authority_epoch=room_binding.authority_epoch, process_generation='driver', ttl_seconds=60, clock=time.time)
                attempt = tasks.start_task(service.db_path, task['identity'], lease, expected_cancel_generation=0, clock=time.time)
                rpc = service._resolve_member_transport(room_binding, task)
                coords = dict(profile='reviewer', source='bot_room')
                sid = (await asyncio.to_thread(rpc.create, **coords, title='Group: room'))['session_id']
                done, failures = asyncio.Event(), []
                loop = asyncio.get_running_loop()
                def terminal(receipt):
                    try:
                        service.runtime._on_terminal(room_binding, attempt, receipt)
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        loop.call_soon_threadsafe(done.set)
                receipt = await asyncio.to_thread(rpc.submit, **coords, session_id=sid, prompt=task['payload']['prompt'],
                    task=task['identity'], execution_generation=attempt.execution_generation, on_terminal=terminal)
                await asyncio.wait_for(done.wait(), 15)
                if failures:
                    raise failures[0]
                stored = tasks.get_task(service.db_path, task['identity'])
                assert stored['status'] == 'settled', '\n'.join(source_errors)
                assert len(observed) == 1
                saved = admission_result(db, receipt['admission_id'])
                assert saved['result']['artifact_scope'] == stored['result']['artifact_scope']
                scope = RoomArtifactScope.from_mapping(saved['result']['artifact_scope'])
                assert scope.target_profile == 'reviewer' and scope.member_id == 'reviewer'
                root_box = RoomArtifactOutbox(root.db.db_path)
                assert root_box.retirement_complete(scope)
                assert not (home / 'hosted-room-artifact-outbox').exists()
                with db._read_ctx() as conn:
                    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='hosted_room_output_artifacts'").fetchone()[0] == 0
                actor = Principal('alice', str(tmp_path), frozenset({'session:read'}), 'viewer')
                page = dispatch_group_files(service, actor, 'groups.attachment.list', {'room_id': 'room'})
                item, = page['items']
                result = dispatch_group_files(service, actor, 'groups.attachment.download', dict(room_id='room',
                    event_id=item['event_id'], attachment_id=item['attachment_id']))
                assert base64.b64decode(result['data_base64']) == data
                await asyncio.to_thread(service.prepare_room, room_binding)
                assert len([e for e in service._events('room') if e['kind'] == 'message.member']) == 1
                assert not target._hosted_output_sources and not observed[0].active
                with pytest.raises(RuntimeStoreError, match='permission_denied'):
                    await asyncio.to_thread(owner_request, home, 'hosted-output-source', dict(
                        source_home=str(tmp_path), token=observed[0].token))
            finally:
                for server in servers:
                    await server.stop()


def test_named_custody_route_does_not_create_a_third_owner_dependency(tmp_path):
    from gateway.session_hosted_output_transport import root_named_route
    named = tmp_path / 'profiles' / 'reviewer'
    assert root_named_route(tmp_path, named)
    assert not root_named_route(named, tmp_path)
    assert not root_named_route(named, tmp_path / 'profiles' / 'writer')
    assert not root_named_route(tmp_path, tmp_path / 'unrelated' / 'profiles' / 'writer')
