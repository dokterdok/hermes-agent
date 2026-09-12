"""Native replication methods use the closed canonical registry and owner realm."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.gateway.test_canonical_passive_lifecycle import owners, create_source_room  # noqa: F401


@pytest.mark.asyncio
async def test_native_registry_keeps_source_owner_and_root_receiver_setup(owners):
    from gateway.session_group_controls import dispatch_group_control
    from gateway.session_group_replication import REPLICATION_METHODS
    from gateway.session_group_delegation import dispatch_delegated_group_control
    from hermes_state_runtime import RuntimeStoreError
    source, target, _ = owners
    gateway, target_id = create_source_room(source, target)
    capability = await dispatch_group_control(source, 'groups.capabilities', {})
    assert set(REPLICATION_METHODS) <= set(capability['methods'])
    assert not capability['room_link']['enabled']
    params = dict(room_id='room', target_install_id=target_id, endpoint='http://127.0.0.1:9876',
                  enrollment_id='native-setup', expected_authority={'gateway_id': gateway, 'epoch': 1})
    prepared = await dispatch_group_control(source, 'groups.replication.prepare', params)
    enrolled = await dispatch_group_control(target, 'groups.replication.enroll', prepared)
    assert enrolled['state'] == 'active'
    status = await dispatch_group_control(source, 'groups.replication.status', {'room_id': 'room'})
    assert status['enrollments'][0]['enrollment_id'] == 'native-setup'
    assert status['publisher']['source_loss_safe'] is False
    assert status['retirement_delivery_enabled'] is False
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await dispatch_group_control(SimpleNamespace(authority=source.authority,
            actor=replace(source.actor, subject='another-owner')), 'groups.replication.status', {'room_id': 'room'})
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await dispatch_group_control(SimpleNamespace(authority=target.authority,
            actor=replace(target.actor, capabilities=frozenset({'session:read'}))), 'groups.replication.enroll', prepared)
    with pytest.raises(RuntimeStoreError):
        await dispatch_delegated_group_control(target.authority, room_id='room', member_id='peer',
            token='not-native-owner-authority', method='groups.replication.enroll', params=prepared)
    with pytest.raises(RuntimeStoreError, match='profile_mismatch'):
        await dispatch_group_control(source, 'groups.replication.prepare', {**params, 'profile': 'foreign'})
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        await dispatch_group_control(source, 'groups.replication.prepare', {**params, 'auto_enroll': True})
    revoked = await dispatch_group_control(target, 'groups.replication.revoke',
        {'room_id': 'room', 'enrollment_id': 'native-setup'})
    assert revoked['state'] == 'revoked'
