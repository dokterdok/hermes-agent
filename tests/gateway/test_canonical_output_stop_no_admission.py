"""Real inert FIFO/producer/native Stop lifecycle regressions; no model or runtime."""
import asyncio
import time

import pytest
from gateway import hosted_room_driver as tasks
from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactError
from gateway.session_hosted_output_lifecycle import records, key_for
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_output_lifecycle import connection, dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize('ack_mode', ['retry-ack', 'inline-ack', 'local-idle'])
@pytest.mark.parametrize('inventory', [False, True], ids=['absent-store', 'empty-store'])
async def test_stop_during_real_local_input_preparation_disallows_admission(tmp_path, monkeypatch, ack_mode, inventory):
    """Stop's captured no-admission obligation must survive the preparation barrier."""
    import threading
    from gateway import hosted_room_input_preparation as preparation
    from hermes_state_runtime import list_session_admissions

    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        entered, release = threading.Event(), threading.Event()
        if inventory:
            RoomArtifactOutbox(service.db_path)
        original = preparation.prepare_hosted_input
        def paused(*args, **kwargs):
            entered.set()
            assert release.wait(8), 'preparation barrier was not released'
            return original(*args, **kwargs)
        monkeypatch.setattr(preparation, 'prepare_hosted_input', paused)
        executions = []
        async def handle(event):
            executions.append(event.message_id)
            return ''
        runner._handle_message = handle
        service.send(room_id='room', event_id='preparing', payload={
            'thread_id': 'preparing', 'text': '@writer Prepare'})
        queued, = tasks.list_tasks(service.db_path, room_id='room', status='queued')
        binding = service.bindings()[0]
        lease = tasks.acquire_lease(service.db_path, room_id='room',
            gateway_id=binding.gateway_id, authority_epoch=binding.authority_epoch,
            process_generation=service.runtime.process_generation, ttl_seconds=60, clock=time.time)
        attempt = tasks.start_task(service.db_path, queued['identity'], lease,
            expected_cancel_generation=0, clock=time.time)
        rpc = service._resolve_member_transport(binding, queued)
        assert rpc is service.member_rpcs[next(iter(service.member_rpcs))]
        real_ack, real_info = service.runtime.acknowledge_unadmitted_stop, rpc.info
        observations = []
        def observe_info(**params):
            assert params == {'profile': 'default', 'session_id': sid, 'source': 'bot_room'}
            if ack_mode == 'retry-ack':
                observations.append('unavailable')
                raise RuntimeStoreError('storage_unavailable')
            result = real_info(**params)
            observations.append(result)
            return result
        sid = (await asyncio.to_thread(rpc.create, profile='default', source='bot_room',
                                      title='Group: room'))['session_id']
        work = asyncio.create_task(asyncio.to_thread(rpc.submit, profile='default',
            source='bot_room', session_id=sid, prompt=queued['payload']['prompt'],
            task=attempt.identity, execution_generation=attempt.execution_generation,
            on_terminal=lambda receipt: None))
        try:
            assert await asyncio.to_thread(entered.wait, 8)
            # Pending requires withholding both independent acknowledgements;
            # local-idle keeps the real canonical observation authoritative.
            if ack_mode != 'inline-ack':
                monkeypatch.setattr(service.runtime, 'acknowledge_unadmitted_stop', lambda task: False)
                monkeypatch.setattr(rpc, 'info', observe_info)
            stopping = await dispatch(connection(authority, service), 'stop',
                                      room_id='room', cancel_id='preparation-stop')
            assert stopping.get('result') == {'cancelled': 1}, stopping
            task = tasks.get_task(service.db_path, attempt.identity)
            assert task['status'] == ('stopping' if ack_mode == 'retry-ack' else 'cancelled')
            assert task['execution_generation'] == 1
            if ack_mode == 'retry-ack':
                assert observations and all(item == 'unavailable' for item in observations)
            elif ack_mode == 'local-idle':
                assert observations and all(item['status'] == 'idle' and item['active'] is False
                                            for item in observations)
            with authority.db._read_ctx() as conn:
                captured = dict(records(conn, 'room'))[key_for(task)]
            if ack_mode == 'retry-ack':
                assert captured['state'] == 'waiting'
                assert captured['binding']['unavailable'] == 'admission_unavailable'
                assert captured['binding']['cancel_id'] == 'preparation-stop'
                with pytest.raises(RoomArtifactError, match='terminal changed'):
                    service._reconcile_stopped_output({**task, 'status': 'cancelled'})
            else:
                assert captured['state'] in {'waiting', 'completed'}
                if captured['state'] == 'waiting':
                    assert captured['binding']['unavailable'] == 'admission_unavailable'
                else:
                    assert captured['disposition'] == 'never_admitted'
            release.set()
            with pytest.raises(RuntimeStoreError, match='permission_denied'):
                await work
            assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
            assert executions == []
            if ack_mode == 'retry-ack':
                monkeypatch.setattr(rpc, 'info', real_info)
                monkeypatch.setattr(service.runtime, 'acknowledge_unadmitted_stop', real_ack)
                assert await asyncio.to_thread(service.runtime._finish_stop, binding, task, lease)
            terminal = tasks.get_task(service.db_path, attempt.identity)
            assert terminal['status'] == 'cancelled' and terminal['execution_generation'] == 1
            assert service._reconcile_stopped_output(terminal)
            with authority.db._read_ctx() as conn:
                completed = dict(records(conn, 'room'))[key_for(terminal)]
            assert completed['state'] == 'completed'
            assert completed['disposition'] == 'never_admitted'
            assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
            assert executions == []
        finally:
            monkeypatch.setattr(rpc, 'info', real_info)
            monkeypatch.setattr(service.runtime, 'acknowledge_unadmitted_stop', real_ack)
            release.set()
            if not work.done():
                await asyncio.gather(work, return_exceptions=True)
            service.runtime._thread = None
