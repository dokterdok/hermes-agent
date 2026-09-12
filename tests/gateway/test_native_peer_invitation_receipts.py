"""Opt-in native invitation recovery with real canonical owners and SQLite."""
from dataclasses import replace
import time
from types import SimpleNamespace

import pytest

from gateway import hosted_room_peer as peer, hosted_rooms
from gateway.session_group_controls import dispatch_group_control
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_canonical_group_peer_setup import owner  # noqa: F401


def request(**changes):
    return dict(room_id='retry-room', home_install_id='home-owner',
        authority_gateway_id='home-owner', authority_epoch=1, member_id='peer',
        ttl_seconds=3600, status_ttl_seconds=7200, request_id='desktop-setup-one', **changes)


async def invite(connection, params=None):
    return await dispatch_group_control(connection, 'groups.peer.invite', request() if params is None else params)


@pytest.mark.asyncio
async def test_reply_recovery_retains_original_bearer_and_horizons(owner, monkeypatch):
    connection, _service, profile = owner
    first = await invite(connection)
    from gateway import session_peer_invitation_receipts as receipts
    monkeypatch.setattr(receipts, 'time', lambda: time.time() + 30)
    def no_reservation(*args, **kwargs):
        pytest.fail('Committed replay must not write reservations')
    from gateway import hosted_room_grant_state
    monkeypatch.setattr(hosted_room_grant_state, 'reserve_grant_state', no_reservation)
    second = await invite(SimpleNamespace(authority=connection.authority,
        actor=replace(connection.actor, transport_id='reconnected-desktop')))
    assert second == first
    claims = peer.decode_room_grant(peer.gateway_room_grant_secret(), second['grant'], permission='status')
    assert claims['target_profile'] == profile
    assert second['expires_at'] - claims['issued_at'] == 3600
    assert second['status_expires_at'] - claims['issued_at'] == 7200
    with connection.authority.db._read_ctx() as conn:
        row = conn.execute('SELECT value FROM state_meta WHERE key LIKE ?', (receipts.PREFIX + '%',)).fetchone()
    assert first['grant'] not in row[0]


@pytest.mark.asyncio
async def test_without_request_id_stable_grant_id_still_means_fresh_issuance(owner, monkeypatch):
    connection, _service, _profile = owner
    now = time.time() - 2
    monkeypatch.setattr(peer, 'clock', lambda supplied=None: now if supplied is None else supplied)
    params = request(grant_id='same-grant-label')
    params.pop('request_id')
    first = await invite(connection, params)
    now += 1
    second = await invite(connection, params)
    assert first['grant'] != second['grant']
    assert second['expires_at'] == first['expires_at'] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'room_id': 'other-room'}, {'member_id': 'other-member'}, {'home_install_id': 'other-home'},
    {'authority_gateway_id': 'other-authority'}, {'authority_epoch': 2}, {'grant_id': 'another-label'},
    {'ttl_seconds': 1800}, {'status_ttl_seconds': 8000}, {'replication': True},
    {'replication': True, 'work_records': True, 'passive_only': True},
])
async def test_request_id_rejects_changed_intent_before_reservation(owner, monkeypatch, change):
    connection, _service, _profile = owner
    await invite(connection)
    from gateway import hosted_room_grant_state
    monkeypatch.setattr(hosted_room_grant_state, 'reserve_grant_state',
        lambda *args, **kwargs: pytest.fail('Changed request must not reserve'))
    with pytest.raises(RuntimeStoreError, match='room_invitation_conflict'):
        await invite(connection, {**request(), **change})


