"""Route validates the exact accepted Input document custody before dequeue."""
import asyncio
import base64
from dataclasses import asdict
import json
import time
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError


@pytest.mark.parametrize(('mime', 'name', 'content'), [
    ('text/plain', 'notes.txt', b'accepted document bytes'),
    ('image/png', 'pixel.png', base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=')),
])
@pytest.mark.asyncio
async def test_document_admission_reconstructs_custody_and_denies_mutations(tmp_path, monkeypatch, mime, name, content):
    from gateway import hosted_room_driver as tasks
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    from gateway.hosted_room_input_custody import initialize_input_custody
    from gateway.hosted_room_input_reclamation import initialize_working_copies
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from hermes_state_runtime import begin_runtime_epoch, list_session_admissions
    from gateway.runtime_ownership import process_ownership
    import hermes_state

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    process_ownership.reserve([tmp_path])
    monkeypatch.setattr(hermes_state, 'DEFAULT_DB_PATH', tmp_path / 'state.db')
    reviewer = tmp_path / 'profiles' / 'reviewer'
    reviewer.mkdir(parents=True)
    (reviewer / 'profile.yaml').write_text('name: reviewer\n')
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {
        'model': {'default': 'fixture'}, 'platform_toolsets': {'cli': []},
        'hosted_rooms': {'profiles': {'reviewer': str(reviewer)}}})
    monkeypatch.setattr(run, '_resolve_gateway_model', lambda _: 'fixture')
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    try:
        initialize_input_custody(db)
        runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                                 config=GatewayConfig(), _cached_agent_for=lambda _: None)
        authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='test',
                                     db=db, epoch=begin_runtime_epoch(db, instance_id='test'))
        initialize_working_copies(db, epoch=authority.epoch)
        runner.session_authority = authority
        store._local_authority_epoch = authority.epoch
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        service = CanonicalHostedRoomService(authority, asyncio.get_running_loop())
        authority.hosted_room_service = service
        service.authorize_room('alice', 'room', create=True)
        service.create_room(room_id='room', name='Files', members=[
            {'member_id': 'writer', 'profile': 'default', 'handle': 'writer'},
            {'member_id': 'reviewer', 'profile': 'reviewer', 'handle': 'reviewer'}])
        attachments = HostedRoomAttachmentStore(db.db_path)
        meta = attachments.put(room_id='room', upload_id='upload', kind='image' if mime.startswith('image/') else 'file',
                               name=name, mime=mime, data=content)
        manifest = [{key: meta[key] for key in ('attachment_id', 'kind', 'name', 'size', 'mime')}]
        service.send(room_id='room', event_id='event', payload={
            'thread_id': 'thread', 'text': '@writer Read', 'attachments': manifest})
        queued, = tasks.list_tasks(db.db_path, room_id='room', status='queued')
        binding = service.bindings()[0]
        lease = tasks.acquire_lease(db.db_path, room_id='room', gateway_id=binding.gateway_id,
            authority_epoch=binding.authority_epoch, process_generation=service.runtime.process_generation,
            ttl_seconds=60, clock=time.time)
        attempt = tasks.start_task(db.db_path, queued['identity'], lease,
                                   expected_cancel_generation=0, clock=time.time)
        task = tasks.get_task(db.db_path, queued['identity'])
        rpc = service._resolve_member_transport(binding, task)
        sid = (await asyncio.to_thread(rpc.create, profile='default', source='bot_room',
                                       title='Group: room'))['session_id']
        receipt = await asyncio.to_thread(rpc.submit, profile='default', source='bot_room',
            session_id=sid, prompt=task['payload']['prompt'], attachments=task['payload']['attachments'],
            task=attempt.identity, execution_generation=attempt.execution_generation,
            on_terminal=lambda _: None)
        row, = list_session_admissions(db, session_id=sid, pending_only=True)
        assert row['admission_id'] == receipt['admission_id']
        assert row['status'] == 'queued'
        if mime == 'image/png':
            assert row['payload']['attachments_v1']['media_types'] == [mime]
            assert service.check_admission(rpc.ref, row) == task
            return
        with db._read_ctx() as conn:
            ref, = conn.execute('SELECT copy_id FROM input_custody_refs WHERE admission_id=?',
                                (row['admission_id'],)).fetchone()
            copy = conn.execute('SELECT * FROM input_custody_copies WHERE copy_id=?', (ref,)).fetchone()
        from gateway.hosted_room_input_reclamation import copy_path
        retained = copy_path(db, dict(copy))
        assert retained.read_bytes() == content
        assert str(retained) in row['payload']['text']
        assert service.check_admission(rpc.ref, row) == task
        for bad in ({**row, 'principal_id': 'foreign'},
                    {**row, 'admission_id': 'foreign'},
                    {**row, 'payload': {'text': 'changed'}},
                    {**row, 'request_id': 'hosted:' + json.dumps([asdict(attempt.identity), 999])}):
            with pytest.raises(RuntimeStoreError, match='permission_denied|admission_conflict'):
                service.check_admission(rpc.ref, bad)
        with db._lock:
            members_json, = db._conn.execute("SELECT members_json FROM hosted_rooms WHERE room_id='room'").fetchone()
            db._conn.execute("UPDATE hosted_rooms SET members_json=? WHERE room_id='room'", (json.dumps([]),))
            db._conn.commit()
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            service.check_admission(rpc.ref, row)
        with db._lock:
            db._conn.execute("UPDATE hosted_rooms SET members_json=? WHERE room_id='room'", (members_json,))
            db._conn.commit()
        assert service.check_admission(rpc.ref, row) == task
        retained.write_bytes(b'X' * len(content))
        with pytest.raises(RuntimeStoreError):
            service.check_admission(rpc.ref, row)
        retained.write_bytes(content)
        assert service.check_admission(rpc.ref, row) == task
        stopped = tasks.begin_task_cancel(db.db_path, attempt.identity, cancel_id='stop',
                                          expected_cancel_generation=0, clock=time.time)
        assert stopped['status'] == 'stopping'
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            service.check_admission(rpc.ref, row)
        assert service.check_admission(rpc.ref, {**row, 'status': 'started'}) == tasks.get_task(db.db_path, attempt.identity)
    finally:
        db.close()
        process_ownership.release(tmp_path)
