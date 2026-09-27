"""Real inert FIFO/producer/native Stop lifecycle regressions; no model or runtime."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from gateway import hosted_room_driver as tasks
from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactError
from gateway.session_hosted_output_lifecycle import records, key_for
from gateway.session_group_retirement import require_room_retired
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_canonical_hosted_outputs import owner, execute_group_turn
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


async def stopped_producer(authority, service, runner, root, monkeypatch, *, name, mixed=False):
    from gateway.session_hosted_output import current_output_binding
    from gateway.session_results import execution_result
    from tools.hosted_room_artifact import share_group_file
    shared, release = asyncio.Event(), asyncio.Event()
    captured = []
    output = root / 'cache' / (name + '.txt')
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(b'private output ' + name.encode())
    async def handle(event):
        captured.append(current_output_binding())
        assert json.loads(await asyncio.to_thread(share_group_file, str(output)))['ok']
        shared.set()
        await release.wait()
        execution_result.get().update(result=dict(interrupted=True, final_response='', messages=[]), usage={})
        return ''
    runner._handle_message = handle
    runner._cached_agent_for = lambda _: SimpleNamespace(interrupt=lambda: None)
    manifest = None
    if mixed:
        import base64
        document = service.attachments.put(room_id='room', upload_id=name, name=name+'.txt',
            kind='file', mime='text/plain', data=b'sensitive original input '+name.encode())
        image = service.attachments.put(room_id='room', upload_id=name+'-image', name='pixel.png',
            kind='image', mime='image/png', data=base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII='))
        manifest = [{k: item[k] for k in ('attachment_id', 'name', 'kind', 'mime', 'size')} for item in (document, image)]
    work = asyncio.create_task(execute_group_turn(authority, service, event_id=name, input_manifest=manifest,
                                                  defer_publication=True, thread_id=name))
    client = connection(authority, service)
    try:
        await asyncio.wait_for(shared.wait(), 8)
        answer = await dispatch(client, 'stop', room_id='room', cancel_id=name+'-stop')
        assert answer.get('result') == {'cancelled': 1}, answer
    finally:
        release.set()
        result = await asyncio.wait_for(work, 8)
        service.runtime._thread = None
    task = tasks.get_task(service.db_path, result[3]['identity'])
    # The admission callback only wakes the inert driver on cancellation.
    # Exercise its real receipt/acknowledgement path before claiming retirement.
    assert task['status'] == 'stopping'
    binding = result[4]
    lease = tasks.acquire_lease(service.db_path, room_id=binding.room_id,
        gateway_id=binding.gateway_id, authority_epoch=binding.authority_epoch,
        process_generation=service.runtime.process_generation, ttl_seconds=60, clock=time.time)
    assert await asyncio.to_thread(service.runtime._finish_stop, binding, task, lease)
    task = tasks.get_task(service.db_path, task['identity'])
    assert task['status'] == 'cancelled'
    service._reconcile_stopped_output(task)
    return task, result[4], captured[0]


def initialize_inputs(authority, root, request):
    from gateway.runtime_ownership import process_ownership
    from gateway.hosted_room_input_reclamation import initialize_working_copies
    process_ownership.reserve([root])
    request.addfinalizer(lambda: process_ownership.release(root))
    initialize_working_copies(authority.db, epoch=authority.epoch)


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed', [False, True], ids=['text', 'mixed'])
async def test_completed_replays_after_real_admission_retirement(tmp_path, monkeypatch, request, mixed):
    from typing import cast
    from hermes_state import SessionDB
    from hermes_state_mutation_retirement import retire_prunable
    from gateway.hosted_room_input_custody import custody_holds
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        db = cast(SessionDB, authority.db)
        media = None
        monkeypatch.setattr('model_tools._resolve_active_context_length', lambda: 32768)
        if mixed:
            initialize_inputs(authority, tmp_path, request)
        task, binding, producer = await stopped_producer(authority, service, runner, tmp_path, monkeypatch,
                                                         name='retirement', mixed=mixed)
        service.prepare_room(binding)
        with authority.db._read_ctx() as conn:
            record, = [r for _, r in records(conn, 'room')]
            assert record['state'] == 'completed'
            assert record['version'] == 2 and 'binding' not in record
            admission_row = conn.execute('SELECT admission_id,payload_json FROM session_admissions').fetchone()
            admitted = json.loads(admission_row['payload_json'])
            if mixed:
                media, = admitted['attachments_v1']['media']
                assert custody_holds(conn, authority.db.db_path, media)
                assert Path(media['path']).is_file()
        if mixed:
            assert media is not None
            from gateway.session_ingress_media import release_admission_media
            assert release_admission_media(authority.db, admission_row['admission_id']) == 0
            assert Path(media['path']).is_file(), 'completed Output is not raw admission retirement'
        assert authority.db._execute_write(lambda c: retire_prunable(c, [producer.ref.session_id])) == [producer.ref.session_id]
        if mixed:
            assert media is not None
            reference = media
            from hermes_state_terminal import ADMISSION_PREFIX
            def missing_or_foreign_proof_holds(conn):
                key = ADMISSION_PREFIX + admission_row['admission_id']
                for statement, args in (
                    ('DELETE FROM state_meta WHERE key=?', (key,)),
                    ("UPDATE state_meta SET value=json_set(value,'$.principal_id','foreign') WHERE key=?", (key,)),
                ):
                    conn.execute('SAVEPOINT incomplete_retirement_proof')
                    try:
                        conn.execute(statement, args)
                        assert custody_holds(conn, db.db_path, reference)
                    finally:
                        conn.execute('ROLLBACK TO incomplete_retirement_proof')
                        conn.execute('RELEASE incomplete_retirement_proof')
            db._execute_write(missing_or_foreign_proof_holds)
        def forbidden(*args, **kwargs):
            raise AssertionError('completed replay must not reconstruct inputs or unlink')
        with monkeypatch.context() as guard:
            guard.setattr('gateway.hosted_room_input_retained.retained_hosted_input', forbidden)
            guard.setattr('gateway.hosted_room_output_discard.os.unlink', forbidden)
            service.prepare_room(binding)
            service.prepare_room(binding)
            assert service._reconcile_stopped_output(task)
            assert not service.output_cleanup_status('room')
            with authority.db._read_ctx() as conn:
                require_room_retired(conn, 'room')
                done, = [r for _, r in records(conn, 'room')]
                assert done['version'] == 2 and done['state'] == 'completed'
                assert not ({'binding', 'original_binding', 'input_binding', 'items', 'blobs'} & done.keys())
                assert admitted['text'] not in json.dumps(done)
                if mixed:
                    assert not custody_holds(conn, authority.db.db_path, media)
            # A compact fact cannot be transplanted to a newly assigned generation.
            authority.db._execute_write(lambda c: c.execute('UPDATE hosted_room_driver_tasks SET execution_generation=execution_generation+1'))
            newer = tasks.get_task(service.db_path, task['identity'])
            authority.db._execute_write(lambda c: c.execute('INSERT INTO state_meta(key,value) VALUES(?,?)',
                (key_for(newer), json.dumps(done))))
            with pytest.raises(RoomArtifactError):
                service._reconcile_stopped_output(task)
            with pytest.raises(RoomArtifactError):
                service._reconcile_stopped_output(newer)
            authority.db._execute_write(lambda c: c.execute('DELETE FROM state_meta WHERE key=?', (key_for(newer),)))
            authority.db._execute_write(lambda c: c.execute('UPDATE hosted_room_driver_tasks SET execution_generation=execution_generation-1'))
            service.prepare_room(binding)
            assert tasks.prune_published_terminal_tasks(service.db_path, room_id='room', clock=lambda: 10**12, retain=0) == 1
            service.prepare_room(binding)
            with authority.db._read_ctx() as conn:
                require_room_retired(conn, 'room')

        if mixed:
            assert media is not None
            from gateway.hosted_room_input_reclamation import collect_legacy_input_aliases
            assert Path(media['path']).is_file()
            collect_legacy_input_aliases(authority.db, epoch=authority.epoch)
            assert not Path(media['path']).exists(), 'positive retirement must permit real reclamation'


@pytest.mark.asyncio
async def test_task_local_input_damage_does_not_starve_later_cleanup(tmp_path, monkeypatch, request):
    from gateway import hosted_room_task_scan as scans
    from gateway.hosted_room_input_reclamation import copy_path
    from hermes_state_mutation_retirement import retire_prunable
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr('model_tools._resolve_active_context_length', lambda: 32768)
        initialize_inputs(authority, tmp_path, request)
        import os
        original_unlink = os.unlink
        def fail_unlink(*args, **kwargs):
            if kwargs.get('dir_fd') is not None:
                raise OSError('hold real physical cleanup until both producers finish')
            return original_unlink(*args, **kwargs)
        with monkeypatch.context() as hold:
            hold.setattr('gateway.hosted_room_output_discard.os.unlink', fail_unlink)
            pairs = [await stopped_producer(authority, service, runner, tmp_path, monkeypatch,
                                            name=name, mixed=True) for name in ('first', 'second')]
        pairs.sort(key=lambda value: value[0]['identity'].task_id)
        first, binding, producer = pairs[0]
        later = pairs[1][0]
        with authority.db._read_ctx() as conn:
            before = dict(records(conn, 'room'))[key_for(first)]
            assert before['state'] == 'pending'
            input_ref = conn.execute('SELECT * FROM input_custody_copies WHERE copy_id=?',
                (before['binding']['input_binding']['copies'][0]['copy_id'],)).fetchone()
            source = copy_path(authority.db, dict(input_ref))
        original_input = source.read_bytes()
        source.write_bytes(b'damaged retained proof after capture')
        paths = [tmp_path / 'hosted-room-artifact-outbox' / 'blobs' / b['blob_name'] for b in before['blobs']]
        original_bytes = [p.read_bytes() for p in paths]
        with authority.db._read_ctx() as conn:
            intact, _ = service._cleanup_snapshot(conn, later)
        assert not intact.get('unavailable')  # independently intact, different input thread
        def populate(conn):
            row = dict(conn.execute('SELECT * FROM hosted_room_driver_tasks WHERE task_id=?', (first['identity'].task_id,)).fetchone())
            for i in range(scans.BUDGET + 2):
                filler = dict(row, task_id=first['identity'].task_id+f'-inventory-{i:03}',
                    thread_id=f'inventory-{i}', turn_id=f'inventory-{i}', status='queued', execution_generation=0,
                    result_json=None, settlement_id=None, settlement_status=None, terminal_at=None)
                conn.execute('INSERT INTO hosted_room_driver_tasks ('+','.join(filler)+') VALUES ('+
                             ','.join('?' for _ in filler)+')', tuple(filler.values()))
        authority.db._execute_write(populate)
        service._artifact_clock = lambda: 10**12
        sizes, visited = [], []
        original_page = scans.page
        def bounded(*args):
            scan, batch = original_page(*args)
            sizes.append(len(batch))
            visited.extend(t['identity'].task_id for t in batch)
            return scan, batch
        monkeypatch.setattr(scans, 'page', bounded)
        for _ in range(6):
            service._prepare_terminal_tasks(service._room('room'))
        with authority.db._read_ctx() as conn:
            after = dict(records(conn, 'room'))
            blocked = after[key_for(first)]
            assert blocked['blocked'] is True and blocked['reason_code'] == 'input_binding_unavailable'
            assert all(blocked[k] == before[k] for k in ('binding', 'items', 'blobs', 'removed', 'attempts'))
            assert after[key_for(later)]['state'] == 'completed', {
                'reason': after[key_for(later)]['reason_code'],
                'blocked': after[key_for(later)].get('blocked'),
                'next': after[key_for(later)]['next_attempt_at'],
                'later_visits': visited.count(later['identity'].task_id), 'sizes': sizes}
        # Remove only inventory fillers, not either genuine execution. Prove
        # refusal is not merely the fillers' queued status or an incomplete scan.
        authority.db._execute_write(lambda c: c.execute(
            'DELETE FROM hosted_room_driver_tasks WHERE task_id LIKE ?',
            (first['identity'].task_id+'-inventory-%',)))
        service._prepare_terminal_tasks(service._room('room'))
        service._prepare_terminal_tasks(service._room('room'))
        with authority.db._read_ctx() as conn:
            assert not scans.pending(conn, 'room')
            with pytest.raises(RuntimeStoreError, match='output_cleanup_pending'):
                require_room_retired(conn, 'room')
        assert [p.read_bytes() for p in paths] == original_bytes
        assert authority.db._execute_write(lambda c: retire_prunable(c, [producer.ref.session_id])) == []
        assert max(sizes) <= scans.BUDGET and visited.count(first['identity'].task_id) >= 2
        # Owner-wide failure or missing shared input schema must not be converted
        # into another task-local success; the frozen batch cannot advance.
        frozen = authority.db._execute_write(lambda c: scans.page(c, 'room'))[0]
        with monkeypatch.context() as draining:
            draining.setattr(runner, '_draining', True)
            with pytest.raises(RuntimeStoreError):
                service._prepare_terminal_tasks(service._room('room'))
        authority.db._execute_write(lambda c: c.execute('ALTER TABLE input_custody_refs RENAME TO preserved_refs'))
        import sqlite3
        try:
            with pytest.raises(sqlite3.OperationalError):
                service._prepare_terminal_tasks(service._room('room'))
            with authority.db._read_ctx() as conn:
                assert scans.scan_state(conn, 'room') == frozen
        finally:
            authority.db._execute_write(lambda c: c.execute('ALTER TABLE preserved_refs RENAME TO input_custody_refs'))
        # Repair exact original bytes: bounded revisits can discharge, never recapture.
        source.write_bytes(original_input)
        for _ in range(4):
            service._prepare_terminal_tasks(service._room('room'))
        with authority.db._read_ctx() as conn:
            assert dict(records(conn, 'room'))[key_for(first)]['state'] == 'completed'
        assert not any(p.exists() for p in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize(('claim_wins', 'inventory', 'mixed', 'with_image', 'damage_media'), [
    (False, 'ready', False, False, None), (True, 'ready', False, False, None),
    (False, 'missing', False, False, None), (False, 'occupied', False, False, None),
    (False, 'ready', True, False, None), (True, 'ready', True, False, None),
    (False, 'ready', True, True, None), (True, 'ready', True, True, None),
    (True, 'ready', True, True, 'missing'), (True, 'ready', True, True, 'corrupt')],
    ids=['queued', 'claim-wins', 'missing-inventory', 'unexpected-output',
         'queued-document', 'claim-wins-document', 'queued-mixed', 'claim-wins-mixed',
         'claim-wins-missing-media', 'claim-wins-corrupt-media'])
async def test_native_stop_before_claim_preserves_exact_canonical_generation(tmp_path, monkeypatch, request, claim_wins, inventory, mixed, with_image, damage_media):
    from gateway.session_results import execution_result
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr('model_tools._resolve_active_context_length', lambda: 32768)
        manifest = None
        if mixed:
            initialize_inputs(authority, tmp_path, request)
            document_bytes = b'accepted document for pre-entry Stop'
            document = service.attachments.put(room_id='room', upload_id='stop-document',
                name='notes.txt', kind='file', mime='text/plain', data=document_bytes)
            attachments = [document]
            if with_image:
                import base64
                image_bytes = base64.b64decode(
                    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=')
                attachments.append(service.attachments.put(room_id='room', upload_id='stop-image',
                    name='pixel.png', kind='image', mime='image/png', data=image_bytes))
            manifest = [{k: item[k] for k in ('attachment_id', 'name', 'kind', 'mime', 'size')}
                        for item in attachments]
        outbox = RoomArtifactOutbox(service.db_path)  # explicit initialized inventory
        released_media = []
        if with_image:
            from gateway import session_ingress_media as native_media
            real_release = native_media.release_admission_media
            def observed_release(db, admission_id):
                with db._read_ctx() as conn:
                    driver, = list(conn.execute('SELECT status,cancel_id,cancel_generation,execution_generation '
                        'FROM hosted_room_driver_tasks WHERE room_id=?', ('room',)))
                    pending = [r['state'] for _, r in records(conn, 'room')]
                released = real_release(db, admission_id)
                released_media.append((released, tuple(driver), pending))
                return released
            monkeypatch.setattr(native_media, 'release_admission_media', observed_release)
        if inventory == 'missing':
            authority.db._execute_write(lambda c: c.execute('DROP TABLE hosted_room_output_artifacts'))
        queued, allow_claim, started, release, interrupted = (asyncio.Event() for _ in range(5))
        calls = []
        original_drain = authority._drain
        if claim_wins:
            # Pause the real preclaim authorization AFTER its actual check. Stop
            # captures NULL, then the already-authorized claimant wins its CAS.
            import threading
            claim_barrier = threading.Event()
            loop = asyncio.get_running_loop()
            original_check = service.check_admission
            def checked(ref, row):
                result = original_check(ref, row)
                if row['status'] == 'queued':
                    loop.call_soon_threadsafe(queued.set)
                    assert claim_barrier.wait(8)
                return result
            monkeypatch.setattr(service, 'check_admission', checked)
            from gateway import session_finite
            original_execute = session_finite.execute_finite_admission
            async def before_execute(*args):
                started.set()
                await release.wait()
                return await original_execute(*args)
            monkeypatch.setattr(session_finite, 'execute_finite_admission', before_execute)
        else:
            async def drain(ref):
                queued.set()
                await allow_claim.wait()
                return await original_drain(ref)
            monkeypatch.setattr(authority, '_drain', drain)
        async def handle(event):
            calls.append(event.message_id)
            started.set()
            await release.wait()
            execution_result.get().update(result=dict(interrupted=True, final_response='', messages=[]), usage={})
            return ''
        runner._handle_message = handle
        runner._cached_agent_for = lambda _: SimpleNamespace(interrupt=interrupted.set)
        original_cancel = authority.cancel_queued
        async def claim_race(*args):
            claim_barrier.set()
            await asyncio.wait_for(started.wait(), 5)
            return await original_cancel(*args)
        if claim_wins:
            monkeypatch.setattr(authority, 'cancel_queued', claim_race)
        work = asyncio.create_task(execute_group_turn(authority, service, input_manifest=manifest))
        client = connection(authority, service)
        stop_succeeded = False
        try:
            await asyncio.wait_for(queued.wait(), 8)
            with authority.db._read_ctx() as conn:
                row = dict(conn.execute('SELECT * FROM session_admissions').fetchone())
                assert row['status'] == 'queued' and row['generation'] is None
                if mixed:
                    from gateway.hosted_room_input_reclamation import copy_path
                    ref, = conn.execute('SELECT copy_id FROM input_custody_refs WHERE admission_id=?',
                                        (row['admission_id'],)).fetchone()
                    retained = conn.execute('SELECT * FROM input_custody_copies WHERE copy_id=?', (ref,)).fetchone()
                    retained_path = copy_path(authority.db, dict(retained))
                    assert retained_path.read_bytes() == document_bytes
                    assert str(retained_path) in json.loads(row['payload_json'])['text']
                    assert row['payload_digest']
                    if with_image:
                        image_reference, = json.loads(row['payload_json'])['attachments_v1']['media']
                        image_path = Path(image_reference['path'])
                        assert image_path.read_bytes() == image_bytes
            if inventory == 'occupied':
                from gateway.hosted_room_artifacts import RoomArtifactScope
                task, = tasks.list_tasks(service.db_path, room_id='room')
                with authority.db._read_ctx() as conn:
                    snapshot, _ = service._cleanup_snapshot(conn, task)
                scope = RoomArtifactScope.from_mapping(snapshot['scope'])
                path = tmp_path / 'unexpected.txt'
                path.write_bytes(b'unclaimed inventory is NOT unlink authority')
                item = outbox.put_path(scope=scope, path=path)  # inventory-only fixture
            stopped = await dispatch(client, 'stop', room_id='room', cancel_id='before-claim')
            assert stopped.get('result') == {'cancelled': 1}, stopped
            stop_succeeded = True
            with authority.db._read_ctx() as conn:
                current = dict(conn.execute('SELECT * FROM session_admissions').fetchone())
                assert current['admission_id'] == row['admission_id']
                if mixed:
                    assert current['payload_json'] == row['payload_json']
                    assert current['payload_digest'] == row['payload_digest']
                    assert retained_path.read_bytes() == document_bytes
                    saved, = [r for _, r in records(conn, 'room')]
                    if claim_wins:
                        assert saved['binding']['admission']['admission_id'] == row['admission_id']
                        assert saved['binding']['input_binding']['copies'][0]['copy_id'] == ref
                    else:
                        assert saved['state'] == 'completed'
                        assert saved['disposition'] == 'never_executed'
                if claim_wins:
                    assert current['status'] == 'started' and type(current['generation']) is int
                    waiting, = [r for _, r in records(conn, 'room')]
                    assert waiting['state'] == 'waiting'
                    assert waiting['binding']['admission']['generation'] == current['generation']
                    assert waiting['original_binding']['admission']['generation'] is None
                else:
                    assert current['status'] == 'terminal' and current['outcome'] == 'cancelled'
                    assert current['generation'] is None and not calls
            if claim_wins:
                assert (interrupted.is_set() or
                        authority.pending_stops.get(current['target_session_id']) == current['generation'])
            repeated = await dispatch(client, 'stop', room_id='room', cancel_id='before-claim')
            assert 'result' in repeated, repeated
            if damage_media:
                assert image_path.read_bytes() == image_bytes
                if damage_media == 'missing':
                    image_path.unlink()
                else:
                    image_path.write_bytes(b'corrupt original native image')
        finally:
            allow_claim.set()
            release.set()
            if claim_wins:
                claim_barrier.set()
            if stop_succeeded:
                result = await asyncio.wait_for(work, 8)
            else:
                work.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await work
            service.runtime._thread = None
        service.prepare_room(result[4])
        service.prepare_room(result[4])
        with authority.db._read_ctx() as conn:
            final, = list(conn.execute('SELECT * FROM session_admissions'))
            assert final['status'] == 'terminal'
            assert final['generation'] == current['generation']
            if mixed:
                assert final['admission_id'] == row['admission_id']
                assert final['payload_json'] == row['payload_json']
                assert final['payload_digest'] == row['payload_digest']
                completed, = [r for _, r in records(conn, 'room')]
                if with_image and damage_media:
                    assert completed['state'] == 'waiting'
                    assert completed['blocked'] is True
                    assert completed['reason_code'] == 'input_binding_unavailable'
                    assert completed['binding']['admission']['admission_id'] == row['admission_id']
                    assert completed['binding']['input_binding']['copies'][0]['copy_id'] == ref
                else:
                    assert completed['state'] == 'completed', (completed['reason_code'], final['outcome'])
                    assert completed['disposition'] == ('local_cleanup' if claim_wins else 'never_executed')
                    assert 'binding' not in completed
            if inventory == 'ready':
                if damage_media:
                    with pytest.raises(RuntimeStoreError, match='output_cleanup_pending'):
                        require_room_retired(conn, 'room')
                else:
                    require_room_retired(conn, 'room')
            else:
                with pytest.raises(RuntimeStoreError, match='output_cleanup_pending'):
                    require_room_retired(conn, 'room')
                held, = [r for _, r in records(conn, 'room')]
                assert held['state'] == 'waiting'
                assert held['reason_code'] == ('inventory_unavailable' if inventory == 'missing' else 'unclaimed_output_inventory')
        if inventory == 'occupied':
            assert outbox.read(scope, item['artifact_id'])[1] == path.read_bytes()
        assert not calls  # stopped before handler entry, even when the claim CAS won
        if with_image:
            assert released_media, 'terminal admission must invoke the real native release'
            assert all(released == 0 for released, _, _ in released_media), released_media
            assert any(driver[0] == 'stopping' and pending == ['waiting']
                       for _, driver, pending in released_media), released_media
            assert any(driver[1:3] == ('before-claim', 1) and driver[0] in ('stopping', 'cancelled')
                       for _, driver, _ in released_media), released_media
            if not damage_media:
                assert image_path.read_bytes() == image_bytes
                from gateway.session_ingress_media import release_admission_media
                assert release_admission_media(authority.db, row['admission_id']) == 1
                assert not image_path.exists()
        if mixed:
            assert retained_path.read_bytes() == document_bytes
            assert not any(e['kind'] == 'message.member' for e in service._events('room'))
        assert tasks.get_task(service.db_path, result[3]['identity'])['execution_generation'] == 1


@pytest.mark.asyncio
async def test_terminal_worker_before_first_output_capture_retains_mixed_native_bytes(tmp_path, monkeypatch, request):
    """The driver can finish in its own transaction before capture_stopping."""
    import base64
    from gateway.session_ingress_media import release_admission_media
    from hermes_state_runtime import cancel_session_input

    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        initialize_inputs(authority, tmp_path, request)
        RoomArtifactOutbox(service.db_path)
        document_bytes = b'original stopped document'
        image_bytes = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=')
        document = service.attachments.put(room_id='room', upload_id='late-document',
            name='notes.txt', kind='file', mime='text/plain', data=document_bytes)
        image = service.attachments.put(room_id='room', upload_id='late-image',
            name='pixel.png', kind='image', mime='image/png', data=image_bytes)
        manifest = [{k: item[k] for k in ('attachment_id', 'name', 'kind', 'mime', 'size')}
                    for item in (document, image)]
        service.send(room_id='room', event_id='late-capture', payload=dict(
            thread_id='late-capture', text='@writer Read', attachments=manifest))
        queued, = tasks.list_tasks(service.db_path, room_id='room', status='queued')
        binding = service.bindings()[0]
        lease = tasks.acquire_lease(service.db_path, room_id='room',
            gateway_id=binding.gateway_id, authority_epoch=binding.authority_epoch,
            process_generation=service.runtime.process_generation, ttl_seconds=60, clock=time.time)
        attempt = tasks.start_task(service.db_path, queued['identity'], lease,
            expected_cancel_generation=0, clock=time.time)
        rpc = service._resolve_member_transport(binding, queued)
        sid = (await asyncio.to_thread(rpc.create, profile='default', source='bot_room',
                                       title='Group: room'))['session_id']
        receipt = await asyncio.to_thread(rpc.submit, profile='default', source='bot_room',
            session_id=sid, prompt=queued['payload']['prompt'], task=attempt.identity,
            execution_generation=attempt.execution_generation,
            attachments=queued['payload']['attachments'],
            on_terminal=lambda _: None)
        with authority.db._read_ctx() as conn:
            admission = dict(conn.execute('SELECT * FROM session_admissions WHERE admission_id=?',
                (receipt['admission_id'],)).fetchone())
            assert admission['status'] == 'queued' and admission['generation'] is None
            payload = json.loads(admission['payload_json'])
            image_ref, = payload['attachments_v1']['media']
            image_path = Path(image_ref['path'])
            assert image_path.read_bytes() == image_bytes
        stopping = tasks.begin_task_cancel(service.db_path, attempt.identity,
            cancel_id='late-stop', expected_cancel_generation=0, clock=time.time)
        assert cancel_session_input(authority.db, epoch=authority.epoch,
            admission_id=admission['admission_id'])['outcome'] == 'cancelled'
        terminal = tasks.complete_task_cancel(service.db_path, attempt.identity,
            cancel_id='late-stop', expected_cancel_generation=1, clock=time.time)
        with authority.db._read_ctx() as conn:
            assert not records(conn, 'room')
        assert release_admission_media(authority.db, admission['admission_id']) == 0
        assert image_path.read_bytes() == image_bytes
        service._capture_stopping_output(stopping, 'late-stop')
        with authority.db._read_ctx() as conn:
            waiting, = [r for _, r in records(conn, 'room')]
            assert waiting['state'] == 'waiting'
        def refuse_ack(*args, **kwargs):
            raise RuntimeError('synthetic Input acknowledgement failure')
        with monkeypatch.context() as failing:
            failing.setattr('hermes_state_input_custody.acknowledge_stopped_native_input', refuse_ack)
            with pytest.raises(RuntimeError, match='synthetic Input acknowledgement failure'):
                service._reconcile_stopped_output(terminal)
        with authority.db._read_ctx() as conn:
            after_failure, = [r for _, r in records(conn, 'room')]
            assert after_failure['state'] == 'waiting'
            assert not conn.execute("SELECT 1 FROM state_meta WHERE key LIKE "
                "'gateway.input.native-output-ack.v1:%'").fetchone()
        assert release_admission_media(authority.db, admission['admission_id']) == 0
        assert image_path.read_bytes() == image_bytes
        assert service._reconcile_stopped_output(terminal)
        with authority.db._read_ctx() as conn:
            completed, = [r for _, r in records(conn, 'room')]
            assert completed['state'] == 'completed' and completed['disposition'] == 'never_executed'
            ack_key, original_ack = conn.execute("SELECT key,value FROM state_meta WHERE key LIKE "
                "'gateway.input.native-output-ack.v1:%'").fetchone()
        ack = json.loads(original_ack)
        for damaged in ({**ack, 'stop': {**ack['stop'], 'cancel_id': 'foreign'}},
                        {**ack, 'native': {**ack['native'], 'generation': 999}},
                        {**ack, 'completion_digest': '0' * 64}, []):
            authority.db._execute_write(lambda c: c.execute('UPDATE state_meta SET value=? WHERE key=?',
                (json.dumps(damaged), ack_key)))
            assert release_admission_media(authority.db, admission['admission_id']) == 0
            assert image_path.read_bytes() == image_bytes
        authority.db._execute_write(lambda c: c.execute('UPDATE state_meta SET value=? WHERE key=?',
            (original_ack, ack_key)))
        assert release_admission_media(authority.db, admission['admission_id']) == 1
        assert not image_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['replay', 'retirement', 'task-retirement'])
async def test_legacy_completed_fact_compacts_without_reconstructing_input(tmp_path, monkeypatch, boundary):
    from tests.gateway.test_canonical_output_refusals import stranded
    from hermes_state_mutation_retirement import retire_prunable
    async with stranded(tmp_path, monkeypatch) as (authority, service, task, binding, pending, paths):
        service.prepare_room(binding)
        service.prepare_room(binding)
        with authority.db._read_ctx() as conn:
            done = dict(records(conn, 'room'))[key_for(task)]
        assert done['version'] == 2 and done['state'] == 'completed'
        assert not any(p.exists() for p in paths)
        # Version-1 representation of the SAME genuinely completed cleanup, not
        # fabricated terminal execution or removal. Keep its original binding.
        legacy = dict(pending, state='completed', blobs=[], reason_code='completed', next_attempt_at=0)
        authority.db._execute_write(lambda c: c.execute('UPDATE state_meta SET value=? WHERE key=?',
            (json.dumps(legacy), key_for(task))))
        sid = pending['binding']['admission']['target_session_id']
        if boundary == 'retirement':
            assert authority.db._execute_write(lambda c: retire_prunable(c, [sid])) == [sid]
        if boundary == 'task-retirement':
            # This fixture still has a stopping driver task and no terminal
            # publication; Retention must not erase its original execution.
            assert tasks.prune_published_terminal_tasks(service.db_path, room_id='room', clock=lambda: 10**12, retain=0) == 0
            assert tasks.get_task(service.db_path, task['identity'])['status'] == 'stopping'
        def forbidden(*args, **kwargs):
            raise AssertionError('legacy completed replay must not unlink or inspect live inputs')
        with monkeypatch.context() as guard:
            guard.setattr('gateway.hosted_room_input_retained.retained_hosted_input', forbidden)
            guard.setattr('gateway.hosted_room_output_discard.os.unlink', forbidden)
            assert service._reconcile_stopped_output(task)
        with authority.db._read_ctx() as conn:
            compact = dict(records(conn, 'room'))[key_for(task)]
        assert compact == done and 'binding' not in compact
