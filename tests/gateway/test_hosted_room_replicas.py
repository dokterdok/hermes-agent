"""Tests for gateway/hosted_room_replicas.py — replica ingest, promotion, and
stale-authority demotion for hosted Group Chat rooms."""

import json
import sqlite3

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


def _safety_reservation(db, room_id):
    """Reservation/quarantine row when Retention safety schema is installed, else None."""
    with sqlite3.connect(db) as conn:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hosted_room_id_reservations'"
        ).fetchone()
        if present is None:
            return None
        owner = conn.execute(
            "SELECT owner_kind FROM hosted_room_id_reservations WHERE room_id=?", (room_id,)
        ).fetchone()
        quarantine = conn.execute(
            "SELECT reason FROM hosted_room_quarantine WHERE room_id=?", (room_id,)
        ).fetchone()
        triggers = {
            name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN (?, ?)",
                (
                    "trg_hosted_rooms_reject_reserved_insert",
                    "trg_hosted_replicas_reject_reserved_insert",
                ),
            )
        }
    return {
        "owner": None if owner is None else owner[0],
        "quarantine": None if quarantine is None else quarantine[0],
        "triggers": triggers,
    }


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


def test_ingest_rejects_epoch_regression(tmp_path):
    page = _seed_room(_authority_db(tmp_path))
    rdb = _replica_db(tmp_path)
    newer = json.loads(json.dumps(page))
    newer["authority"]["epoch"] = 3
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=newer
    )
    stale = json.loads(json.dumps(page))
    stale["authority"]["epoch"] = 2
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
    assert promoted["executable"] is False
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
    assert claim["payload"]["promoted_from_replica"] is True
    assert replay["authority"] == {"gateway_id": AUTH_B, "epoch": 2}

    # The copied epoch is not a fence. New work stays refused.
    with pytest.raises(rooms.RoomQuarantinedError):
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

    safety = _safety_reservation(rdb, "room-1")
    if safety is not None:
        assert safety["owner"] == "authority"
        assert safety["quarantine"] == "unsafe_replica_promotion"
        assert safety["triggers"] == {
            "trg_hosted_rooms_reject_reserved_insert",
            "trg_hosted_replicas_reject_reserved_insert",
        }


def test_unfenced_promotion_does_not_admit_execution(tmp_path, monkeypatch):
    """confirm-equivalent promotion must not execute while the old host is live.

    ``groups.promote`` calls ``promote_replica`` once ``confirm`` is true. That flag,
    the bumped epoch, and a green reservation transfer are not proof the previous
    authority is fenced. Authority A's database is left writable on purpose.
    """
    from gateway.hosted_room_driver import TaskIdentity, acquire_lease, admit_task
    from tui_gateway.hosted_room_service import HostedRoomService

    adb = _authority_db(tmp_path)
    page = _seed_room(adb)
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    monkeypatch.setattr(rooms, "local_authority_gateway_id", lambda: AUTH_B)
    promoted = replicas.promote_replica(rdb, room_id="room-1")

    # The previous host was not demoted and can still commit.
    rooms.append_event(
        adb,
        room_id="room-1",
        event_id="a-still-live",
        kind="message.user",
        actor=USER,
        payload={"text": "old host still writing"},
        authority_gateway_id=AUTH_A,
        authority_epoch=1,
    )

    with pytest.raises(rooms.RoomQuarantinedError):
        rooms.append_event(
            rdb,
            room_id="room-1",
            event_id="b-work",
            kind="message.user",
            actor=USER,
            payload={"text": "should not run"},
            authority_gateway_id=AUTH_B,
            authority_epoch=promoted["authority_epoch"],
        )
    with pytest.raises(rooms.RoomQuarantinedError):
        admit_task(
            rdb,
            TaskIdentity(
                room_id="room-1", task_id="task-1", thread_id="thread-1", turn_id="turn-1"
            ),
            payload={"target_profile": "default", "prompt": "continue", "source_event_seq": 1},
            clock=lambda: 1_700_000_000.0,
        )
    with pytest.raises(rooms.RoomQuarantinedError):
        acquire_lease(
            rdb,
            room_id="room-1",
            gateway_id=AUTH_B,
            authority_epoch=promoted["authority_epoch"],
            process_generation="proc-1",
            ttl_seconds=30,
            clock=lambda: 1_700_000_000.0,
        )

    replay = rooms.read_events(rdb, room_id="room-1", since_seq=0, limit=100)
    claim = replay["events"][-1]
    assert claim["kind"] == "authority.claimed"
    assert claim["payload"]["promoted_from_replica"] is True
    service = HostedRoomService.__new__(HostedRoomService)
    service.db_path = rdb
    assert all(binding.room_id != "room-1" for binding in service.bindings())

    safety = _safety_reservation(rdb, "room-1")
    if safety is not None:
        assert safety["owner"] == "authority"
        assert safety["quarantine"] == "unsafe_replica_promotion"
        with sqlite3.connect(rdb) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    """SELECT name FROM sqlite_master WHERE type='trigger' AND name IN (
                           'trg_hosted_events_quarantine_unsafe_lineage',
                           'trg_hosted_events_reject_quarantined_insert')"""
                )
            }
        assert names == {
            "trg_hosted_events_quarantine_unsafe_lineage",
            "trg_hosted_events_reject_quarantined_insert",
        }


