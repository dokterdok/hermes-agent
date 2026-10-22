"""Tests for gateway/hosted_room_replicas.py — replica ingest, promotion, and
stale-authority demotion for hosted Group Chat rooms."""

import pytest

import gateway.hosted_room_replicas as replicas
import gateway.hosted_rooms as rooms

USER = {"kind": "user", "id": "tek"}
MEMBERS = [{"kind": "bot", "id": "planner"}, {"kind": "bot", "id": "coder"}]

AUTH_A = "install:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AUTH_B = "install:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _authority_db(tmp_path, name="authority.db"):
    return tmp_path / name


def _replica_db(tmp_path, name="replica.db"):
    return tmp_path / name


def _seed_room(db, *, gateway_id=AUTH_A, n_events=3, room_id="room-1"):
    rooms.create_room(
        db,
        room_id=room_id,
        name="Field Room",
        members=MEMBERS,
        authority_gateway_id=gateway_id,
    )
    for index in range(n_events):
        rooms.append_event(
            db,
            room_id=room_id,
            event_id=f"e{index}",
            kind="message.user",
            actor=USER,
            payload={"text": f"msg {index} 😀"},
            authority_gateway_id=gateway_id,
            authority_epoch=1,
        )
    return rooms.read_events(db, room_id=room_id, since_seq=0, limit=100)


def test_ingest_page_persists_events_and_lineage(tmp_path):
    page = _seed_room(_authority_db(tmp_path))
    rdb = _replica_db(tmp_path)
    result = replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    assert result["ingested"] == 3
    assert result["stored_seq"] == 3
    assert result["caught_up"] is True
    state = replicas.replica_state(rdb, room_id="room-1")
    assert state["last_seq"] == 3
    assert state["authority"] == page["authority"]
    assert state["members"] == MEMBERS


def test_ingest_page_is_idempotent(tmp_path):
    page = _seed_room(_authority_db(tmp_path))
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    again = replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    assert again["ingested"] == 0
    assert again["stored_seq"] == 3


@pytest.mark.parametrize("field", ["payload", "actor", "kind", "authority_epoch"])
def test_ingest_refuses_a_replay_that_changes_one_stored_field(tmp_path, field):
    page = _seed_room(_authority_db(tmp_path))
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    changed = {
        "payload": {"text": "rewritten"},
        "actor": {"kind": "user", "id": "someone-else"},
        "kind": "message.member",
        "authority_epoch": 2,
    }[field]
    page["events"][1] = {**page["events"][1], field: changed}
    with pytest.raises(replicas.ReplicaError, match="conflicts with the stored"):
        replicas.ingest_page(
            rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
        )


def test_ingest_rejects_sequence_gap(tmp_path):
    adb = _authority_db(tmp_path)
    _seed_room(adb, n_events=5)
    later = rooms.read_events(adb, room_id="room-1", since_seq=2, limit=100)
    rdb = _replica_db(tmp_path)
    with pytest.raises(replicas.ReplicaGapError):
        replicas.ingest_page(
            rdb,
            room_id="room-1",
            room_name="Field Room",
            members=MEMBERS,
            page=later,
        )


