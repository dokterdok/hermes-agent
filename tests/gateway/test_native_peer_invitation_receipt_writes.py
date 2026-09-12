"""Finite local SQLite checks of invitation intent and reservation boundaries."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from gateway import hosted_room_grant_state as grants, hosted_rooms as rooms
from gateway import session_peer_invitation_receipts as receipts
from gateway.hosted_room_peer import gateway_room_grant_secret, issue_room_grant
from gateway.run import _profile_runtime_scope
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_canonical_group_peer_setup import owner  # noqa: F401
from tests.gateway.test_native_peer_invitation_receipts import invite


def retained(connection):
    with connection.authority.db._read_ctx() as conn:
        row = conn.execute('SELECT key,value FROM state_meta WHERE key GLOB ?', (receipts.PREFIX + '*',)).fetchone()
    return row[0], receipts._open(gateway_room_grant_secret(), row[0], row[1])


@pytest.mark.asyncio
async def test_pending_is_durable_before_reservations_and_no_nested_store_writer(owner, monkeypatch):
    connection, _service, _profile = owner
    db = connection.authority.db
    reserve, connect, verify = grants.reserve_grant_state, sqlite3.connect, rooms.room_grant_is_revoked
    opened, writer_checks = [], []
    def observed_connect(*args, **kwargs):
        assert not db._conn.in_transaction, 'A store was opened from the canonical writer'
        opened.append(str(args[0]))
        return connect(*args, **kwargs)
    def observed_verify(*args, **kwargs):
        assert kwargs.get('_conn') is not None, 'Verification reopened a partial snapshot'
        if kwargs['_conn'] is db._conn:
            assert db._conn.in_transaction
            writer_checks.append(True)
        return verify(*args, **kwargs)
    def observed_reserve(paths, **kwargs):
        assert not db._conn.in_transaction
        with closing(connect(str(db.db_path))) as other:
            key, raw = other.execute('SELECT key,value FROM state_meta WHERE key GLOB ?',
                                    (receipts.PREFIX + '*',)).fetchone()
        pending = receipts._open(gateway_room_grant_secret(), key, raw)
        assert pending['state'] == 'pending'
        assert pending['issued_at'] == kwargs['claims']['issued_at']
        return reserve(paths, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', observed_connect)
    monkeypatch.setattr(rooms, 'room_grant_is_revoked', observed_verify)
    monkeypatch.setattr(grants, 'reserve_grant_state', observed_reserve)
    response = await invite(connection)
    assert retained(connection)[1]['state'] == 'committed'
    assert writer_checks and any('?mode=ro' in uri for uri in opened)
    assert await invite(connection) == response


@pytest.mark.asyncio
async def test_pending_with_complete_positive_reservations_recovers_without_new_effects(owner, monkeypatch):
    connection, _service, _profile = owner
    db, write = connection.authority.db, connection.authority.db._execute_write
    calls = 0
    def fail_before_receipt_commit(fn, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('test receipt write refused')
        return write(fn, *args, **kwargs)
    monkeypatch.setattr(db, '_execute_write', fail_before_receipt_commit)
    with pytest.raises(RuntimeError, match='test receipt write refused'):
        await invite(connection)
    _key, value = retained(connection)
    assert value['state'] == 'pending'
    expected = issue_room_grant(gateway_room_grant_secret(), **{**value['intent']['grant'],
        'issued_at': value['issued_at'], 'grant_id': value['grant_id']})
    monkeypatch.setattr(db, '_execute_write', write)
    monkeypatch.setattr(grants, 'reserve_grant_state', lambda *args, **kwargs: pytest.fail('Pending retry re-reserved'))
    response = await invite(connection)
    assert response['grant'] == expected
    assert response['status_expires_at'] == value['issued_at'] + value['intent']['grant']['status_ttl_seconds']
    assert retained(connection)[1]['state'] == 'committed'


@pytest.mark.asyncio
async def test_partial_reservation_uses_source_compensation_and_stays_pending(owner, monkeypatch):
    connection, _service, _profile = owner
    db = connection.authority.db
    # The suite's shared-state fixture and the profile SessionDB are distinct.
    with _profile_runtime_scope(connection.authority.profile_id):
        paths = tuple(Path(p).resolve() for p in grants.grant_state_db_paths(connection.authority.profile_id))
    assert len(paths) == 2 and paths[1] == Path(db.db_path).resolve()
    reserve = rooms.reserve_peer_room
    calls = []
    def refuse_second(path, **kwargs):
        calls.append(Path(path).resolve())
        if len(calls) == 2:
            raise RuntimeError('test second reservation refused')
        return reserve(path, **kwargs)
    monkeypatch.setattr(rooms, 'reserve_peer_room', refuse_second)
    with pytest.raises(RuntimeStoreError, match='room_invitation_pending') as error:
        await invite(connection)
    assert str(error.value.__cause__) == 'test second reservation refused'
    assert calls == list(paths)
    assert retained(connection)[1]['state'] == 'pending'
    for path in paths:
        with closing(sqlite3.connect(str(path))) as conn:
            assert conn.execute('SELECT COUNT(*) FROM hosted_room_peer_reservations WHERE room_id=?',
                                ('retry-room',)).fetchone()[0] == 0
    monkeypatch.setattr(grants, 'reserve_grant_state', lambda *args, **kwargs: pytest.fail('Unproven retry re-reserved'))
    with pytest.raises(RuntimeStoreError, match='room_invitation_pending'):
        await invite(connection)
    assert retained(connection)[1]['state'] == 'pending'


@pytest.mark.asyncio
async def test_single_store_reservation_does_not_nest_the_canonical_writer(owner, monkeypatch):
    connection, _service, _profile = owner
    db = connection.authority.db
    monkeypatch.setattr(rooms, 'default_db_path', lambda: Path(db.db_path))
    reserve = grants.reserve_grant_state
    calls = []
    def observe(paths, **kwargs):
        assert not db._conn.in_transaction
        assert tuple(paths) == (str(Path(db.db_path).resolve()),)
        calls.append(True)
        return reserve(paths, **kwargs)
    monkeypatch.setattr(grants, 'reserve_grant_state', observe)
    first = await invite(connection)
    assert await invite(connection) == first
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['reservation', 'epoch', 'receipt'])
async def test_final_writer_rereads_local_authorization_and_protected_receipt(owner, monkeypatch, change):
    connection, _service, _profile = owner
    db, write = connection.authority.db, connection.authority.db._execute_write
    await invite(connection)
    calls = 0
    def changed_writer(fn, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls != 2:
            return write(fn, *args, **kwargs)
        def inside(conn):
            if change == 'reservation':
                conn.execute('UPDATE hosted_room_peer_reservations SET revoked_at=1 WHERE room_id=?', ('retry-room',))
            elif change == 'epoch':
                conn.execute('UPDATE runtime_epoch SET epoch=epoch+1 WHERE singleton=1')
            else:
                conn.execute('UPDATE state_meta SET value=? WHERE key GLOB ?', ('{}', receipts.PREFIX + '*'))
            return fn(conn)
        return write(inside, *args, **kwargs)
    monkeypatch.setattr(db, '_execute_write', changed_writer)
    reason = 'stale_epoch' if change == 'epoch' else 'room_invitation_invalidated'
    with pytest.raises(RuntimeStoreError, match=reason):
        await invite(connection)


@pytest.mark.asyncio
async def test_commit_is_followed_by_fresh_other_store_checks(owner, monkeypatch):
    connection, _service, _profile = owner
    db, write = connection.authority.db, connection.authority.db._execute_write
    await invite(connection)
    with _profile_runtime_scope(connection.authority.profile_id):
        other = next(Path(p) for p in grants.grant_state_db_paths(connection.authority.profile_id)
                     if Path(p).resolve() != Path(db.db_path).resolve())
    calls = 0
    def revoke_after_commit(fn, *args, **kwargs):
        nonlocal calls
        result = write(fn, *args, **kwargs)
        calls += 1
        if calls == 2:
            assert not db._conn.in_transaction
            with closing(sqlite3.connect(str(other))) as conn:
                conn.execute('UPDATE hosted_room_peer_reservations SET revoked_at=1 WHERE room_id=?', ('retry-room',))
                conn.commit()
        return result
    monkeypatch.setattr(db, '_execute_write', revoke_after_commit)
    with pytest.raises(RuntimeStoreError, match='room_invitation_invalidated'):
        await invite(connection)


@pytest.mark.asyncio
async def test_metadata_edits_and_signing_key_changes_cannot_reconstruct_new_authority(owner, monkeypatch):
    connection, _service, _profile = owner
    await invite(connection)
    key, _value = retained(connection)
    with connection.authority.db._read_ctx() as conn:
        raw = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()[0]
    sealed = json.loads(raw)
    payload = json.loads(sealed['payload'])
    payload['issued_at'] += 100
    sealed['payload'] = json.dumps(payload)
    connection.authority.db._execute_write(lambda conn: conn.execute(
        'UPDATE state_meta SET value=? WHERE key=?', (json.dumps(sealed), key)))
    with pytest.raises(RuntimeStoreError, match='room_invitation_invalidated'):
        await invite(connection)
    connection.authority.db._execute_write(lambda conn: conn.execute(
        'UPDATE state_meta SET value=? WHERE key=?', (raw, key)))
    from gateway import hosted_room_peer
    monkeypatch.setattr(hosted_room_peer, 'gateway_room_grant_secret', lambda: b'new signing secret' * 2)
    with pytest.raises(RuntimeStoreError, match='room_invitation_invalidated'):
        await invite(connection)
