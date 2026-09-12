"""Home-controller consent is independent of Bot membership, never implicit."""
from dataclasses import replace
import json

import pytest

from gateway.session_group_home_access import dispatch_home_access, home_access_granted
from gateway.session_hosted_service import _OWNER
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch
from tests.gateway.test_canonical_group_delegation import room  # noqa: F401


def test_nonparticipant_home_requires_explicit_current_owner_consent(room):
    authority, actor, service = room
    roster = [{'member_id': 'pm', 'profile': 'pm', 'handle': 'pm'},
              {'member_id': 'builder', 'profile': 'builder', 'handle': 'builder'}]
    authority.db._execute_write(lambda conn: conn.execute(
        'UPDATE hosted_rooms SET members_json=? WHERE room_id=?', (json.dumps(roster), 'room')))
    assert not home_access_granted(authority, 'room')
    args = {'room_id': 'room', 'enabled': True}
    assert dispatch_home_access(authority, actor, 'groups.control.home.set', args)['enabled']
    assert home_access_granted(authority, 'room')
    authority.epoch = begin_runtime_epoch(authority.db, instance_id='ordinary-restart')
    assert home_access_granted(authority, 'room')
    dispatch_home_access(authority, actor, 'groups.control.home.set', {**args, 'enabled': False})
    assert not home_access_granted(authority, 'room')
    dispatch_home_access(authority, actor, 'groups.control.home.set', args)
    authority.db._execute_write(lambda conn: conn.execute('UPDATE state_meta SET value=? WHERE key=?',
        ('replacement', _OWNER + 'room')))
    assert not home_access_granted(authority, 'room')


@pytest.mark.asyncio
async def test_native_dispatch_preserves_actor_schema_and_epoch_boundaries(room):
    from types import SimpleNamespace
    from gateway.session_group_controls import dispatch_group_control
    authority, actor, service = room
    for denied in (replace(actor, subject='messaging-admin'), replace(actor, profile_id='foreign'),
                   replace(actor, capabilities=frozenset({'session:read'}))):
        with pytest.raises(RuntimeStoreError):
            dispatch_home_access(authority, denied, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
    for params in ({'room_id': 'room', 'enabled': 'true'},
                   {'room_id': 'room', 'enabled': True, 'owner': actor.subject}):
        with pytest.raises(RuntimeStoreError):
            dispatch_home_access(authority, actor, 'groups.control.home.set', params)
    assert not home_access_granted(authority, 'room')
    actor = replace(actor, capabilities=frozenset({'session:control', 'session:read'}))
    connection = SimpleNamespace(authority=authority, actor=actor)
    before = await dispatch_group_control(connection, 'groups.control.home.get', {'room_id': 'room'})
    assert before['enabled'] is False
    await dispatch_group_control(connection, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
    assert (await dispatch_group_control(connection, 'groups.control.home.get', {'room_id': 'room'}))['enabled']
    for params in ({'room_id': 'room', 'enabled': True, 'owner': 'someone'},
                   {'room_id': 'room', 'enabled': True, 'profile': 'foreign'}):
        with pytest.raises(RuntimeStoreError):
            await dispatch_group_control(connection, 'groups.control.home.set', params)
    dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
    authority.db._execute_write(lambda conn: conn.execute('UPDATE hosted_rooms SET authority_epoch=2 WHERE room_id=?', ('room',)))
    assert not home_access_granted(authority, 'room')


@pytest.mark.asyncio
async def test_visible_owner_permission_control_cannot_cross_room_authority_change(room):
    from types import SimpleNamespace
    from gateway.session_group_controls import dispatch_group_control
    authority, actor, service = room
    actor = replace(actor, capabilities=frozenset({'session:control', 'session:read'}))
    connection = SimpleNamespace(authority=authority, actor=actor)
    state = await dispatch_group_control(connection, 'groups.control.home.get', {'room_id': 'room'})
    params = {'room_id': 'room', 'enabled': True, 'expected_authority': state['authority']}
    assert (await dispatch_group_control(connection, 'groups.control.home.set', params))['enabled'] is True
    authority.db._execute_write(lambda conn: conn.execute('UPDATE hosted_rooms SET authority_epoch=2 WHERE room_id=?', ('room',)))
    with pytest.raises(RuntimeStoreError, match='stale_generation'):
        await dispatch_group_control(connection, 'groups.control.home.set', params)
    assert not home_access_granted(authority, 'room')