def _successor_after_takeover(tmp_path, monkeypatch):
    """A hosts four messages and C copies all four. B takes over from a copy of
    the first three (its authority.claimed is seq 4) and posts one message."""
    adb, bdb, cdb = (tmp_path / name for name in ("a.db", "b.db", "c.db"))
    page = _seed_room(adb, n_events=4)
    replicas.ingest_page(
        cdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    replicas.ingest_page(
        bdb,
        room_id="room-1",
        room_name="Field Room",
        members=MEMBERS,
        page=rooms.read_events(adb, room_id="room-1", since_seq=0, limit=3),
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    replicas.promote_replica(bdb, room_id="room-1")
    rooms.append_event(
        bdb,
        room_id="room-1",
        event_id="b-work",
        kind="message.user",
        actor=USER,
        payload={"text": "on B"},
        authority_gateway_id=AUTH_B,
        authority_epoch=2,
    )
    return bdb, cdb


def test_ingest_refuses_a_replay_from_a_different_room_history(tmp_path, monkeypatch):
    """B's seq 4 is its authority.claimed where C holds A's message: C keeps A's history."""
    bdb, cdb = _successor_after_takeover(tmp_path, monkeypatch)
    from_b = rooms.read_events(bdb, room_id="room-1", since_seq=0, limit=100)

    with pytest.raises(replicas.ReplicaError, match="conflicts with the stored"):
        replicas.ingest_page(
            cdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=from_b
        )
    state = replicas.replica_state(cdb, room_id="room-1")
    assert (state["last_seq"], state["authority"]) == (
        4,
        {"gateway_id": AUTH_A, "epoch": 1},
    )


def test_ingest_refuses_an_authority_change_without_a_claim(tmp_path, monkeypatch):
    """B's page after C's cursor holds only B's epoch-2 message: taking it would
    put B's work on top of A's seq 4 with no authority.claimed in between."""
    bdb, cdb = _successor_after_takeover(tmp_path, monkeypatch)
    after_c = rooms.read_events(bdb, room_id="room-1", since_seq=4, limit=100)
    assert after_c["authority"] == {"gateway_id": AUTH_B, "epoch": 2}

    with pytest.raises(replicas.ReplicaError, match="without an authority.claimed"):
        replicas.ingest_page(
            cdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=after_c
        )
    state = replicas.replica_state(cdb, room_id="room-1")
    assert (state["last_seq"], state["authority"]["epoch"]) == (4, 1)


def test_fresh_copy_pages_the_successor_through_its_claim(tmp_path, monkeypatch):
    """Short pages stamped with B's authority before its claim arrives stay valid."""
    bdb, _ = _successor_after_takeover(tmp_path, monkeypatch)
    ddb = tmp_path / "d.db"
    for since_seq in (0, 2, 4):
        result = replicas.ingest_page(
            ddb,
            room_id="room-1",
            room_name="Field Room",
            members=MEMBERS,
            page=rooms.read_events(bdb, room_id="room-1", since_seq=since_seq, limit=2),
        )
    assert (result["stored_seq"], result["caught_up"]) == (5, True)
    assert result["authority"] == {"gateway_id": AUTH_B, "epoch": 2}


def test_promote_waits_for_the_claim_the_copy_has_seen(tmp_path, monkeypatch):
    """C read B's log up to, not including, B's claim: promoting it then would mint
    an epoch no copy can follow. Once the claim is stored, C promotes and is copied."""
    bdb, _ = _successor_after_takeover(tmp_path, monkeypatch)
    cdb, ddb = tmp_path / "c2.db", tmp_path / "d.db"
    replicas.ingest_page(
        cdb,
        room_id="room-1",
        room_name="Field Room",
        members=MEMBERS,
        page=rooms.read_events(bdb, room_id="room-1", since_seq=0, limit=3),
    )
    monkeypatch.setattr(
        replicas, "local_authority_gateway_id", lambda: "install:" + "c" * 32
    )

    with pytest.raises(replicas.ReplicaError, match="claimed for epoch 2"):
        replicas.promote_replica(cdb, room_id="room-1")
    assert replicas.replica_state(cdb, room_id="room-1")["last_seq"] == 3
    replicas.ingest_page(
        cdb,
        room_id="room-1",
        room_name="Field Room",
        members=MEMBERS,
        page=rooms.read_events(bdb, room_id="room-1", since_seq=3, limit=100),
    )
    assert replicas.promote_replica(cdb, room_id="room-1")["authority_epoch"] == 3
    result = replicas.ingest_page(
        ddb,
        room_id="room-1",
        room_name="Field Room",
        members=MEMBERS,
        page=rooms.read_events(cdb, room_id="room-1", since_seq=0, limit=100),
    )
    assert (result["stored_seq"], result["caught_up"]) == (6, True)


AUTH_X = "install:" + "x" * 32


def _skip_an_epoch(page):
    claim = page["events"][0]
    claim["authority_epoch"] = claim["payload"]["authority_epoch"] = 3
    page.update(authority={"gateway_id": AUTH_B, "epoch": 3}, latest_seq=5)


def _another_gateway_acts(page):  # still before B's claim, so the page is not caught up
    event = {**page["events"][0], "kind": "room.activity", "authority_epoch": 1, "payload": {}}
    page.update(events=[{**event, "actor": {"kind": "gateway", "id": AUTH_X}}], latest_seq=5)


# One claim-rule violation each, applied to B's page from since_seq=3 (its claim alone).
_CLAIM_RULE_BREAKS = {
    "claim names another predecessor": lambda page: page["events"][0]["payload"].update(
        previous_gateway_id=AUTH_X
    ),
    "claim skips an epoch": _skip_an_epoch,
    "another gateway acts in the span": _another_gateway_acts,
    "caught-up stamp ahead of its events": lambda page: page.update(events=[], latest_seq=3),
}


@pytest.mark.parametrize("short_page_first", [False, True])
@pytest.mark.parametrize("case", list(_CLAIM_RULE_BREAKS))
def test_ingest_holds_the_claim_rule_for_a_copy(tmp_path, monkeypatch, case, short_page_first):
    """C copied A's three events. A short page of B's log (stamped B, epoch 2, adding
    nothing) must not make C forget whose span it holds."""
    adb, bdb, cdb = (tmp_path / name for name in ("a.db", "b.db", "c.db"))
    page = _seed_room(adb, n_events=3)
    for db in (bdb, cdb):
        replicas.ingest_page(
            db, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
        )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    replicas.promote_replica(bdb, room_id="room-1")
    if short_page_first:
        replicas.ingest_page(
            cdb,
            room_id="room-1",
            room_name="Field Room",
            members=MEMBERS,
            page=rooms.read_events(bdb, room_id="room-1", since_seq=0, limit=1),
        )
    broken = rooms.read_events(bdb, room_id="room-1", since_seq=3, limit=100)
    _CLAIM_RULE_BREAKS[case](broken)

    with pytest.raises(replicas.ReplicaError):
        replicas.ingest_page(
            cdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=broken
        )
    assert replicas.replica_state(cdb, room_id="room-1")["last_seq"] == 3


@pytest.mark.parametrize("case", ["gateway actor off the stamp", "epoch 0 event"])
def test_fresh_copy_holds_the_claim_rule(tmp_path, case):
    adb = _authority_db(tmp_path)
    rooms.create_room(
        adb, room_id="room-1", name="Field Room", members=MEMBERS, authority_gateway_id=AUTH_A
    )
    rooms.append_event(
        adb,
        room_id="room-1",
        event_id="turn",
        kind="turn.started",
        actor={"kind": "gateway", "id": AUTH_A},
        payload={},
        authority_gateway_id=AUTH_A,
        authority_epoch=1,
    )
    page = rooms.read_events(adb, room_id="room-1", since_seq=0, limit=100)
    if case == "gateway actor off the stamp":
        page["authority"] = {"gateway_id": AUTH_B, "epoch": 1}
    else:
        page["events"][0]["authority_epoch"] = 0
    rdb = _replica_db(tmp_path)
    with pytest.raises(replicas.ReplicaError, match="without an authority.claimed"):
        replicas.ingest_page(
            rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
        )
    with pytest.raises(replicas.ReplicaError, match="not found"):
        replicas.replica_state(rdb, room_id="room-1")


def test_ingest_rejects_epoch_regression(tmp_path):
    adb = _authority_db(tmp_path)
    stale = _seed_room(adb)
    rooms.claim_authority(
        adb,
        room_id="room-1",
        expected_gateway_id=AUTH_A,
        expected_epoch=1,
        new_gateway_id=AUTH_B,
        event_id="claim-b",
    )
    newer = rooms.read_events(adb, room_id="room-1", since_seq=0, limit=100)
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=newer
    )
    with pytest.raises(replicas.ReplicaEpochRegressionError):
        replicas.ingest_page(
            rdb,
            room_id="room-1",
            room_name="Field Room",
            members=MEMBERS,
            page=stale,
        )


def test_ingest_requires_authority_stamp(tmp_path):
    page = _seed_room(_authority_db(tmp_path))
    page.pop("authority")
    with pytest.raises(replicas.ReplicaError):
        replicas.ingest_page(
            _replica_db(tmp_path),
            room_id="room-1",
            room_name="Field Room",
            members=MEMBERS,
            page=page,
        )


def test_promote_replica_continues_room_at_next_epoch(tmp_path, monkeypatch):
    page = _seed_room(_authority_db(tmp_path))
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)

    promoted = replicas.promote_replica(rdb, room_id="room-1")
    assert promoted["authority_gateway_id"] == AUTH_B
    assert promoted["authority_epoch"] == 2
    assert promoted["previous_gateway_id"] == AUTH_A
    assert promoted["claim_seq"] == 4

    # The room is now locally authoritative with the full history + claim.
    replay = rooms.read_events(rdb, room_id="room-1", since_seq=0, limit=100)
    assert [e["seq"] for e in replay["events"]] == [1, 2, 3, 4]
    claim = replay["events"][-1]
    assert claim["kind"] == "authority.claimed"
    assert claim["payload"]["previous_gateway_id"] == AUTH_A
    assert claim["payload"]["authority_epoch"] == 2
    assert replay["authority"] == {"gateway_id": AUTH_B, "epoch": 2}

    # New work continues under the new epoch.
    rooms.append_event(
        rdb,
        room_id="room-1",
        event_id="post-takeover",
        kind="message.user",
        actor=USER,
        payload={"text": "continuing"},
        authority_gateway_id=AUTH_B,
        authority_epoch=2,
    )

    # The old authority's identity/epoch is fenced out.
    with pytest.raises(rooms.HostedRoomError):
        rooms.append_event(
            rdb,
            room_id="room-1",
            event_id="stale-write",
            kind="message.user",
            actor=USER,
            payload={"text": "stale"},
            authority_gateway_id=AUTH_A,
            authority_epoch=1,
        )

    # Replica bookkeeping is consumed by promotion.
    with pytest.raises(replicas.ReplicaError):
        replicas.replica_state(rdb, room_id="room-1")


