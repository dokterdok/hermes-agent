"""Canonical deletion preserves the RoomLink no-new-work boundary."""
from types import SimpleNamespace

import pytest

from gateway import hosted_rooms as rooms
from gateway.hosted_room_driver import RoomUnavailableError
from gateway.session_group_controls import _group
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch


@pytest.mark.parametrize('awaiting_stop', [False, True])
def test_disband_fences_before_stop_and_retains_accepted_history(tmp_path, monkeypatch, awaiting_stop):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with SessionDB(tmp_path / 'state.db') as db:
        authority = SimpleNamespace(db=db, profile_id=str(tmp_path),
            epoch=begin_runtime_epoch(db, instance_id='test'))
        service = CanonicalHostedRoomService(authority, None)
        authority.hosted_room_service = service
        service.authorize_room('owner', 'room', create=True)
        gateway_id = rooms.local_authority_gateway_id()
        rooms.create_room(db.db_path, room_id='room', name='Room', authority_gateway_id=gateway_id,
            members=[{'member_id': 'one', 'profile': 'default', 'handle': 'one'},
                     {'member_id': 'two', 'profile': 'other', 'handle': 'two'}])
        monkeypatch.setattr(service.runtime, 'status', lambda: {'running': True})

        def append(event_id):
            return rooms.append_event(db.db_path, room_id='room', event_id=event_id,
                kind='message.user', actor={'kind': 'user', 'id': 'owner'},
                payload={'text': 'Accepted work'}, authority_gateway_id=gateway_id, authority_epoch=1)

        accepted = append('accepted')
        calls = []

        def stop(room_id, *, cancel_id, require_acknowledged):
            assert room_id == 'room' and require_acknowledged
            assert append('accepted')['seq'] == accepted['seq']
            with pytest.raises(rooms.HostedRoomError, match='being disbanded'):
                append('late')
            calls.append('stop')
            if awaiting_stop:
                raise RuntimeError('waiting for current work')

        monkeypatch.setattr(service, 'stop_room', stop)
        monkeypatch.setattr(service, 'revoke_room_routes', lambda room_id: calls.append('revoke'))
        args = (authority, SimpleNamespace(subject='owner'), tmp_path, 'groups.disband', {'room_id': 'room'})
        if awaiting_stop:
            with pytest.raises(RuntimeError, match='waiting for current work'):
                _group(*args)
            assert calls == ['stop']
            assert rooms.room_state(db.db_path, room_id='room').get('disbanded_at') is None
            restarted = CanonicalHostedRoomService(authority, None)
            with pytest.raises(RoomUnavailableError, match='being disbanded'):
                restarted._require_work_open('room')
        else:
            assert _group(*args)['tombstone']['room_id'] == 'room'
            assert calls == ['stop', 'revoke']
        assert rooms.read_events(db.db_path, room_id='room', include_disbanded=True)['events'][0]['event_id'] == 'accepted'
