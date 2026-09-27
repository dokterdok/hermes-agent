"""Real hosted image settlement, refused preparation, and old native-only inventory."""
import asyncio
import hashlib
from pathlib import Path

import pytest

from gateway.hosted_room_driver import TaskIdentity
from gateway.hosted_room_input_preparation import (
    prepare_hosted_input, reconstruct_accepted_payload, reconstruct_attested_payload,
)
from gateway.hosted_room_input_custody import initialize_input_custody
from gateway.hosted_room_input_reclamation import (
    collect_legacy_input_aliases, collect_working_copies, initialize_working_copies,
)
from gateway.session_contract import Submission
from gateway.session_ingress_media import _media_root
from hermes_state_input_custody import PreparedInputHandle
from hermes_state_runtime import (
    RuntimeStoreError, admit_session_input, claim_session_input, get_session_admission,
    settle_session_input,
)
from tests.gateway.input_reclamation_fixtures import owned, close, rpc_files, retire_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 2])
async def test_terminal_hosted_image_retry_after_real_settlement(tmp_path, monkeypatch, count):
    db, owner = owned(tmp_path, monkeypatch)
    try:
        rpc, bound = rpc_files(tmp_path, owner, count=count, image=True, named=True)
        params = dict(task=TaskIdentity('room', 'task', 'thread', 'turn'), execution_generation=1,
            prompt='read', attachments=[item for item, _ in bound], on_terminal=lambda value: None)
        first = await rpc._submit(params)
        row = get_session_admission(db, admission_id=first['admission_id'])
        image = Path(row['payload']['attachments_v1']['media'][0]['path'])
        executed = []
        async def inert(authority, ref, admission):
            assert image.exists()
            executed.append(admission['admission_id'])
            return 'inert result'
        monkeypatch.setattr('gateway.session_finite.execute_finite_admission', inert)
        await owner._drain(rpc.ref)
        await asyncio.sleep(0)  # Flush the existing terminal waiter, not a timing assertion.
        terminal = get_session_admission(db, admission_id=first['admission_id'])
        assert terminal['status'] == 'terminal' and terminal['outcome'] == 'completed'
        assert not image.exists(), 'real settlement must release the image before retry'
        def no_capture(*args, **kwargs):
            pytest.fail('accepted retry recaptured bytes')
        monkeypatch.setattr('gateway.session_ingress_media.capture_native_media', no_capture)
        monkeypatch.setattr('gateway.hosted_room_input_preparation._copy', no_capture)
        receipts = []
        params['on_terminal'] = receipts.append
        retry = await rpc._submit(params)
        assert retry['admission_id'] == first['admission_id'] and retry['status'] == 'terminal'
        assert receipts[0]['text'] == 'inert result'
        digests = [hashlib.sha256(data).hexdigest() for _, data in bound]
        assert reconstruct_attested_payload(db, 'read', params['attachments'], digests, row) == row['payload']
        for retired in (False, True):
            if retired:
                retire_metadata(db, row['admission_id'])
                from hermes_state_mutation_retirement import RETIRED_PREFIX
                db._execute_write(lambda conn: conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)',
                    (RETIRED_PREFIX + rpc.ref.session_id, '{}')))
                collect_working_copies(db, epoch=owner.epoch)
                prepared = prepare_hosted_input(rpc, request_id=row['request_id'], prompt='read',
                    attachments=params['attachments'])
                receipt = await owner.submit(rpc.principal, Submission(row['request_id'], rpc.ref, prepared.payload, 'queue'),
                    _input_custody=prepared.handle)
                assert receipt.admission_id == first['admission_id'] and receipt.status == 'terminal'
            assert reconstruct_accepted_payload(rpc, 'read', params['attachments'], row) == row['payload']
            assert reconstruct_attested_payload(db, 'read', params['attachments'], digests, row) == row['payload']
            with pytest.raises(RuntimeStoreError, match='admission_conflict'):
                reconstruct_attested_payload(db, 'changed', params['attachments'], digests, row)
            changed = digests[:-1] + ['0' * 64]
            with pytest.raises(RuntimeStoreError, match='admission_conflict'):
                reconstruct_attested_payload(db, 'read', params['attachments'], changed, row)
        await owner._drain(rpc.ref)
        assert executed == [first['admission_id']] and not image.exists()
        rpc.authorizer = lambda *args: False
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await rpc._dispatch_owned('submit', params)
    finally:
        close(db, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('held', [False, True])
async def test_refused_mixed_preparation_native_bytes_are_collectible(tmp_path, monkeypatch, held):
    db, owner = owned(tmp_path, monkeypatch)
    try:
        rpc, bound = rpc_files(tmp_path, owner, count=2, image=True)
        manifest = [item for item, _ in bound]
        image_digest = hashlib.sha256(bound[-1][1]).hexdigest()
        image = _media_root() / image_digest / (image_digest + '.png')
        if held:
            saved = prepare_hosted_input(rpc, request_id='control', prompt='keep', attachments=manifest)
            await owner.submit(rpc.principal, Submission('control', rpc.ref, saved.payload, 'queue'),
                _input_custody=saved.handle)
        def revoke(*args, **kwargs):
            result = prepare_hosted_input(*args, **kwargs)
            assert image.exists(), 'exercise actual canonical capture'
            rpc.authorizer = lambda *args: False
            return result
        monkeypatch.setattr('gateway.hosted_room_input_preparation.prepare_hosted_input', revoke)
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await rpc._submit(dict(task=TaskIdentity('room', 'task', 'thread', 'turn'), execution_generation=1,
                prompt='read', attachments=manifest, on_terminal=lambda value: None))
        assert db._conn.execute('SELECT count(*) FROM session_admissions').fetchone()[0] == int(held)
        db._execute_write(lambda conn: conn.execute('UPDATE input_custody_preparations SET expires_at=0'))
        collect_working_copies(db, epoch=owner.epoch)
        # Explicit exclusive pre-ingress opportunity, never online housekeeping.
        initialize_working_copies(db, epoch=owner.epoch)
        collect_legacy_input_aliases(db, epoch=owner.epoch)
        assert image.exists() is held
    finally:
        close(db, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('damaged_proof', ['absent', 'foreign'])
async def test_interrupted_consumed_native_image_waits_for_exact_raw_retirement(tmp_path, monkeypatch, request, damaged_proof):
    from gateway.hosted_room_input_custody import custody_holds
    from gateway.session_ingress_media import release_admission_media
    from hermes_state_mutation_retirement import retire_prunable
    from hermes_state_terminal import ADMISSION_PREFIX

    import time
    from gateway.hosted_room_artifacts import RoomArtifactOutbox
    from gateway import hosted_room_driver as tasks
    from gateway.session_hosted_output_lifecycle import records
    from tests.gateway.test_canonical_hosted_outputs import owner as output_owner
    from tests.gateway.test_canonical_output_stop_corrections import initialize_inputs

    async with output_owner(tmp_path, monkeypatch) as (authority, service, runner):
        initialize_inputs(authority, tmp_path, request)
        from hermes_state import SessionDB
        from typing import cast
        db = cast(SessionDB, authority.db)
        from tests.gateway.test_api_media_retention import PNG
        image_bytes = PNG
        document = service.attachments.put(room_id='room', upload_id='document',
            name='notes.txt', kind='file', mime='text/plain', data=b'original document')
        image_upload = service.attachments.put(room_id='room', upload_id='image',
            name='pixel.png', kind='image', mime='image/png', data=image_bytes)
        manifest = [{k: item[k] for k in ('attachment_id', 'name', 'kind', 'mime', 'size')}
                    for item in (document, image_upload)]
        service.send(room_id='room', event_id='interrupted', payload=dict(
            thread_id='interrupted', text='@writer Read', attachments=manifest))
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
            attachments=queued['payload']['attachments'], on_terminal=lambda _: None)
        admission = get_session_admission(db, admission_id=receipt['admission_id'])
        assert admission is not None
        reference, = admission['payload']['attachments_v1']['media']
        image = Path(reference['path'])
        assert image.read_bytes() == image_bytes
        claimed = claim_session_input(db, epoch=authority.epoch, session_id=rpc.ref.session_id)
        assert claimed is not None and claimed['admission_id'] == receipt['admission_id']
        terminal = settle_session_input(db, epoch=authority.epoch, admission_id=receipt['admission_id'],
            generation=claimed['generation'], outcome='interrupted')
        assert terminal['status'] == 'terminal' and terminal['outcome'] == 'interrupted'
        with db._read_ctx() as conn:
            prepared, = conn.execute('SELECT preparation_id FROM input_custody_preparations WHERE admission_id=?',
                                     (receipt['admission_id'],)).fetchall()
        db._execute_write(lambda conn: conn.execute(
            'UPDATE input_custody_preparations SET expires_at=0 WHERE preparation_id=?',
            (prepared['preparation_id'],)))
        with db._read_ctx() as conn:
            assert custody_holds(conn, db.db_path, reference)
        assert release_admission_media(db, receipt['admission_id']) == 0
        assert collect_legacy_input_aliases(db, epoch=authority.epoch)['removed'] == 0
        assert image.read_bytes() == image_bytes

        # Retirement first refuses the still-running driver; Output must finish its
        # physical obligation through its own lifecycle before the raw writer can retire.
        assert db._execute_write(lambda conn: retire_prunable(conn, [sid])) == []
        stopping = tasks.begin_task_cancel(service.db_path, attempt.identity,
            cancel_id='interrupted-stop', expected_cancel_generation=0, clock=time.time)
        assert stopping['status'] == 'stopping'
        RoomArtifactOutbox(service.db_path)
        service._capture_stopping_output(stopping, 'interrupted-stop')
        with db._read_ctx() as conn:
            pending, = [record for _, record in records(conn, 'room')]
            assert pending['state'] == 'waiting'
        assert release_admission_media(db, receipt['admission_id']) == 0
        assert image.read_bytes() == image_bytes
        assert db._execute_write(lambda conn: retire_prunable(conn, [sid])) == []
        driver_terminal = tasks.complete_task_cancel(service.db_path, attempt.identity,
            cancel_id='interrupted-stop', expected_cancel_generation=1, clock=time.time)
        assert driver_terminal['status'] == 'cancelled'
        assert service._reconcile_stopped_output(driver_terminal)
        service.prepare_room(binding)
        with db._read_ctx() as conn:
            completed, = [record for _, record in records(conn, 'room')]
            assert completed['state'] == 'completed'
            assert custody_holds(conn, db.db_path, reference)
        assert release_admission_media(db, receipt['admission_id']) == 0
        assert image.read_bytes() == image_bytes

        # Completed Output is not raw retirement; only this writer emits the proof.
        assert db._execute_write(lambda conn: retire_prunable(conn, [sid])) == [sid]
        with db._read_ctx() as conn:
            assert conn.execute('SELECT 1 FROM session_admissions WHERE admission_id=?',
                                (receipt['admission_id'],)).fetchone() is None
        key = ADMISSION_PREFIX + receipt['admission_id']
        with db._read_ctx() as conn:
            saved = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()[0]
        def damage(conn):
            if damaged_proof == 'absent':
                conn.execute('DELETE FROM state_meta WHERE key=?', (key,))
            else:
                conn.execute("UPDATE state_meta SET value=json_set(value,'$.principal_id','foreign') WHERE key=?", (key,))
        db._execute_write(damage)
        with db._read_ctx() as conn:
            assert custody_holds(conn, db.db_path, reference)
        assert collect_legacy_input_aliases(db, epoch=authority.epoch)['removed'] == 0
        assert image.read_bytes() == image_bytes
        db._execute_write(lambda conn: conn.execute('INSERT OR REPLACE INTO state_meta(key,value) VALUES(?,?)',
                                                   (key, saved)))
        with db._read_ctx() as conn:
            assert not custody_holds(conn, db.db_path, reference)
        assert collect_legacy_input_aliases(db, epoch=authority.epoch)['removed'] == 1
        assert not image.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertainty', ['live', 'foreign_admission'])
async def test_consumed_native_image_rejects_live_or_foreign_admission(tmp_path, monkeypatch, uncertainty):
    from hermes_state_input_custody import copy_is_held

    db, owner = owned(tmp_path, monkeypatch)
    try:
        rpc, bound = rpc_files(tmp_path, owner, count=2, image=True)
        prepared = prepare_hosted_input(rpc, request_id='hosted:uncertain', prompt='read',
                                        attachments=[item for item, _ in bound])
        assert isinstance(prepared.handle, PreparedInputHandle)
        receipt = await owner.submit(rpc.principal, Submission('hosted:uncertain', rpc.ref,
            prepared.payload, 'queue'), _input_custody=prepared.handle)
        row = get_session_admission(db, admission_id=receipt.admission_id)
        assert row is not None
        reference, = row['payload']['attachments_v1']['media']
        image = Path(reference['path'])
        if uncertainty == 'foreign_admission':
            claimed = claim_session_input(db, epoch=owner.epoch, session_id=rpc.ref.session_id)
            assert claimed is not None
            settle_session_input(db, epoch=owner.epoch, admission_id=receipt.admission_id,
                                 generation=claimed['generation'], outcome='completed')
            db._execute_write(lambda conn: conn.execute(
                "UPDATE session_admissions SET principal_id='foreign' WHERE admission_id=?",
                (receipt.admission_id,)))
        db._execute_write(lambda conn: conn.execute(
            'UPDATE input_custody_preparations SET expires_at=0 WHERE preparation_id=?',
            (prepared.handle.preparation_id,)))
        with db._read_ctx() as conn:
            copy = conn.execute("SELECT * FROM input_custody_copies WHERE namespace='native'").fetchone()
            assert copy is not None and copy_is_held(conn, copy, float('inf'))
        assert collect_legacy_input_aliases(db, epoch=owner.epoch)['removed'] == 0
        assert image.read_bytes() == bound[-1][1]
    finally:
        close(db, tmp_path)


@pytest.mark.asyncio
async def test_native_only_legacy_document_inventory_drains_after_positive_retirement(tmp_path, monkeypatch):
    from gateway.session_hosted_attachments import submission_payload
    db, owner = owned(tmp_path, monkeypatch, initialize=False)
    try:
        # Remove only the fixture's empty initialization marker to model pre-v2 storage.
        from gateway.hosted_room_input_custody import _READY
        db._execute_write(lambda conn: conn.execute('DELETE FROM state_meta WHERE key=?', (_READY,)))
        rpc, bound = rpc_files(tmp_path, owner)
        payload = submission_payload(rpc, 'read', [item for item, _ in bound])
        assert set(payload) == {'text'}
        old = Path(payload['text'].split('file: ', 1)[1].strip())
        assert old.parent.parent == _media_root() and old.read_bytes() == bound[0][1]
        row = admit_session_input(db, epoch=owner.epoch, principal_id=rpc.principal.subject,
            session_id=rpc.ref.session_id, request_id='hosted:old', payload=payload)
        assert db._conn.execute('SELECT count(*) FROM session_admissions').fetchone()[0] == 1
        initialize_input_custody(db)
        initialize_working_copies(db, epoch=owner.epoch)
        assert collect_legacy_input_aliases(db, epoch=owner.epoch)['removed'] == 0
        assert old.exists()
        # Actual inert settlement; retirement metadata mirrors the canonical retained shape.
        async def inert(*args):
            return 'done'
        monkeypatch.setattr('gateway.session_finite.execute_finite_admission', inert)
        await owner._drain(rpc.ref)
        assert get_session_admission(db, admission_id=row['admission_id'])['status'] == 'terminal'
        assert old.exists()  # Opaque documents are not native admission deletion candidates.
        retire_metadata(db, row['admission_id'])
        initialize_input_custody(db)
        initialize_working_copies(db, epoch=owner.epoch)
        collect_legacy_input_aliases(db, epoch=owner.epoch)
        assert not old.exists(), 'sole native-only inventory must become collectible work'
        assert db._conn.execute('SELECT count(*) FROM gateway_legacy_input_paths').fetchone()[0] == 0
        initialize_working_copies(db, epoch=owner.epoch)
        assert collect_legacy_input_aliases(db, epoch=owner.epoch)['removed'] == 0
    finally:
        close(db, tmp_path)
