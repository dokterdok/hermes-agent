"""Real event acceptance rechecks consent in its write transaction."""
import json
from pathlib import Path

import pytest

from gateway import hosted_rooms
from gateway.session_group_delegation import dispatch_owner_delegation
from gateway.session_group_home_access import dispatch_home_access
from gateway.session_group_messaging_send import send_from_home, send_from_peer
from gateway.session_hosted_service import _OWNER
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch
from tests.gateway.test_canonical_group_delegation import room as owner_room  # noqa: F401


@pytest.fixture
def room(owner_room, monkeypatch):
    authority, actor, service = owner_room
    other = Path(authority.profile_id) / 'profiles' / 'reviewer'
    other.mkdir(parents=True)
    from gateway import run
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {
        'hosted_rooms': {'profiles': {'reviewer': str(other)}}})
    roster = [{'member_id': 'home', 'profile': 'default', 'handle': 'home'},
              {'member_id': 'reviewer', 'profile': 'reviewer', 'handle': 'reviewer'}]
    authority.db._execute_write(lambda conn: conn.execute(
        'UPDATE hosted_rooms SET members_json=? WHERE room_id=?', (json.dumps(roster), 'room')))
    return authority, actor, service


def _home_args(service, guard=lambda: None):
    return dict(room=service._room('room'), guard=guard, command_id='message-1',
                text='@home Prepare the report', actor={'kind': 'user', 'id': 'messaging-owner', 'display_name': 'Owner'})


def _consent(authority, actor, enabled=True):
    return dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': enabled})


def test_home_text_uses_existing_planner_once_and_requires_consent(room):
    from gateway.hosted_room_driver import list_tasks
    authority, actor, service = room
    args = _home_args(service)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        send_from_home(authority, **args)
    _consent(authority, actor)
    event = send_from_home(authority, **args)
    repeated = send_from_home(authority, **args)
    assert repeated['idempotent'] and repeated['event_id'] == event['event_id']
    messages = [e for e in service._events('room') if e['kind'] == 'message.user']
    assert len(messages) == 1 and messages[0]['actor'] == args['actor']
    tasks = list_tasks(service.db_path, room_id='room', status='queued')
    assert len(tasks) == 1
    assert tasks[0]['payload']['target_member_id'] == 'home'


@pytest.mark.parametrize('change', ['consent', 'owner', 'authority', 'runtime', 'home_guard'])
def test_waiting_home_send_cannot_cross_permission_change(room, monkeypatch, change):
    authority, actor, service = room
    allowed = [True]
    def guard():
        if not allowed[0]:
            raise PermissionError('Home destination changed')
    _consent(authority, actor)
    args = _home_args(service, guard)
    original = hosted_rooms.append_event
    def change_before_append(*args, **kwargs):
        if change == 'consent':
            _consent(authority, actor, False)
        elif change == 'owner':
            authority.db._execute_write(lambda conn: conn.execute(
                'UPDATE state_meta SET value=? WHERE key=?', ('replacement', _OWNER + 'room')))
        elif change == 'authority':
            authority.db._execute_write(lambda conn: conn.execute(
                'UPDATE hosted_rooms SET authority_epoch=2 WHERE room_id=?', ('room',)))
        elif change == 'runtime':
            begin_runtime_epoch(authority.db, instance_id='replacement-runtime')
        else:
            allowed[0] = False
        return original(*args, **kwargs)
    monkeypatch.setattr(hosted_rooms, 'append_event', change_before_append)
    with pytest.raises((RuntimeStoreError, PermissionError)):
        send_from_home(authority, **args)
    assert not any(e['kind'] == 'message.user' for e in service._events('room'))


def test_revoked_home_cannot_replay_an_accepted_event(room):
    authority, actor, service = room
    args = _home_args(service)
    _consent(authority, actor)
    send_from_home(authority, **args)
    _consent(authority, actor, False)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        send_from_home(authority, **args)
    assert sum(e['kind'] == 'message.user' for e in service._events('room')) == 1


def test_peer_send_retains_exact_grant_through_append(room, monkeypatch):
    authority, actor, service = room
    grant = dispatch_owner_delegation(authority, actor, 'issue',
        {'room_id': 'room', 'member_id': 'home', 'request_id': 'return-control'})
    args = dict(room=service._room('room'), member_id='home', token=grant['control_token'],
        command_id='peer-message', text='@home Prepare the report', actor_display_name='Peer owner')
    event = send_from_peer(authority, **args)
    assert event['actor']['id'] == 'peer:home'
    original = hosted_rooms.append_event
    def revoke_before_append(*args, **kwargs):
        dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'home'})
        return original(*args, **kwargs)
    monkeypatch.setattr(hosted_rooms, 'append_event', revoke_before_append)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        send_from_peer(authority, **{**args, 'command_id': 'second-message'})
    assert sum(e['kind'] == 'message.user' for e in service._events('room')) == 1