def test_promote_refuses_when_room_exists_locally(tmp_path, monkeypatch):
    db = _authority_db(tmp_path)
    page = _seed_room(db)
    # Same DB also holds a replica row for the same id — conflict must win.
    replicas.ingest_page(
        db, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    with pytest.raises(rooms.RoomConflictError):
        replicas.promote_replica(db, room_id="room-1")


def test_promote_refuses_when_already_authority(tmp_path, monkeypatch):
    page = _seed_room(_authority_db(tmp_path))
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_A)
    with pytest.raises(replicas.ReplicaError):
        replicas.promote_replica(rdb, room_id="room-1")


def test_demote_fences_stale_local_authority(tmp_path, monkeypatch):
    adb = _authority_db(tmp_path)
    _seed_room(adb)
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_A)

    result = replicas.demote_room(
        adb, room_id="room-1", observed_gateway_id=AUTH_B, observed_epoch=2
    )
    assert result["idempotent"] is False
    assert result["authority_gateway_id"] == AUTH_B
    assert result["authority_epoch"] == 2

    replay = rooms.read_events(adb, room_id="room-1", since_seq=0, limit=100)
    lost = replay["events"][-1]
    assert lost["kind"] == "authority.lost"
    assert lost["payload"]["authority_gateway_id"] == AUTH_B
    assert replay["authority"] == {"gateway_id": AUTH_B, "epoch": 2}

    # Local sends at the stale identity/epoch are now rejected.
    with pytest.raises(rooms.HostedRoomError):
        rooms.append_event(
            adb,
            room_id="room-1",
            event_id="after-demote",
            kind="message.user",
            actor=USER,
            payload={"text": "stale"},
            authority_gateway_id=AUTH_A,
            authority_epoch=1,
        )

    # Repeating the same observation is idempotent.
    again = replicas.demote_room(
        adb, room_id="room-1", observed_gateway_id=AUTH_B, observed_epoch=2
    )
    assert again["idempotent"] is True