def test_later_claim_cannot_wash_unfenced_promotion(tmp_path, monkeypatch):
    """A higher epoch written after promotion is not fencing proof.

    ``claim_authority`` compare-and-swaps one store and omits
    ``promoted_from_replica``. Replacing the current claim with that payload
    must not open append, driver admission, or the scheduler. An ordinary
    room in the same database stays executable.
    """
    from gateway.hosted_room_driver import TaskIdentity, acquire_lease, admit_task
    from tui_gateway.hosted_room_service import HostedRoomService

    adb = _authority_db(tmp_path)
    page = _seed_room(adb)
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    monkeypatch.setattr(rooms, "local_authority_gateway_id", lambda: AUTH_B)
    promoted = replicas.promote_replica(rdb, room_id="room-1")
    assert promoted["executable"] is False

    rooms.create_room(
        rdb, room_id="room-ok", name="Ordinary", members=MEMBERS, authority_gateway_id=AUTH_B
    )
    rooms.append_event(
        rdb, room_id="room-ok", event_id="ok-1", kind="message.user", actor=USER,
        payload={"text": "ordinary room still runs"},
        authority_gateway_id=AUTH_B, authority_epoch=1,
    )

    with sqlite3.connect(rdb) as conn:
        seq, epoch = conn.execute(
            "SELECT next_seq, authority_epoch FROM hosted_rooms WHERE room_id='room-1'"
        ).fetchone()
        actor = conn.execute(
            """SELECT actor_json FROM hosted_room_events
                WHERE room_id='room-1' AND kind='authority.claimed'"""
        ).fetchone()[0]
    target = int(epoch) + 1
    payload = json.dumps(
        {
            "previous_gateway_id": AUTH_B,
            "authority_gateway_id": AUTH_B,
            "authority_epoch": target,
            "reason": 'note "promoted_from_replica":true is not this claim',
        },
        sort_keys=True, separators=(",", ":"),
    )
    try:
        with sqlite3.connect(rdb) as conn:
            conn.execute(
                """INSERT INTO hosted_room_events
                   (room_id, seq, event_id, kind, actor_json, authority_epoch, payload_json, created_at)
                   VALUES (?, ?, ?, 'authority.claimed', ?, ?, ?, ?)""",
                ("room-1", int(seq), "wash-sql", actor, target, payload, 1.0),
            )
            conn.execute(
                """UPDATE hosted_rooms
                      SET authority_gateway_id=?, authority_epoch=?, next_seq=next_seq+1
                    WHERE room_id='room-1'""",
                (AUTH_B, target),
            )
    except sqlite3.IntegrityError as exc:
        # Retention's reject trigger aborts the wash insert once the claim is quarantined.
        assert "quarantined" in str(exc).lower()
        target = int(epoch)

    with pytest.raises(rooms.RoomQuarantinedError):
        rooms.append_event(
            rdb, room_id="room-1", event_id="after-wash", kind="message.user", actor=USER,
            payload={"text": "washed epoch must not run"},
            authority_gateway_id=AUTH_B, authority_epoch=target,
        )
    with pytest.raises(rooms.RoomQuarantinedError):
        admit_task(
            rdb,
            TaskIdentity(
                room_id="room-1", task_id="task-wash", thread_id="thread-1", turn_id="turn-1"
            ),
            payload={"target_profile": "default", "prompt": "continue", "source_event_seq": 1},
            clock=lambda: 1_700_000_000.0,
        )
    with pytest.raises(rooms.RoomQuarantinedError):
        acquire_lease(
            rdb, room_id="room-1", gateway_id=AUTH_B, authority_epoch=target,
            process_generation="proc-wash", ttl_seconds=30, clock=lambda: 1_700_000_000.0,
        )
    with pytest.raises(rooms.RoomQuarantinedError):
        rooms.claim_authority(
            rdb, room_id="room-1", expected_gateway_id=AUTH_B, expected_epoch=target,
            new_gateway_id=AUTH_B, event_id="wash-api",
        )
    with pytest.raises(rooms.RoomQuarantinedError):
        rooms.rename_room(rdb, room_id="room-1", event_id="rename-wash", name="Stolen")

    service = HostedRoomService.__new__(HostedRoomService)
    service.db_path = rdb
    bound = {binding.room_id for binding in service.bindings()}
    assert "room-1" not in bound
    assert "room-ok" in bound
    claimed = rooms.claim_authority(
        rdb, room_id="room-ok", expected_gateway_id=AUTH_B, expected_epoch=1,
        new_gateway_id=AUTH_B, event_id="ok-claim",
    )
    rooms.append_event(
        rdb, room_id="room-ok", event_id="ok-2", kind="message.user", actor=USER,
        payload={"text": "claim_authority on a normal room still runs"},
        authority_gateway_id=AUTH_B, authority_epoch=claimed["authority_epoch"],
    )


