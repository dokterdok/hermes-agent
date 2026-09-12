"""Room-specific grants retain canonical issuer identity across restarts."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway import hosted_room_controls as controls, hosted_rooms
from gateway.session_contract import Principal
from gateway.session_group_delegation import dispatch_delegated_group_control, dispatch_owner_delegation
from gateway.session_hosted_service import CanonicalHostedRoomService, _OWNER
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with SessionDB(tmp_path / 'state.db') as db:
        authority = SimpleNamespace(db=db, profile_id=str(tmp_path),
            epoch=begin_runtime_epoch(db, instance_id='first'))
        service = CanonicalHostedRoomService(authority, None)
        authority.hosted_room_service = service
        service.authorize_room('native-owner', 'room', create=True)
        hosted_rooms.create_room(db.db_path, room_id='room', name='Shared',
            authority_gateway_id=hosted_rooms.local_authority_gateway_id(),
            members=[{'member_id': 'home', 'profile': 'default', 'handle': 'home'}])
        actor = Principal('native-owner', str(tmp_path), frozenset({'session:control'}), 'native')
        yield authority, actor, service


@pytest.mark.asyncio
async def test_grant_survives_restart_but_not_owner_change_or_other_room(room):
    authority, actor, service = room
    params = dict(room_id='room', member_id='home', request_id='setup')
    issued = dispatch_owner_delegation(authority, actor, 'issue', params)
    assert dispatch_owner_delegation(authority, actor, 'issue', params)['control_token'] == issued['control_token']
    async def read(**changes):
        return await dispatch_delegated_group_control(authority, room_id='room', member_id='home',
            token=issued['control_token'], method='groups.state', params={'room_id': 'room', **changes})
    assert (await read())['room']['name'] == 'Shared'
    authority.epoch = begin_runtime_epoch(authority.db, instance_id='second')
    authority.hosted_room_service = CanonicalHostedRoomService(authority, None)
    assert (await read())['room']['name'] == 'Shared'
    with pytest.raises(RuntimeStoreError):
        await read(room_id='another-room')
    for method in ('session.list', 'groups.send', 'groups.approve', 'groups.control.invite'):
        with pytest.raises(RuntimeStoreError):
            await dispatch_delegated_group_control(authority, room_id='room', member_id='home',
                token=issued['control_token'], method=method, params={'room_id': 'room'})
    authority.db._execute_write(lambda conn: conn.execute('UPDATE state_meta SET value=? WHERE key=?',
        ('replacement-owner', _OWNER + 'room')))
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await read()
    replacement = replace(actor, subject='replacement-owner')
    with pytest.raises(RuntimeStoreError, match='control_reauthorization_required'):
        dispatch_owner_delegation(authority, replacement, 'issue', {**params, 'reuse_existing': True})
    dispatch_owner_delegation(authority, replacement, 'revoke', {'room_id': 'room', 'member_id': 'home'})
    renewed = dispatch_owner_delegation(authority, replacement, 'issue', params)
    assert renewed['control_token'] != issued['control_token']
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await read()


@pytest.mark.asyncio
async def test_existing_unbound_token_and_messaging_admin_do_not_become_native_owner(room, monkeypatch):
    authority, actor, service = room
    gateway, epoch = service._owned_authority('room')
    old = controls.issue_home_control_token(authority.db.db_path, room_id='room', member_id='home',
        authority_gateway_id=gateway, authority_epoch=epoch, request_id='legacy-setup',
        expires_at=controls.ROOM_LIFETIME_EXPIRES_AT)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await dispatch_delegated_group_control(authority, room_id='room', member_id='home', token=old.control_token,
            method='groups.state', params={'room_id': 'room'})
    for denied in (replace(actor, subject='messaging-admin'), replace(actor, profile_id='foreign'),
                   replace(actor, capabilities=frozenset({'session:read'}))):
        with pytest.raises(RuntimeStoreError):
            dispatch_owner_delegation(authority, denied, 'issue',
                                     {'room_id': 'room', 'member_id': 'home', 'request_id': 'setup'})
    dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'home'})
    issued = dispatch_owner_delegation(authority, actor, 'issue',
                                      {'room_id': 'room', 'member_id': 'home', 'request_id': 'setup'})
    # Equal room/member/owner/request IDs in another profile still form a
    # different credential realm, even with one installation signing secret.
    other_home = authority.db.db_path.parent / 'profiles' / 'other'
    other_home.mkdir(parents=True)
    with SessionDB(other_home / 'state.db') as other_db:
        other = SimpleNamespace(db=other_db, profile_id=str(other_home),
            epoch=begin_runtime_epoch(other_db, instance_id='other'))
        other_service = CanonicalHostedRoomService(other, None)
        other.hosted_room_service = other_service
        other_service.authorize_room(actor.subject, 'room', create=True)
        hosted_rooms.create_room(other_db.db_path, room_id='room', name='Other',
            authority_gateway_id=gateway, members=[{'member_id': 'home', 'profile': 'other', 'handle': 'home'}])
        other_actor = replace(actor, profile_id=str(other_home))
        other_grant = dispatch_owner_delegation(other, other_actor, 'issue',
            {'room_id': 'room', 'member_id': 'home', 'request_id': 'setup'})
        assert other_grant['control_token'] != issued['control_token']
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await dispatch_delegated_group_control(other, room_id='room', member_id='home',
                token=issued['control_token'], method='groups.state', params={'room_id': 'room'})
    from gateway import session_group_controls
    original = session_group_controls.dispatch_group_control
    async def read_then_revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'home'})
        return result
    monkeypatch.setattr(session_group_controls, 'dispatch_group_control', read_then_revoke)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await dispatch_delegated_group_control(authority, room_id='room', member_id='home',
            token=issued['control_token'], method='groups.state', params={'room_id': 'room'})
