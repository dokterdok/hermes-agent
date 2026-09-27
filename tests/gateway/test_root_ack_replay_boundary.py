"""One isolated causal regression for the Input/Output ack replay boundary."""
import asyncio
import base64
import json
import time
from pathlib import Path

import pytest

from gateway import hosted_room_driver as tasks
from gateway.hosted_room_artifacts import RoomArtifactOutbox
from gateway.session_hosted_output_lifecycle import records
from gateway.session_ingress_media import release_admission_media
from hermes_state_runtime import cancel_session_input
from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_output_stop_corrections import initialize_inputs


@pytest.mark.asyncio
async def test_completed_output_replay_cannot_repair_foreign_input_ack(tmp_path, monkeypatch, request):
    """A real writer's receipt binds admission/copy; a replay is read-only."""
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        initialize_inputs(authority, tmp_path, request)
        RoomArtifactOutbox(service.db_path)
        image_bytes = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=')
        document = service.attachments.put(room_id='room', upload_id='ack-document',
            name='notes.txt', kind='file', mime='text/plain', data=b'original document')
        image = service.attachments.put(room_id='room', upload_id='ack-image',
            name='pixel.png', kind='image', mime='image/png', data=image_bytes)
        manifest = [{k: item[k] for k in ('attachment_id', 'name', 'kind', 'mime', 'size')}
                    for item in (document, image)]
        service.send(room_id='room', event_id='ack-replay', payload=dict(
            thread_id='ack-replay', text='@writer Read', attachments=manifest))
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
        admission_id = receipt['admission_id']
        with authority.db._read_ctx() as conn:
            raw = conn.execute('SELECT * FROM session_admissions WHERE admission_id=?',
                               (admission_id,)).fetchone()
            assert raw['status'] == 'queued'
            image_ref, = json.loads(raw['payload_json'])['attachments_v1']['media']
            path = Path(image_ref['path'])
            assert path.read_bytes() == image_bytes
        stopping = tasks.begin_task_cancel(service.db_path, attempt.identity,
            cancel_id='replay-stop', expected_cancel_generation=0, clock=time.time)
        assert cancel_session_input(authority.db, epoch=authority.epoch,
                                    admission_id=admission_id)['outcome'] == 'cancelled'
        terminal = tasks.complete_task_cancel(service.db_path, attempt.identity,
            cancel_id='replay-stop', expected_cancel_generation=1, clock=time.time)
        assert release_admission_media(authority.db, admission_id) == 0
        service._capture_stopping_output(stopping, 'replay-stop')
        assert service._reconcile_stopped_output(terminal)
        with authority.db._read_ctx() as conn:
            completed, = [record for _, record in records(conn, 'room')]
            assert completed['state'] == 'completed'
            ack_key, ack_value = conn.execute("SELECT key,value FROM state_meta WHERE key LIKE "
                "'gateway.input.native-output-ack.v1:%'").fetchone()
            ack = json.loads(ack_value)
            assert ack['admission']['admission_id'] == admission_id
            assert ack['native']['copy_id']
        # A committed completed record is not a writer callback on replay.
        for field, replacement in (('admission', {**ack['admission'], 'admission_id': 'foreign'}),
                                   ('native', {**ack['native'], 'copy_id': 'foreign'})):
            forged = {**ack, field: replacement}
            authority.db._execute_write(lambda conn: conn.execute(
                'UPDATE state_meta SET value=? WHERE key=?', (json.dumps(forged), ack_key)))
            assert service._reconcile_stopped_output(terminal)
            with authority.db._read_ctx() as conn:
                assert json.loads(conn.execute('SELECT value FROM state_meta WHERE key=?',
                                               (ack_key,)).fetchone()[0]) == forged
            assert release_admission_media(authority.db, admission_id) == 0
            assert path.read_bytes() == image_bytes
        # A missing receipt beside the same completed Output is equally inert.
        authority.db._execute_write(lambda conn: conn.execute(
            'DELETE FROM state_meta WHERE key=?', (ack_key,)))
        assert service._reconcile_stopped_output(terminal)
        with authority.db._read_ctx() as conn:
            assert conn.execute('SELECT 1 FROM state_meta WHERE key=?', (ack_key,)).fetchone() is None
        assert release_admission_media(authority.db, admission_id) == 0
        assert path.read_bytes() == image_bytes
        # Restore only the actual writer-issued receipt to demonstrate release.
        authority.db._execute_write(lambda conn: conn.execute(
            'INSERT INTO state_meta(key,value) VALUES(?,?)', (ack_key, ack_value)))
        assert release_admission_media(authority.db, admission_id) == 1
        assert not path.exists()
