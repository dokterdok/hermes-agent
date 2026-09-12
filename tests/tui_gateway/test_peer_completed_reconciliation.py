"""Late completion reconciles a planner-created task; no new target admission."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_peer_canonical_controls import peer_target, target  # noqa: F401


@pytest.mark.asyncio
async def test_late_completion_publishes_once_without_new_generation(peer_target):
    from gateway import hosted_room_driver as tasks
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from gateway.platforms.api_server_authority_runs import run_admission
    from hermes_state import SessionDB
    from hermes_state_runtime import (
        RuntimeStoreError, begin_runtime_epoch, claim_session_input,
        get_session_admission, list_session_admissions, settle_session_input,
    )
    from tui_gateway.hosted_room_peer_transport import build_member_dispatch

    p = peer_target
    with SessionDB(p.peer.receipt_db_path) as db:
        owner = SimpleNamespace(db=db, profile_id=str(p.peer.receipt_db_path.parent),
            epoch=begin_runtime_epoch(db, instance_id='source'))
        service = CanonicalHostedRoomService(owner, asyncio.get_running_loop())
        service.authorize_room('alice', 'room', create=True)
        service.create_room(room_id='room', name='Room', members=[
            {'member_id': 'host', 'profile': 'default', 'handle': 'host'},
            {'member_id': 'helper', 'profile': p.target.profile, 'handle': 'helper', 'target': {
                'kind': 'peer', 'installation_id': p.catalog.installation_id,
                'peer_id': p.catalog.installation_id, 'profile': p.target.profile,
                'capability_digest': p.catalog.catalog_digest}}])
        service.register_peer_route(room_id='room', member_id='helper', route=p.route,
            client=p.peer, target_url=p.peer.base_url, catalog=p.catalog)
        service.send(room_id='room', event_id='input', payload={
            'text': '@helper Inspect fixture', 'thread_id': 'thread'})
        task, = tasks.list_tasks(db.db_path, room_id='room', status='queued')
        identity, payload = task['identity'], task['payload']
        # Retained deferred state is fixture input; do not simulate owner loss.
        db._execute_write(lambda conn: conn.execute(
            "UPDATE hosted_room_driver_tasks SET status='deferred', execution_generation=7, "
            "result_json=?, terminal_at=? WHERE room_id=? AND task_id=?",
            (json.dumps({'reason': 'member_unavailable', 'retryable': True}), time.time(),
             identity.room_id, identity.task_id)))
        service.prepare_room(p.binding)
        assert any(e['kind'] == 'turn.deferred' for e in service._events('room'))
        dispatch = build_member_dispatch(binding=p.binding, route=p.route, room_id='room',
            task_id=identity.task_id, target_profile=p.target.profile, execution_generation=7,
            source_event_seq=payload['source_event_seq'], prompt=payload['prompt'], trace_id=p.route.trace_id)
        receipt = await asyncio.to_thread(p.peer.dispatch, dispatch=dispatch.as_mapping(), grant=p.route.grant)
        authority, admitted = run_admission(p.target.adapter, receipt['run_id'])
        claimed = claim_session_input(authority.db, epoch=authority.epoch,
                                       session_id=admitted['target_session_id'])
        settle_session_input(authority.db, epoch=authority.epoch, admission_id=claimed['admission_id'],
            generation=claimed['generation'], outcome='completed',
            result={'result': {'final_response': 'Completed exactly once.'}, 'usage': {}})
        remote_before = get_session_admission(authority.db, admission_id=claimed['admission_id'])
        args = dict(room_id='room', member_id='helper', task_id=identity.task_id, execution_generation=7)
        before = tasks.get_task(db.db_path, identity)
        lease = service.runtime._ensure_lease(p.binding)
        for generation, cancellation in ((8, 0), (7, 1)):
            with pytest.raises(tasks.StaleTaskError):
                tasks.resolve_deferred_completion(db.db_path, identity, lease,
                    expected_execution_generation=generation, expected_cancel_generation=cancellation,
                    settlement_id='peer-run:' + receipt['run_id'], result={'text': 'must not commit'}, clock=time.time)
            assert tasks.get_task(db.db_path, identity) == before
        result = await asyncio.to_thread(service.retry_room_task, **args)
        assert result['status'] == 'settled'
        assert result['execution_generation'] == 7 and result['cancel_generation'] == 0
        assert result['result']['text'] == 'Completed exactly once.'
        assert result['settlement_id'] == 'peer-run:' + receipt['run_id']
        repeated = await asyncio.to_thread(service.retry_room_task, **args)
        assert repeated['idempotent'] and repeated['status'] == 'settled'
        with pytest.raises(RuntimeStoreError, match='stale_generation'):
            await asyncio.to_thread(service.retry_room_task, **{**args, 'execution_generation': 8})
        events = service._events('room')
        assert sum(e['kind'] == 'message.member' for e in events) == 1
        assert sum(e['kind'] == 'turn.settled' for e in events) == 1
        assert not tasks.list_tasks(db.db_path, room_id='room', status='queued')
        assert p.peer._receipt(identity.task_id, 7)['run_id'] == receipt['run_id']
        assert get_session_admission(authority.db, admission_id=claimed['admission_id']) == remote_before
        assert len(list_session_admissions(authority.db,
            session_id=claimed['target_session_id'], pending_only=False)) == 1