@pytest.mark.asyncio
async def test_owner_profile_and_current_policy_are_not_replay_permissions(owner, monkeypatch):
    connection, _service, _profile = owner
    await invite(connection)
    for actor, reason in ((replace(connection.actor, subject='different-owner'), 'room_invitation_conflict'),
                         (replace(connection.actor, profile_id='/not-the-target'), 'profile_mismatch'),
                         (replace(connection.actor, capabilities=frozenset()), 'permission_denied')):
        with pytest.raises(RuntimeStoreError, match=reason):
            await invite(SimpleNamespace(authority=connection.authority, actor=actor))
    with pytest.raises(RuntimeStoreError, match='profile_mismatch'):
        await invite(connection, {**request(), 'profile': 'foreign'})
    from gateway import run
    original = run._load_gateway_config
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {**original(), 'agent': {'max_turns': 3}})
    with pytest.raises(RuntimeStoreError, match='room_invitation_conflict'):
        await invite(connection)


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidated', ['exact', 'scope', 'expired', 'short_reservation'])
async def test_invalidated_receipt_never_reopens_or_extends_reservation(owner, monkeypatch, invalidated):
    connection, _service, _profile = owner
    first = await invite(connection)
    from gateway import session_peer_invitation_receipts as receipts, hosted_room_grant_state
    if invalidated in {'exact', 'scope'}:
        await dispatch_group_control(connection, 'groups.peer.revoke_exact' if invalidated == 'exact'
                                     else 'groups.peer.revoke', {'grant': first['grant']})
    elif invalidated == 'expired':
        monkeypatch.setattr(receipts, 'time', lambda: first['expires_at'])
    else:
        connection.authority.db._execute_write(lambda conn: conn.execute(
            'UPDATE hosted_room_peer_reservations SET expires_at=? WHERE room_id=?',
            (first['status_expires_at'] - 1, 'retry-room')))
    with connection.authority.db._read_ctx() as conn:
        before = [dict(row) for row in conn.execute('SELECT * FROM hosted_room_peer_reservations')]
    monkeypatch.setattr(hosted_room_grant_state, 'reserve_grant_state',
        lambda *args, **kwargs: pytest.fail('Invalidated replay must not reserve'))
    reason = 'room_invitation_expired' if invalidated == 'expired' else 'room_invitation_invalidated'
    with pytest.raises(RuntimeStoreError, match=reason):
        await invite(connection)
    with connection.authority.db._read_ctx() as conn:
        assert [dict(row) for row in conn.execute('SELECT * FROM hosted_room_peer_reservations')] == before


@pytest.mark.asyncio
async def test_capacity_preserves_replay_and_expired_request_identity(owner, monkeypatch):
    from gateway import session_peer_invitation_receipts as receipts
    connection, _service, _profile = owner
    monkeypatch.setattr(receipts, 'MAX_RECEIPTS', 1)
    first = await invite(connection)
    with pytest.raises(RuntimeStoreError, match='room_invitation_receipt_limit'):
        await invite(connection, {**request(), 'request_id': 'second'})
    assert await invite(connection) == first
    monkeypatch.setattr(receipts, 'time', lambda: first['expires_at'])
    with pytest.raises(RuntimeStoreError, match='room_invitation_expired'):
        await invite(connection)
    with pytest.raises(RuntimeStoreError, match='room_invitation_receipt_limit'):
        await invite(connection, {**request(), 'request_id': 'second'})


@pytest.mark.asyncio
async def test_passive_replay_keeps_its_exact_status_only_permissions(owner, monkeypatch):
    from gateway import session_peer_invitation_receipts as receipts
    connection, _service, _profile = owner
    params = {**request(), 'replication': True, 'work_records': True, 'passive_only': True}
    first = await invite(connection, params)
    monkeypatch.setattr(receipts, 'time', lambda: first['expires_at'] + 1)
    assert await invite(connection, params) == first
    claims = peer.decode_room_grant(peer.gateway_room_grant_secret(), first['grant'], permission='replicate')
    assert set(claims['permissions']) == {'status', 'replicate', 'work_records'}
    monkeypatch.setattr(receipts, 'time', lambda: first['status_expires_at'])
    with pytest.raises(RuntimeStoreError, match='room_invitation_expired'):
        await invite(connection, params)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_id', [None, True, '', ' padded ', 'x' * 257])
async def test_invalid_request_id_never_reserves_or_falls_back_to_fresh_invite(owner, monkeypatch, bad_id):
    connection, _service, _profile = owner
    from gateway import hosted_room_grant_state
    monkeypatch.setattr(hosted_room_grant_state, 'reserve_grant_state',
        lambda *args, **kwargs: pytest.fail('Invalid request ID reached fresh issuance'))
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        await invite(connection, {**request(), 'request_id': bad_id})
