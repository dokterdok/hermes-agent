"""Issuance rotations preserve active recovery without recycling old bearers."""

import hashlib

import pytest

from gateway import hosted_room_controls as controls
from gateway.session_group_delegation import dispatch_delegated_group_control, dispatch_owner_delegation
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch
from tests.gateway.test_canonical_group_delegation import room  # noqa: F401


async def read(authority, token):
    return await dispatch_delegated_group_control(authority, room_id='room', member_id='home', token=token,
        method='groups.state', params={'room_id': 'room'})


@pytest.mark.asyncio
async def test_rotations_never_recycle_bearers_for_current_or_older_request_ids(room):
    authority, actor, _ = room
    previous = []
    for index, request_id in enumerate(('A', 'A', 'B', 'A', 'C', 'B', 'A')):
        params = dict(room_id='room', member_id='home', request_id=request_id)
        issued = dispatch_owner_delegation(authority, actor, 'issue', params)
        token = issued['control_token']
        assert hashlib.sha256(token.encode()).digest() not in [hashlib.sha256(old.encode()).digest() for old in previous]
        assert (await read(authority, token))['room']['room_id'] == 'room'
        authority.epoch = begin_runtime_epoch(authority.db, instance_id=f'ordinary-restart-{index}')
        recovered = dispatch_owner_delegation(authority, actor, 'issue', params)
        assert recovered['control_token'] == token
        assert dispatch_owner_delegation(authority, actor, 'issue',
            {**params, 'request_id': 'recover-active', 'reuse_existing': True})['control_token'] == token
        for old in previous:
            with pytest.raises(RuntimeStoreError, match='permission_denied'):
                await read(authority, old)
        dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'home'})
        with pytest.raises(controls.HostedRoomControlConflictError):
            dispatch_owner_delegation(authority, actor, 'issue', {**params, 'reuse_existing': True})
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await read(authority, token)
        previous.append(token)
    with authority.db._read_ctx() as conn:
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_control_tokens').fetchone()[0] == 1
    stored = authority.db.db_path.read_bytes()
    assert all(token.encode() not in stored for token in previous)


@pytest.mark.asyncio
async def test_legacy_active_token_reads_before_migration_and_recovers_unchanged(room):
    authority, actor, service = room
    params = dict(room_id='room', member_id='home', request_id='legacy-active')
    dispatch_owner_delegation(authority, actor, 'issue', params)
    gateway, epoch = service._owned_authority('room')
    with authority.db._read_ctx() as conn:
        request_id = conn.execute('SELECT request_id FROM hosted_room_control_tokens').fetchone()[0]
    legacy = controls._derived_control_token(room_id='room', member_id='home',
        authority_gateway_id=gateway, authority_epoch=epoch, request_id=request_id)
    # Model the actual previous schema/derivation, not a new-schema token with
    # a different opaque value. No production database is involved.
    def old_schema(conn):
        conn.execute('UPDATE hosted_room_control_tokens SET token_hash=?',
                     (hashlib.sha256(legacy.encode('ascii')).digest(),))
        if 'issuance_nonce' in {row[1] for row in conn.execute('PRAGMA table_info(hosted_room_control_tokens)')}:
            conn.execute('ALTER TABLE hosted_room_control_tokens DROP COLUMN issuance_nonce')
    authority.db._execute_write(old_schema)
    assert (await read(authority, legacy))['room']['name'] == 'Shared'
    assert dispatch_owner_delegation(authority, actor, 'issue', params)['control_token'] == legacy
    assert dispatch_owner_delegation(authority, actor, 'issue',
        {**params, 'request_id': 'recover', 'reuse_existing': True})['control_token'] == legacy
    dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'home'})
    for request_id in ('new', 'legacy-active', 'new'):
        issued = dispatch_owner_delegation(authority, actor, 'issue', {**params, 'request_id': request_id})
        assert hashlib.sha256(issued['control_token'].encode()).digest() != hashlib.sha256(legacy.encode()).digest()
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await read(authority, legacy)
        dispatch_owner_delegation(authority, actor, 'revoke', {'room_id': 'room', 'member_id': 'home'})
