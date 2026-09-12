"""Canonical grant facts rechecked inside a passive store transaction."""

from types import SimpleNamespace

import pytest

from gateway import hosted_room_peer as peer
from gateway import hosted_room_replica_ingress as ingress
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair, TARGET, signed_grant  # noqa: F401


def authorized(pair, token):
    return ingress.authorize_granted_room(token=token, secret=pair.secret,
        target_install_id=TARGET, target_profile="default", room_id="room", members=pair.members,
        authority={"gateway_id": pair.gateway, "epoch": pair.epoch}, permission="replicate")


@pytest.mark.parametrize("change", ["reservation", "expiry", "scope", "token"])
def test_authorization_rechecks_current_facts_after_prevalidation(pair, monkeypatch, change):
    callback = authorized(pair, pair.token)
    claims = peer.decode_room_grant(pair.secret, pair.token, permission="replicate")
    if change == "token":
        rooms.revoke_room_grant_id(pair.target, claims=claims, expires_at=claims["status_expires_at"])
    with rooms._transaction(pair.target, immediate=True) as conn:
        if change == "reservation":
            conn.execute("UPDATE hosted_room_peer_reservations SET expires_at=1")
        elif change == "expiry":
            monkeypatch.setattr(ingress, "time", SimpleNamespace(time=lambda: claims["status_expires_at"] + 1))
        elif change == "scope":
            conn.execute("INSERT INTO hosted_room_revoked_grants(scope_key,expires_at,revoked_before) VALUES (?,?,?)",
                (rooms._room_grant_scope_key(claims), claims["status_expires_at"], claims["issued_at"]))
        with pytest.raises(peer.HostedRoomGrantError):
            callback(conn)


def test_exact_token_revocation_does_not_revoke_replacement_with_same_grant_id(pair):
    original = peer.decode_room_grant(pair.secret, pair.token, permission="replicate")
    replacement = signed_grant(pair.secret, permissions=("replicate", "work_records", "status"))
    current = peer.decode_room_grant(pair.secret, replacement, permission="replicate")
    assert current["grant_id"] == original["grant_id"]
    assert current["_token_sha256"] != original["_token_sha256"]
    old_callback, current_callback = authorized(pair, pair.token), authorized(pair, replacement)
    rooms.revoke_room_grant_id(pair.target, claims=original, expires_at=original["status_expires_at"])
    with rooms._transaction(pair.target, immediate=True) as conn:
        with pytest.raises(peer.HostedRoomGrantError):
            old_callback(conn)
        current_callback(conn)