def test_promote_refuses_when_room_exists_locally(tmp_path, monkeypatch):
    db = _authority_db(tmp_path)
    page = _seed_room(db)
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    try:
        # Same DB also holds a replica row for the same id — conflict must win.
        replicas.ingest_page(
            db, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
        )
    except rooms.RoomConflictError as exc:
        # Retention reservation triggers refuse a replica beside a live authority room.
        assert "already reserved" in str(exc)
        safety = _safety_reservation(db, "room-1")
        assert safety is not None and safety["owner"] == "authority"
        assert rooms.room_state(db, room_id="room-1")["authority_gateway_id"] == AUTH_A
        with pytest.raises(replicas.ReplicaError):
            replicas.replica_state(db, room_id="room-1")
        with pytest.raises(replicas.ReplicaError):
            replicas.promote_replica(db, room_id="room-1")
        return
    with pytest.raises(rooms.RoomConflictError):
        replicas.promote_replica(db, room_id="room-1")


def test_promote_moves_events_under_the_shared_budget(tmp_path, monkeypatch):
    """A replica that already fits must still promote once the safety budget trigger is installed.

    Copying events before deleting the replica copy counts the same bytes twice and
    aborts above half the shared budget. Without that trigger the small promote test
    covers the move.
    """
    adb = _authority_db(tmp_path)
    rooms.create_room(
        adb, room_id="probe", name="Field Room", members=MEMBERS, authority_gateway_id=AUTH_A
    )
    with sqlite3.connect(adb) as conn:
        budget_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hosted_room_event_budget'"
        ).fetchone()
    if budget_table is None:
        return
    # Just over half of the 16MiB ordinary budget, so a doubled copy cannot fit.
    payload = {"text": "x" * 200_000}
    rooms.create_room(
        adb, room_id="room-1", name="Field Room", members=MEMBERS, authority_gateway_id=AUTH_A
    )
    for index in range(42):
        rooms.append_event(
            adb, room_id="room-1", event_id=f"e{index}", kind="message.user", actor=USER,
            payload=payload, authority_gateway_id=AUTH_A, authority_epoch=1,
        )
    page = rooms.read_events(adb, room_id="room-1", since_seq=0, limit=100)
    rdb = _replica_db(tmp_path)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    promoted = replicas.promote_replica(rdb, room_id="room-1")
    assert promoted["authority_epoch"] == 2
    with sqlite3.connect(rdb) as conn:
        budget = conn.execute(
            "SELECT event_bytes FROM hosted_room_event_budget WHERE singleton=1"
        ).fetchone()[0]
        hosted = conn.execute(
            """SELECT COALESCE(SUM(
                   LENGTH(CAST(event_id AS BLOB)) + LENGTH(CAST(kind AS BLOB)) +
                   LENGTH(CAST(actor_json AS BLOB)) + LENGTH(CAST(payload_json AS BLOB))
               ), 0) FROM hosted_room_events"""
        ).fetchone()[0]
        remaining = conn.execute(
            "SELECT COUNT(*) FROM hosted_room_replica_events"
        ).fetchone()[0]
    assert remaining == 0
    assert budget == hosted


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
    """Authority A hosts, replica B follows, B copies the log, A is demoted
    only when demote_room is called. B does not execute the copy."""
    adb = _authority_db(tmp_path)
    rdb = _replica_db(tmp_path)
    page = _seed_room(adb, n_events=4)
    replicas.ingest_page(
        rdb, room_id="room-1", room_name="Field Room", members=MEMBERS, page=page
    )

    # A is still live. B's copy is not host-loss recovery and does not execute.
    monkeypatch.setattr(replicas, "local_authority_gateway_id", lambda: AUTH_B)
    promoted = replicas.promote_replica(rdb, room_id="room-1")
    with pytest.raises(rooms.RoomQuarantinedError):
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

    # B's room holds the copied history and the unfenced claim, not new work.
    replay = rooms.read_events(rdb, room_id="room-1", since_seq=0, limit=100)
    kinds = [e["kind"] for e in replay["events"]]
    assert kinds == ["message.user"] * 4 + ["authority.claimed"]
    assert replay["events"][-1]["payload"]["promoted_from_replica"] is True
    assert replay["authority"]["gateway_id"] == AUTH_B
