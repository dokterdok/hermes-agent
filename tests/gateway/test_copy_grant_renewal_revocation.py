"""An accepted history page cannot renew copy authority that was revoked before renewal."""
import time

import pytest

from gateway import hosted_room_custody as custody
from gateway import hosted_room_peer as peer
from gateway import hosted_room_replicas as replicas
from gateway import hosted_rooms as rooms
from tests.gateway.fixtures.passive_copy import SECRET
from tests.gateway.test_hosted_room_replica_ingress import stores as stores, issued, ingest


@pytest.mark.parametrize('revocation', ['exact', 'scope'])
def test_revocation_after_page_commit_prevents_renewal(stores, monkeypatch, revocation):
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    token, claims = issued(stores[1], member_id=custody.CUSTODY_MEMBER_ID,
        permissions=('replicate', 'status'), issued_at=now - 3000)
    consent = custody.local_consent
    def revoke_before_renewal(db, room_id):
        assert replicas.replica_state(db, room_id=room_id)['last_seq'] == 1
        if revocation == 'exact':
            rooms.revoke_room_grant_token(db, claims=claims, token_sha256=peer.room_grant_token_digest(token),
                                          expires_at=claims['status_expires_at'])
        else:
            rooms.revoke_room_grant_scope(db, claims=claims, expires_at=claims['status_expires_at'])
        return consent(db, room_id)
    monkeypatch.setattr(custody, 'local_consent', revoke_before_renewal)
    with pytest.raises(peer.HostedRoomGrantError):
        ingest(stores, token)
    assert rooms.room_grant_is_revoked(stores[1], claims=claims,
                                      token_sha256=peer.room_grant_token_digest(token))


def test_current_copy_grant_renews_and_replays_the_same_authority(stores, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    token, _ = issued(stores[1], member_id=custody.CUSTODY_MEMBER_ID,
        permissions=('replicate', 'status'), issued_at=now - 3000)
    first = ingest(stores, token)['custody']['renewed_grant']
    assert first != token
    assert ingest(stores, token)['custody']['renewed_grant'] == first
    renewed = peer.decode_room_grant(SECRET, first, permission='replicate')
    assert rooms.peer_room_grant_is_current(stores[1], claims=renewed)
    assert not rooms.room_grant_is_revoked(stores[1], claims=renewed,
                                         token_sha256=peer.room_grant_token_digest(first))


@pytest.mark.parametrize('revocation', ['exact', 'scope'])
def test_revocation_before_reservation_writer_wins_over_earlier_validation(stores, monkeypatch, revocation):
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    token, claims = issued(stores[1], member_id=custody.CUSTODY_MEMBER_ID,
        permissions=('replicate', 'status'), issued_at=now - 3000)
    reserve = rooms.reserve_peer_room
    def revoke_before_writer(db, **kwargs):
        if revocation == 'exact':
            rooms.revoke_room_grant_token(db, claims=claims, token_sha256=peer.room_grant_token_digest(token),
                                          expires_at=claims['status_expires_at'])
        else:
            rooms.revoke_room_grant_scope(db, claims=claims, expires_at=claims['status_expires_at'])
        return reserve(db, **kwargs)
    monkeypatch.setattr(rooms, 'reserve_peer_room', revoke_before_writer)
    with pytest.raises(peer.HostedRoomGrantError):
        ingest(stores, token)
    if revocation == 'scope':
        assert not rooms.peer_room_is_reserved(stores[1], room_id='room', target_profile='default')
