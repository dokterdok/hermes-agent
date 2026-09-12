"""Real passive stores and signed ingress, with no inference or runtime worker."""

import copy

import pytest

from gateway import hosted_room_peer as peer
from gateway import hosted_room_replicas as replicas
from gateway import hosted_room_replica_ingress as ingress
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair, TARGET, signed_grant  # noqa: F401


@pytest.mark.parametrize("version", [1, 2])
def test_history_then_work_are_idempotent_and_never_authoritative(pair, version):
    if version == 2:
        pair.successor_fixture()
        pair.enroll()
    pair.copy(limit=1)
    with pytest.raises(work.WorkRecordError):
        pair.ingest_work()
    pair.copy()
    record = pair.record()
    ack = pair.ingest_work(record)
    assert ack == work.acknowledgement(record)
    assert pair.ingest_work(record) == ack
    assert "private input" not in work.encode(record)
    assert record["tasks"][0]["phase"] == "queued"
    assert not rooms.list_rooms(pair.target)
    summary = replicas.replica_state(pair.target, room_id="room")["work_records"]
    assert summary["source_loss_safe"] is False
    assert summary["digest"] == record["digest"]
    if version == 2:
        assert summary["task_origins"]["task"]["epoch"] == 1


@pytest.mark.parametrize("change", ["permission", "profile", "installation", "authority", "member", "revoked", "expired-reservation"])
def test_ingress_scope_refusal_does_not_modify_retained_history_or_work(pair, change):
    pair.copy()
    pair.ingest_work()
    page = pair.page()
    members = copy.deepcopy(pair.members)
    token, target, profile = pair.token, TARGET, "default"
    if change == "permission":
        token = signed_grant(pair.secret, permissions=("status",))
    elif change == "profile":
        profile = "other"
    elif change == "installation":
        target = "other"
    elif change == "authority":
        page["authority"]["gateway_id"] = "other"
    elif change == "member":
        members[1]["target"]["installation_id"] = "other"
    elif change == "revoked":
        claims = peer.decode_room_grant(pair.secret, token, permission="replicate")
        rooms.revoke_room_grant_id(pair.target, claims=claims, expires_at=claims["status_expires_at"])
    else:
        with rooms._transaction(pair.target, immediate=True) as conn:
            conn.execute("UPDATE hosted_room_peer_reservations SET expires_at=1")
    before = replicas.replica_state(pair.target, room_id="room")
    with pytest.raises(peer.HostedRoomGrantError):
        ingress.ingest_granted_page(pair.target, token=token, secret=pair.secret,
            target_install_id=target, target_profile=profile, room_id="room", room_name="Workshop",
            members=members, page=page)
    assert replicas.replica_state(pair.target, room_id="room") == before


@pytest.mark.parametrize("damage", ["revision", "digest", "record_json"])
def test_invalid_work_is_classified_only_when_consumed_and_never_replaced(pair, damage):
    pair.copy()
    original = pair.record()
    pair.ingest_work(original)
    with rooms._transaction(pair.target, immediate=True) as conn:
        conn.execute(f"UPDATE {work.TARGET_TABLE} SET {damage}=?", (0 if damage == "revision" else "invalid",))
        before = tuple(conn.execute(f"SELECT revision,digest,record_json FROM {work.TARGET_TABLE}").fetchone())
        work.initialize(conn)
        assert conn.execute(f"SELECT disposition FROM {work.TARGET_TABLE}").fetchone()[0] == "current"
    with pytest.raises(work.InvalidStoredWorkRecord):
        pair.ingest_work(original)
    with rooms._transaction(pair.target) as conn:
        assert tuple(conn.execute(f"SELECT revision,digest,record_json FROM {work.TARGET_TABLE}").fetchone()) == before
        assert conn.execute(f"SELECT disposition FROM {work.TARGET_TABLE}").fetchone()[0] == "invalid"
