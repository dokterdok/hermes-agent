"""Exact-grant retirement cannot revoke another worker's accepted replacement."""

import contextvars
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway import hosted_room_grant_state as state
from gateway import hosted_rooms
from gateway.hosted_room_peer import decode_room_grant, issue_room_grant
from gateway.platforms import api_server
from gateway.platforms import api_server_room_grants as grants


@pytest.fixture
def exact_revoke(tmp_path, monkeypatch):
    secret = b"test-exact-grant-secret-32-bytes!!"
    stores = (tmp_path / "state.db", tmp_path / "profiles" / "reviewer" / "state.db")
    monkeypatch.setattr(state, "grant_state_db_paths", lambda: stores)
    monkeypatch.setattr(
        hosted_rooms, "local_authority_gateway_id", lambda: "install:peer"
    )
    control_cleanup = Mock()
    monkeypatch.setattr(
        "gateway.hosted_room_control_client.revoke_stored_peer_control", control_cleanup
    )
    adapter = api_server.APIServerAdapter.__new__(api_server.APIServerAdapter)
    adapter._room_grant_secret = lambda: secret
    adapter._read_json_body = AsyncMock(return_value=({}, None))
    profile = contextvars.ContextVar("exact-profile", default="reviewer")

    def token(grant_id):
        return issue_room_grant(
            secret,
            grant_id=grant_id,
            room_id="room-1",
            home_install_id="install:home",
            authority_gateway_id="install:home",
            authority_epoch=1,
            member_id="member-peer",
            target_install_id="install:peer",
            target_profile="reviewer",
            execution_policy_digest="a" * 64,
            issued_at=time.time() - 10,
            ttl_seconds=3600,
        )

    async def revoke(value, scheme="HermesRoom"):
        request = SimpleNamespace(headers={"Authorization": f"{scheme} {value}"})
        return await grants._handle_room_member_grant_revoke_exact(
            adapter,
            request,
            _openai_error=api_server._openai_error,
            _api_request_profile=profile,
        )

    return secret, stores, token, revoke, profile, control_cleanup


@pytest.mark.asyncio
async def test_exact_http_revoke_is_idempotent_and_preserves_replacement(exact_revoke):
    secret, stores, token, revoke, _profile, cleanup = exact_revoke
    losing, winning = token("losing-grant"), token("winning-grant")
    for _ in range(2):
        assert (await revoke(losing)).status == 200
    losing_claims = decode_room_grant(secret, losing, permission="status")
    winning_claims = decode_room_grant(secret, winning, permission="status")
    for db in stores:
        assert hosted_rooms.room_grant_is_revoked(db, claims=losing_claims)
        assert not hosted_rooms.room_grant_is_revoked(db, claims=winning_claims)
    cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_exact_revoke_rejects_wrong_profile_or_bearer(exact_revoke):
    secret, stores, token, revoke, profile, cleanup = exact_revoke
    value = token("scope-test")
    assert (await revoke(value, "Bearer")).status == 401
    reset = profile.set("other")
    try:
        assert (await revoke(value)).status == 401
    finally:
        profile.reset(reset)
    claims = decode_room_grant(secret, value, permission="status")
    for db in stores:
        assert not hosted_rooms.room_grant_is_revoked(db, claims=claims)
    cleanup.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("grant_id", ["grant/user", "grant@home", "g" * 256])
async def test_exact_revoke_accepts_the_signed_grant_identifier(exact_revoke, grant_id):
    secret, stores, token, revoke, _profile, cleanup = exact_revoke
    value = token(grant_id)
    claims = decode_room_grant(secret, value, permission="status")
    sibling = decode_room_grant(secret, token("other@grant"), permission="status")
    for _ in range(2):
        assert (await revoke(value)).status == 200
    for db in stores:
        assert hosted_rooms.room_grant_is_revoked(db, claims=claims)
        assert not hosted_rooms.room_grant_is_revoked(db, claims=sibling)
    cleanup.assert_not_called()


@pytest.mark.parametrize(
    "invalid", [None, True, "", "   ", "bad\nidentifier", "g" * 257]
)
def test_exact_storage_keeps_rejecting_invalid_grant_identifiers(exact_revoke, invalid):
    secret, stores, token, _revoke, _profile, _cleanup = exact_revoke
    claims = decode_room_grant(secret, token("valid-grant"), permission="status")
    claims["grant_id"] = invalid
    for db in stores:
        with pytest.raises(hosted_rooms.HostedRoomError):
            hosted_rooms.revoke_room_grant_id(
                db, claims=claims, expires_at=claims["expires_at"]
            )
        with pytest.raises(hosted_rooms.HostedRoomError):
            hosted_rooms.room_grant_is_revoked(db, claims=claims)
