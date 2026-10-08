"""A frozen native invitation can finish publication, never recreate authority."""
import asyncio
import json
import sqlite3
import time

from gateway import hosted_rooms
from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret
from gateway.platforms.api_server_room_grants import _grant_db
from tests.gateway.test_room_invitation_publication import CommitProbe
from tests.gateway.test_session_group_peers import call, gateway as gateway


def parameters(request_id, **changes):
    return dict(room_id='native-publication', home_install_id='original', authority_gateway_id='original',
                authority_epoch=1, member_id='reviewer', request_id=request_id,
                requested_at=time.time(), ttl_seconds=60, status_ttl_seconds=3600) | changes


def receipt(gateway, request_id):
    with hosted_rooms._transaction(_grant_db(gateway.adapter)) as conn:
        return dict(conn.execute('SELECT * FROM hosted_room_setup_invitations WHERE request_id=?',
                                (request_id,)).fetchone())


def authority_snapshot(store):
    return {table: store._conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in (
        'run_room_authorities', 'run_room_origins', 'run_room_authority_aliases', 'run_room_namespaces')}


def test_completed_and_cleanup_only_receipts_never_republish_authority(gateway, monkeypatch):
    monkeypatch.setenv('HERMES_ROOM_LINK_URL', 'http://127.0.0.1:9')
    params = parameters('native-completed-1')
    first = asyncio.run(call(gateway.owner, 'groups.peer.invite', **params))
    assert isinstance(first, dict), first
    store = gateway.adapter._run_idempotency_store
    before = authority_snapshot(store)
    with hosted_rooms._transaction(_grant_db(gateway.adapter)) as conn:
        reservations = [tuple(row) for row in conn.execute('SELECT * FROM hosted_room_peer_reservations')]

    def forbidden(*args, **kwargs):
        raise AssertionError('completed or cleanup-only receipt published authority')

    monkeypatch.setattr(store, 'commit_room_invitation', forbidden)
    assert asyncio.run(call(gateway.owner, 'groups.peer.invite', **params)) == first
    cleanup = asyncio.run(call(gateway.owner, 'groups.peer.invite', **parameters(
        'native-cleanup-only', retirement_only=True)))
    assert isinstance(cleanup, dict), cleanup
    claims = decode_room_grant(gateway_room_grant_secret(), cleanup['grant'], permission='status')
    assert set(claims['permissions']) == {'status', 'retire'}
    assert authority_snapshot(store) == before
    with hosted_rooms._transaction(_grant_db(gateway.adapter)) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM hosted_room_peer_reservations')] == reservations
    assert receipt(gateway, params['request_id'])['publication_pending'] == 0
    assert receipt(gateway, 'native-cleanup-only')['publication_pending'] == 0
    assert 'publication_pending' not in cleanup


def test_revoked_pending_receipt_stays_frozen_and_does_not_publish(gateway, monkeypatch):
    monkeypatch.setenv('HERMES_ROOM_LINK_URL', 'http://127.0.0.1:9')
    store = gateway.adapter._run_idempotency_store
    original = store._conn
    params = parameters('native-pending-revoked')

    def fail():
        raise sqlite3.OperationalError('fixture publication commit failure')

    store._conn = CommitProbe(original, fail)
    try:
        failed = asyncio.run(call(gateway.owner, 'groups.peer.invite', **params))
        assert not isinstance(failed, dict), failed
    finally:
        store._conn = original
    pending = receipt(gateway, params['request_id'])
    assert pending['publication_pending'] == 1
    frozen = json.loads(pending['response_json'])
    assert asyncio.run(call(gateway.owner, 'groups.peer.revoke', grant=frozen['grant'])) == {'revoked': True}
    before = authority_snapshot(store)

    def forbidden(*args, **kwargs):
        raise AssertionError('revoked frozen receipt published authority')

    monkeypatch.setattr(store, 'commit_room_invitation', forbidden)
    replay = asyncio.run(call(gateway.owner, 'groups.peer.invite', **params))
    assert replay['grant'] == frozen['grant'] and replay['catalog'] == frozen['catalog']
    assert receipt(gateway, params['request_id']) == pending
    assert authority_snapshot(store) == before
    claims = decode_room_grant(gateway_room_grant_secret(), frozen['grant'], permission='status')
    assert hosted_rooms.room_grant_is_revoked(_grant_db(gateway.adapter), claims=claims)
