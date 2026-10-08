"""The target's consent writes belong to the same ordinary grant publication."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_rooms
from gateway.hosted_room_custody import CONSENT_TABLE
from gateway.hosted_room_peer import decode_room_grant
from gateway.platforms.api_server_room_grants import _grant_db
from gateway.platforms.api_server_run_authority import room_authority, room_namespace
from tests.gateway.test_api_server_room_cancellation import _adapter, _invitation
from tests.gateway.test_room_cancellation_retirement_http import app


@pytest.mark.asyncio
async def test_failed_consent_keeps_prior_reservation_and_authority(tmp_path):
    adapter = _adapter(tmp_path / 'runs.db')
    try:
        async with TestClient(TestServer(app(adapter))) as client:
            grant, _ = await _invitation(client)
            old = decode_room_grant(adapter._room_grant_secret(), grant, permission='status')
            with hosted_rooms._transaction(_grant_db(adapter), immediate=True) as conn:
                conn.execute(f"CREATE TRIGGER reject_invitation_consent BEFORE INSERT ON {CONSENT_TABLE} "
                             "BEGIN SELECT RAISE(ABORT,'consent unavailable'); END")
            response = await client.post('/v1/room-members/invitations',
                headers={'Authorization': 'Bearer test-room-key'}, json={
                    **{key: old[key] for key in ('room_id', 'home_install_id', 'authority_gateway_id', 'member_id')},
                    'authority_epoch': 2})
            assert response.status == 400
            assert hosted_rooms.peer_room_grant_is_current(_grant_db(adapter), claims=old)
            assert adapter._run_idempotency_store.accepts_room_authority(
                room_authority(old), namespace=room_namespace(old), claims=old)
    finally:
        adapter._run_idempotency_store.close()