def test_demote_rejects_non_superseding_epoch(tmp_path, monkeypatch):
    adb = _authority_db(tmp_path)
    _seed_room(adb)
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_A)
    with pytest.raises(replicas.ReplicaEpochRegressionError):
        replicas.demote_room(
            adb, room_id="room-1", observed_gateway_id=AUTH_B, observed_epoch=1
        )


def test_full_failover_round_trip(tmp_path, monkeypatch):
    """Authority A hosts, replica B follows, A dies, B promotes, A returns
    and is fenced + demoted; the room's history survives intact throughout."""
    adb = _authority_db(tmp_path)
    rdb = _replica_db(tmp_path)
    page = _seed_room(adb, n_events=4)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )

    # A "dies"; B takes over.
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    promoted = replicas.promote_replica(rdb, room_id="room-1")
    rooms.append_event(
        rdb,
        room_id="room-1",
        event_id="b-work",
        kind="message.user",
        actor=USER,
        payload={"text": "work continues on B"},
        authority_gateway_id=AUTH_B,
        authority_epoch=promoted["authority_epoch"],
    )

    # A comes back, observes B's claim, and fences itself.
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_A)
    replicas.demote_room(
        adb,
        room_id="room-1",
        observed_gateway_id=AUTH_B,
        observed_epoch=promoted["authority_epoch"],
    )
    with pytest.raises(rooms.HostedRoomError):
        rooms.append_event(
            adb,
            room_id="room-1",
            event_id="a-stale",
            kind="message.user",
            actor=USER,
            payload={"text": "split brain attempt"},
            authority_gateway_id=AUTH_A,
            authority_epoch=1,
        )

    # B's room holds the complete history: 4 original + claim + new work.
    replay = rooms.read_events(rdb, room_id="room-1", since_seq=0, limit=100)
    kinds = [e["kind"] for e in replay["events"]]
    assert kinds == ["message.user"] * 4 + ["authority.claimed", "message.user"]
    assert replay["authority"]["gateway_id"] == AUTH_B
