"""Canonical deletion preserves the RoomLink no-new-work boundary."""
from types import SimpleNamespace

import pytest

from gateway import hosted_rooms as rooms
from gateway.hosted_room_driver import RoomUnavailableError
from gateway.session_group_controls import _group
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch
from hermes_state_runtime import RuntimeStoreError


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


@pytest.mark.parametrize('custody', ['route', 'pending', 'both', 'completed', 'none'])
def test_unavailable_coordinator_cannot_skip_route_retirement(tmp_path, monkeypatch, custody):
    from gateway import hosted_room_link_records as records, hosted_room_links as links
    from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with SessionDB(tmp_path / 'state.db') as db:
        authority = SimpleNamespace(db=db, profile_id=str(tmp_path),
            epoch=begin_runtime_epoch(db, instance_id='test'))
        service = CanonicalHostedRoomService(authority, None)
        authority.hosted_room_service = service
        service.authorize_room('owner', 'room', create=True)
        gateway = rooms.local_authority_gateway_id()
        rooms.create_room(db.db_path, room_id='room', name='Room', authority_gateway_id=gateway,
            members=[{'member_id': 'one', 'profile': 'default', 'handle': 'one'},
                     {'member_id': 'two', 'profile': 'other', 'handle': 'two'}])
        if custody in {'route', 'both'}:
            catalog = GatewayRoomCatalog.from_mapping(catalog_mapping(
                installation_id='peer', target_profile='other', persistent_process=True))
            links.save_room_link(db.db_path, links.make_stored_link(
                room_id='room', member_id='two', target_url='http://127.0.0.1:9999',
                target_profile='other', grant='stored-grant', catalog=catalog,
                cancellation_scope_id='cancel', trace_id='trace'))
        if custody in {'pending', 'both', 'completed'}:
            service.begin_room_disband('room')
        if custody == 'completed':
            records.complete_room_link_retirement(db.db_path, room_id='room',
                authority_gateway_id=gateway, authority_epoch=1)
        before = records.list_room_link_records(db.db_path, room_id='room')
        assert not service.runtime.status()['running']
        args = (authority, SimpleNamespace(subject='owner'), tmp_path, 'groups.disband', {'room_id': 'room'})
        if custody in {'route', 'pending', 'both'}:
            with pytest.raises(RuntimeStoreError, match='runtime_coordination_required'):
                _group(*args)
            assert rooms.room_state(db.db_path, room_id='room').get('disbanded_at') is None
            assert records.list_room_link_records(db.db_path, room_id='room') == before
        else:
            assert _group(*args)['tombstone']['room_id'] == 'room'
