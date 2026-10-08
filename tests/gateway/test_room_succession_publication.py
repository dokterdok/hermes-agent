"""Grant publication rollback preserves the distinction between invitation and verified authority."""
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_rooms as rooms
from gateway import hosted_room_fence as fence
from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_backup as backup
from gateway.hosted_room_peer import decode_room_grant
from gateway.platforms.api_server_room_grants import _grant_db
from gateway.platforms.api_server_run_authority import room_authority, room_namespace
from tests.gateway.test_api_server_room_cancellation import _adapter, _invitation
from tests.gateway.test_room_cancellation_retirement_http import app
from tests.gateway.test_room_succession_authority_lineage import (
    ROOM, claims_for, invitation, net as net, promise, winner_request)


@pytest.mark.asyncio
async def test_ordinary_continuation_consent_failure_rolls_back_reservation_and_floor(tmp_path):
    adapter = _adapter(tmp_path / 'runs.db')
    try:
        async with TestClient(TestServer(app(adapter))) as client:
            token, _ = await _invitation(client)
            old = decode_room_grant(adapter._room_grant_secret(), token, permission='status')
            with rooms._transaction(_grant_db(adapter), immediate=True) as conn:
                conn.execute(f"CREATE TRIGGER refuse_continuation BEFORE INSERT ON {succession.CONSENT} "
                             "BEGIN SELECT RAISE(ABORT,'continuation consent unavailable'); END")
            response = await client.post('/v1/room-members/invitations',
                headers={'Authorization': 'Bearer test-room-key'}, json={
                    **{key: old[key] for key in ('room_id', 'home_install_id', 'authority_gateway_id', 'member_id')},
                    'authority_epoch': 2})
            assert response.status == 400
            assert rooms.peer_room_grant_is_current(_grant_db(adapter), claims=old)
            assert adapter._run_idempotency_store.accepts_room_authority(
                room_authority(old), namespace=room_namespace(old), claims=old)
            with rooms._transaction(_grant_db(adapter)) as conn:
                consent, = succession.consents_locked(conn, old['room_id'])
            assert consent['options']['authority']['authority_epoch'] == 1
    finally:
        adapter._run_idempotency_store.close()


def test_verified_winner_survives_failed_consent_and_can_retry_grant_publication(net):
    gateways, adapter, ctx = net
    with gateways['p'].acting():
        invitation(adapter, gateways['h'].install_id)
    promised = promise(gateways, ctx)
    private, request = winner_request(gateways)
    with gateways['p'].acting():
        old = claims_for(adapter, promised)
        with rooms._transaction(gateways['p'].db, immediate=True) as conn:
            conn.execute(f"CREATE TRIGGER refuse_continuation BEFORE INSERT ON {succession.CONSENT} "
                         "BEGIN SELECT RAISE(ABORT,'continuation consent unavailable'); END")
        with pytest.raises(sqlite3.IntegrityError, match='continuation consent unavailable'):
            backup.answer_learn(ctx, request)
        assert rooms.peer_room_grant_is_current(gateways['p'].db, claims=old)
        store = adapter._run_idempotency_store
        assert not store.accepts_room_authority(room_authority(old))
        assert fence.room_fence_state(store.path, ROOM)['authority'] == {
            'epoch': 2, 'install_id': gateways['b'].install_id}
        with rooms._transaction(gateways['p'].db, immediate=True) as conn:
            conn.execute('DROP TRIGGER refuse_continuation')
        reply = backup.answer_learn(ctx, request)
        current = backup.open_sealed_reply(private, reply, request)['continuation_grants'][0]
        assert rooms.peer_room_grant_is_current(gateways['p'].db, claims=claims_for(adapter, current))
        assert not rooms.peer_room_grant_is_current(gateways['p'].db, claims=old)
        with pytest.raises(fence.RoomAuthorityConflict):
            fence.learn_authority(store.path, room_id=ROOM, epoch=2, install_id=gateways['a'].install_id)
