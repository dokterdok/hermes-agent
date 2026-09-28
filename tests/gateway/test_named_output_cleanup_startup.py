"""Replay committed named-owner cleanup only after exact service registration."""
import json
import time

import pytest

from tests.gateway.test_hosted_mux_runtime import mux  # noqa: F401


def test_pending_named_output_cleanup_replays_after_service_registration(mux, monkeypatch):
    from gateway import hosted_room_driver as tasks
    from gateway import hosted_rooms, session_hosted_output_rpc as output
    from gateway import hosted_room_output_discard as primitive
    from gateway.session_authorities import owner_scope
    from gateway.session_hosted_service import CanonicalHostedRoomService, _ensure_hosted_service
    from gateway.session_hosted_transport import HostedRoomOwnerRPC, install_hosted_transport
    from hermes_state_runtime import claim_session_input, RuntimeStoreError

    runner, homes, loop, call = mux
    source = runner.session_authorities.require(homes['alpha'])
    target = runner.session_authorities.require(homes['beta'])
    for authority in (source, target):
        with owner_scope(authority):
            service = CanonicalHostedRoomService(authority, loop)
            authority.hosted_room_service = service
            install_hosted_transport(runner.session_control_server, authority, loop,
                                     attest=service.attest)
    with owner_scope(source):
        source.hosted_room_service.authorize_room('alice', 'cleanup-restart', create=True)
        hosted_rooms.create_room(source.db.db_path, room_id='cleanup-restart', name='Cleanup restart',
            authority_gateway_id=hosted_rooms.local_authority_gateway_id(),
            members=[{'member_id': 'helper', 'profile': 'beta', 'handle': 'helper'}])
    rpc = HostedRoomOwnerRPC(home=homes['beta'], source_home=homes['alpha'],
                            room_id='cleanup-restart', member_id='helper', profile='beta')
    sid = rpc.create(profile='beta', source='bot_room', title='Group: cleanup-restart')['session_id']
    identity = tasks.TaskIdentity('cleanup-restart', 'task', 'thread', 'turn')
    tasks.admit_task(source.db.db_path, identity,
        payload={'target_profile': 'beta', 'target_member_id': 'helper',
                 'source_event_seq': 1, 'prompt': 'input'}, clock=time.time)
    lease = tasks.acquire_lease(source.db.db_path, room_id=identity.room_id,
        gateway_id=hosted_rooms.local_authority_gateway_id(), authority_epoch=1,
        process_generation='fixture', ttl_seconds=30, clock=time.time)
    tasks.start_task(source.db.db_path, identity, lease, expected_cancel_generation=0,
                     clock=time.time)
    try:
        receipt = rpc.submit(profile='beta', source='bot_room', session_id=sid,
            prompt='input', task=identity, execution_generation=1,
            on_terminal=lambda row: None)
        row = claim_session_input(target.db, epoch=target.epoch, session_id=sid)
        assert row is not None
        assert row['admission_id'] == receipt['admission_id']
        with target.db._read_ctx() as conn:
            raw = output._admission(conn, row['admission_id'])
            assert raw is not None
            loaded = output._load_consent(conn, {**row, 'payload_digest': raw['payload_digest']})
        assert loaded is not None
        _, consent_json, _, scope = loaded
        old_service = target.hosted_room_service
        outbox = output._provider(old_service)
        outbox.put_bytes(scope=scope, data=b'pending owner output', source_name='report.txt')
        blobs = list(outbox.blob_root.iterdir())
        assert blobs and all(path.is_file() for path in blobs)

        def unavailable(*args, **kwargs):
            raise OSError('synthetic interruption after retirement commit')

        with monkeypatch.context() as interrupted:
            interrupted.setattr(primitive, 'cleanup_exact', unavailable)
            assert not output._stage_owner_cleanup(target, row, scope, consent_json,
                reason='producer_failed', allowed={('started', None)})

        def record():
            with target.db._read_ctx() as conn:
                saved = conn.execute('SELECT value FROM state_meta WHERE key=?',
                                     (output._failure_key(row['admission_id']),)).fetchone()
                assert saved is not None
                return json.loads(saved[0])

        pending = record()
        assert pending['state'] == 'pending' and pending['blobs']
        assert all(path.exists() for path in blobs)
        assert old_service.stop(timeout=5)
        target.hosted_room_service = None
        with pytest.raises(RuntimeStoreError, match='output_owner_unavailable'):
            output._provider(old_service)
        call(_ensure_hosted_service(runner, target))
        assert target.hosted_room_service is not old_service
        completed = record()
        assert completed['state'] == 'completed', completed
        assert completed['commitment'] == pending['commitment']
        assert completed['blobs'] == []
        assert all(not path.exists() for path in blobs)
        call(_ensure_hosted_service(runner, target))
        assert record() == completed
    finally:
        with rpc._lock:
            rpc.callbacks.clear()
        if rpc._monitor is not None:
            rpc._monitor.join(5)
            assert not rpc._monitor.is_alive()
