"""A bot on another profile's gateway must confirm Stop before its group chat moves on."""

import asyncio
import time

import pytest

from gateway import hosted_room_driver as tasks, hosted_rooms, session_hosted_transport as transport
from gateway.session_group_controls import _disband
from tests.gateway.test_session_hosted_transport import _SourceTask, _server


def test_unconfirmed_stop_holds_the_turn_and_disband_until_that_gateway_answers(
        hosted_owner, tmp_path, monkeypatch):
    authority, loop, _, _ = hosted_owner
    target = tmp_path / 'profiles' / 'other'
    target.mkdir(parents=True, mode=0o700)
    authority.profile_id = str(target)
    source = _SourceTask(tmp_path, monkeypatch, b'attachment')
    service, runtime, db = source.service, source.service.runtime, source.db.db_path
    runtime.prepare_room = runtime.publish_terminal = None
    hosted_rooms.append_event(db, room_id='room', event_id='input', kind='message.user',
        actor={'kind': 'user', 'id': 'alice'}, payload={'text': 'frozen'},
        authority_gateway_id=source.gateway, authority_epoch=1)
    servers = [_server(tmp_path), _server(target)]
    transport.install_hosted_transport(servers[0], source.authority, loop, attest=service.attest)
    transport.install_hosted_transport(servers[1], authority, loop, attest=lambda *a: None)
    for server in servers:
        assert asyncio.run_coroutine_threadsafe(server.start(), loop).result(10)
    try:
        binding = service.bindings()[0]
        rpc = service._resolve_member_transport(binding, source.task)
        sid = rpc.create(profile='other', source='bot_room', title='Group: room')['session_id']
        rpc.submit(profile='other', source='bot_room', session_id=sid, prompt='frozen',
                   task=source.identity, execution_generation=1, attachments=source.bound, on_terminal=None)
        tasks.begin_task_cancel(db, source.identity, cancel_id='stop', expected_cancel_generation=0,
                                clock=time.time)
        with monkeypatch.context() as unanswered:
            def no_answer(*_, **__):
                raise TimeoutError('that gateway did not answer')

            unanswered.setattr(transport, 'owner_request', no_answer)
            now = time.time() + runtime.indeterminate_defer_seconds + 1
            runtime.clock = lambda: now
            runtime._retry_stopping_tasks(binding, runtime._ensure_lease(binding))
            assert tasks.get_task(db, source.identity)['status'] == 'stopping'
            with pytest.raises(hosted_rooms.RoomStopPendingError):
                _disband(service, service, db, source.gateway, {'room_id': 'room'})
            assert hosted_rooms.room_state(db, room_id='room').get('disbanded_at') is None
        # That gateway is back: Stop reaches it, and the group chat can move on.
        runtime._retry_stopping_tasks(binding, runtime._ensure_lease(binding))
        assert tasks.get_task(db, source.identity)['status'] == 'cancelled'
        assert 'tombstone' in _disband(service, service, db, source.gateway, {'room_id': 'room'})
    finally:
        for server in servers:
            asyncio.run_coroutine_threadsafe(server.stop(), loop).result(10)
        source.db.close()
