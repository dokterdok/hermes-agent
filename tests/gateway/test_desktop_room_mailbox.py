from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from gateway import desktop_room_mailbox as mailbox
from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactScope


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def authorities(*room_ids: str, token: str = "authority:one") -> list[dict]:
    return [
        {"room_id": room_id, "authority_token": token}
        for room_id in room_ids
    ]


def authority_commitment(token: str = "authority:one") -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def own_room(db: Path, clock: Clock, room_id: str = "room-1") -> None:
    mailbox.register_projected_authorities(
        db,
        [{"room_id": room_id, "authority_hash": authority_commitment()}],
        clock=clock,
    )
    mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities(room_id),
        clock=clock,
    )


def begin_room_turn(db: Path, clock: Clock, **overrides):
    params = {
        "room_id": "room-1",
        "consumer_id": "desktop:owner",
        "authority_token": "authority:one",
        "session_key": "session-1",
        "profile": "reviewer",
        "member_id": "gateway-a::reviewer",
        "turn_id": "turn-1",
        "execution_generation": 1,
        "recipient_member_ids": ["gateway-a::reviewer", "gateway-b::builder"],
        "clock": clock,
    }
    params.update(overrides)
    return mailbox.begin_turn(db, **params)


def test_enqueue_is_idempotent_and_rejects_key_reuse(tmp_path):
    db = tmp_path / "state.db"
    first = mailbox.enqueue_command(
        db,
        command_id="messaging:abc",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
    )
    replay = mailbox.enqueue_command(
        db,
        command_id="messaging:abc",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
    )

    assert first["state"] == "pending"
    assert replay["idempotent"] is True
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="different room work"):
        mailbox.enqueue_command(
            db,
            command_id="messaging:abc",
            room_id="room-1",
            authority_hash=authority_commitment(),
            action="send",
            payload={"message": "changed"},
        )


def test_enqueue_moves_the_cross_process_pending_signal(tmp_path):
    db = tmp_path / "desktop_room_mailbox.db"
    signal = mailbox.pending_signal_path(db)

    mailbox.enqueue_command(
        db,
        command_id="messaging:first",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
    )
    first = signal.read_text(encoding="ascii")
    mailbox.enqueue_command(
        db,
        command_id="messaging:second",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "again"},
    )

    assert signal.read_text(encoding="ascii") != first
    assert signal.stat().st_mode & 0o777 == 0o600


def test_signal_failure_does_not_change_a_durable_enqueue(tmp_path, monkeypatch):
    db = tmp_path / "desktop_room_mailbox.db"
    mailbox.enqueue_command(
        db,
        command_id="messaging:seed",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "seed"},
    )
    monkeypatch.setattr(
        type(mailbox.pending_signal_path(db)),
        "write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read only")),
    )

    queued = mailbox.enqueue_command(
        db,
        command_id="messaging:still-durable",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
    )

    assert queued["state"] == "pending"


def test_one_desktop_claims_and_presence_is_room_scoped(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:one",
        room_id="name:Classic room",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )

    claimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("name:Classic room"),
        clock=clock,
    )
    duplicate = mailbox.claim_commands(
        db,
        consumer_id="desktop:second",
        room_authorities=authorities("name:Classic room", token="authority:two"),
        clock=clock,
    )

    assert [item["command_id"] for item in claimed] == ["messaging:one"]
    assert duplicate == []
    assert mailbox.room_available(db, "name:Classic room", clock=clock) is True
    assert mailbox.room_available(db, "another-room", clock=clock) is False


def test_presence_refresh_does_not_claim_pending_commands(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:pending",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )

    owned = mailbox.refresh_presence(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        clock=clock,
    )
    claimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        clock=clock,
    )

    assert owned == ["room-1"]
    assert mailbox.room_available(db, "room-1", clock=clock) is True
    assert [item["command_id"] for item in claimed] == ["messaging:pending"]


def test_expired_claim_is_recovered_by_the_registered_desktop(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:retry",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )
    first = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        claim_ttl=10,
        presence_ttl=10,
        clock=clock,
    )
    clock.value += 11
    second = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        clock=clock,
    )

    assert first[0]["attempts"] == 1
    assert second[0]["attempts"] == 2
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="no longer owned"):
        mailbox.complete_command(
            db,
            consumer_id="desktop:first",
            command_id="messaging:retry",
            lease_token=first[0]["lease_token"],
            success=True,
            result={"thread_id": "old"},
            clock=clock,
        )


def test_completion_ack_retry_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    mailbox.enqueue_command(
        db,
        command_id="messaging:complete",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="stop",
        payload={},
    )
    claimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
    )
    first = mailbox.complete_command(
        db,
        consumer_id="desktop:first",
        command_id="messaging:complete",
        lease_token=claimed[0]["lease_token"],
        success=True,
        result={"stopped": True},
    )
    replay = mailbox.complete_command(
        db,
        consumer_id="desktop:first",
        command_id="messaging:complete",
        lease_token=claimed[0]["lease_token"],
        success=True,
        result={"stopped": True},
    )

    assert first["state"] == "completed"
    assert replay["idempotent"] is True


def test_reclaim_rotates_token_and_fences_a_stale_attempt(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:fenced",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )
    first = mailbox.claim_commands(
        db,
        consumer_id="desktop:same-install",
        room_authorities=authorities("room-1"),
        claim_ttl=5,
        clock=clock,
    )[0]
    clock.value += 6
    second = mailbox.claim_commands(
        db,
        consumer_id="desktop:same-install",
        room_authorities=authorities("room-1"),
        claim_ttl=5,
        clock=clock,
    )[0]

    assert first["lease_token"] != second["lease_token"]
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="no longer owned"):
        mailbox.complete_command(
            db,
            consumer_id="desktop:same-install",
            command_id="messaging:fenced",
            lease_token=first["lease_token"],
            success=True,
            result={"thread_id": "old"},
            clock=clock,
        )


def test_live_claim_can_be_renewed(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:renew",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )
    claimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        claim_ttl=10,
        clock=clock,
    )[0]
    clock.value += 8
    renewed = mailbox.renew_command(
        db,
        consumer_id="desktop:first",
        command_id="messaging:renew",
        lease_token=claimed["lease_token"],
        claim_ttl=10,
        clock=clock,
    )
    clock.value += 5

    assert renewed["lease_token"] == claimed["lease_token"]
    assert mailbox.complete_command(
        db,
        consumer_id="desktop:first",
        command_id="messaging:renew",
        lease_token=claimed["lease_token"],
        success=True,
        result={"thread_id": "thread-1"},
        clock=clock,
    )["state"] == "completed"


def test_presence_expires_without_deleting_pending_work(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:later",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "later"},
        clock=clock,
    )
    mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        presence_ttl=5,
        claim_ttl=5,
        clock=clock,
    )
    clock.value += 6

    assert mailbox.room_available(db, "room-1", clock=clock) is False
    reclaimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        clock=clock,
    )
    assert reclaimed[0]["command_id"] == "messaging:later"


def test_authority_token_fences_a_cold_desktop_after_owner_expiry(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:fenced-room",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )
    mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
        claim_ttl=5,
        presence_ttl=5,
        clock=clock,
    )
    clock.value += 6

    assert mailbox.claim_commands(
        db,
        consumer_id="desktop:cold",
        room_authorities=authorities("room-1", token="authority:wrong"),
        clock=clock,
    ) == []
    reclaimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
        clock=clock,
    )
    assert reclaimed[0]["command_id"] == "messaging:fenced-room"


def test_claim_cannot_establish_room_authority(tmp_path):
    db = tmp_path / "state.db"
    mailbox.enqueue_command(
        db,
        command_id="messaging:unregistered",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
    )

    assert mailbox.claim_commands(
        db,
        consumer_id="desktop:untrusted",
        room_authorities=authorities("room-1", token="authority:guessed"),
    ) == []

    claimed = mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
    )
    assert [item["command_id"] for item in claimed] == ["messaging:unregistered"]


def test_projected_authority_commitment_is_idempotent_and_fenced(tmp_path):
    db = tmp_path / "state.db"
    assert mailbox.register_projected_authorities(
        db,
        [{"room_id": "room-1", "authority_hash": authority_commitment()}],
    ) == ["room-1"]
    assert mailbox.register_projected_authorities(
        db,
        [{"room_id": "room-1", "authority_hash": authority_commitment()}],
    ) == ["room-1"]

    assert mailbox.register_projected_authorities(
        db,
        [{
            "room_id": "room-1",
            "authority_hash": authority_commitment("authority:other"),
        }],
    ) == []

    assert mailbox.register_projected_authorities(
        db,
        [
            {
                "room_id": "room-1",
                "authority_hash": authority_commitment("authority:other"),
            },
            {"room_id": "room-2", "authority_hash": authority_commitment()},
        ],
    ) == ["room-2"]

    with pytest.raises(mailbox.DesktopRoomMailboxError, match="commitment"):
        mailbox.enqueue_command(
            db,
            command_id="messaging:conflict",
            room_id="room-1",
            authority_hash=authority_commitment("authority:other"),
            action="send",
            payload={"message": "must not replace owner"},
        )


def test_action_filter_allows_stop_to_bypass_pending_send(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    for action in ("send", "stop"):
        mailbox.enqueue_command(
            db,
            command_id=f"messaging:{action}",
            room_id="room-1",
            authority_hash=authority_commitment(),
            action=action,
            payload={"message": "hello"} if action == "send" else {},
            clock=clock,
        )

    stopped = mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
        actions=["stop"],
        clock=clock,
    )
    sends = mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
        actions=["send"],
        clock=clock,
    )

    assert [item["action"] for item in stopped] == ["stop"]
    assert [item["action"] for item in sends] == ["send"]


def test_renew_keeps_room_ownership_alive_for_long_turn(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:long",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "hello"},
        clock=clock,
    )
    command = mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
        claim_ttl=45,
        presence_ttl=90,
        clock=clock,
    )[0]

    for _ in range(8):
        clock.value += 30
        mailbox.renew_command(
            db,
            consumer_id="desktop:owner",
            command_id=command["command_id"],
            lease_token=command["lease_token"],
            claim_ttl=45,
            presence_ttl=90,
            clock=clock,
        )

    assert mailbox.room_available(db, "room-1", clock=clock) is True
    assert mailbox.claim_commands(
        db,
        consumer_id="desktop:other",
        room_authorities=authorities("room-1"),
        actions=["stop"],
        clock=clock,
    ) == []


def test_default_presence_overlaps_the_minute_desktop_backstop(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.register_projected_authorities(
        db,
        [{"room_id": "room-1", "authority_hash": authority_commitment()}],
        clock=clock,
    )
    mailbox.claim_commands(
        db,
        consumer_id="desktop:first",
        room_authorities=authorities("room-1"),
        clock=clock,
    )

    clock.value += 61
    assert mailbox.room_available(db, "room-1", clock=clock) is True
    clock.value += 30
    assert mailbox.room_available(db, "room-1", clock=clock) is False


def test_latest_command_state_is_scoped_per_room(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    mailbox.enqueue_command(
        db,
        command_id="messaging:first",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "first"},
        clock=clock,
    )
    clock.value += 1
    mailbox.enqueue_command(
        db,
        command_id="messaging:second",
        room_id="room-1",
        authority_hash=authority_commitment(),
        action="send",
        payload={"message": "second"},
        clock=clock,
    )
    mailbox.enqueue_command(
        db,
        command_id="messaging:other",
        room_id="room-2",
        authority_hash=authority_commitment(),
        action="stop",
        payload={},
        clock=clock,
    )

    states = mailbox.latest_command_states(db, ["room-1", "room-2"])

    assert states["room-1"]["command_id"] == "messaging:second"
    assert states["room-2"]["command_id"] == "messaging:other"


def test_paged_claims_preserve_presence_for_every_owned_room(tmp_path):
    db = tmp_path / "state.db"
    clock = Clock()
    rooms = [f"room-{index}" for index in range(260)]

    for index in range(0, len(rooms), mailbox.MAX_ROOM_IDS):
        batch = rooms[index : index + mailbox.MAX_ROOM_IDS]
        mailbox.register_projected_authorities(
            db,
            [
                {"room_id": room_id, "authority_hash": authority_commitment()}
                for room_id in batch
            ],
            clock=clock,
        )
        mailbox.claim_commands(
            db,
            consumer_id="desktop:first",
            room_authorities=authorities(*batch),
            clock=clock,
        )

    assert mailbox.available_room_ids(db, rooms, clock=clock) == set(rooms)


def test_turn_grant_binds_authority_session_profile_and_frozen_recipients(tmp_path):
    db = tmp_path / "desktop_room_mailbox.db"
    clock = Clock()
    own_room(db, clock)

    first = begin_room_turn(db, clock)
    replay = begin_room_turn(db, clock)

    assert replay["token"] == first["token"]
    assert replay["recipient_member_ids"] == [
        "gateway-a::reviewer",
        "gateway-b::builder",
    ]
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="authority"):
        begin_room_turn(db, clock, authority_token="authority:wrong")
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="different authority"):
        begin_room_turn(
            db,
            clock,
            recipient_member_ids=[
                "gateway-a::reviewer",
                "gateway-b::builder",
                "gateway-c::late",
            ],
        )
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="session and profile"):
        mailbox.verify_turn_submission(
            db,
            token=first["token"],
            session_key="session-other",
            profile="reviewer",
            clock=clock,
        )
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="session and profile"):
        mailbox.verify_turn_submission(
            db,
            token=first["token"],
            session_key="session-1",
            profile="other",
            clock=clock,
        )

    verified = mailbox.verify_turn_submission(
        db,
        token=first["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    assert verified["state"] == "submitted"
    assert verified["scope"]["member_id"] == "gateway-a::reviewer"
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="profile changed"):
        mailbox.turn_artifacts(
            db,
            token=first["token"],
            expected_profile="other",
            clock=clock,
        )


def test_turn_begin_binds_first_owner_only_after_trusted_projection(tmp_path):
    db = tmp_path / "desktop_room_mailbox.db"
    clock = Clock()
    mailbox.register_projected_authorities(
        db,
        [{"room_id": "room-1", "authority_hash": authority_commitment()}],
        clock=clock,
    )

    turn = begin_room_turn(db, clock)

    assert turn["state"] == "issued"
    assert mailbox.room_available(db, "room-1", clock=clock) is True


def test_turn_output_is_read_from_exact_scope_then_acked_idempotently(
    tmp_path, monkeypatch
):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    manifest = outbox.put_path(scope=scope, path=shared)

    result = mailbox.turn_artifacts(db, token=turn["token"], clock=clock)
    assert result["artifacts"] == [manifest]
    read_manifest, data = mailbox.read_turn_artifact(
        db,
        token=turn["token"],
        artifact_id=manifest["artifact_id"],
        clock=clock,
    )
    assert read_manifest == manifest
    assert data == b"ROOM_HANDOFF"

    completed = mailbox.complete_turn(
        db,
        token=turn["token"],
        artifact_ids=[manifest["artifact_id"]],
        clock=clock,
    )
    replay = mailbox.complete_turn(
        db,
        token=turn["token"],
        artifact_ids=[manifest["artifact_id"]],
        clock=clock,
    )
    assert completed["state"] == "completed"
    assert replay["idempotent"] is True
    assert outbox.list(scope) == [manifest]
    read_manifest, data = mailbox.read_turn_artifact(
        db,
        token=turn["token"],
        artifact_id=manifest["artifact_id"],
        clock=clock,
    )
    assert read_manifest == manifest
    assert data == b"ROOM_HANDOFF"

    clock.value += mailbox.TERMINAL_RETENTION_SECONDS + 1
    mailbox.reap_stale_turns(
        db,
        profile="reviewer",
        artifact_db_path=tmp_path / "reviewer-state.db",
        clock=clock,
    )
    assert outbox.list(scope) == []


def test_turn_completion_recovers_after_lost_response(tmp_path, monkeypatch):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    manifest = outbox.put_path(scope=scope, path=shared)
    first = mailbox.complete_turn(
        db,
        token=turn["token"],
        artifact_ids=[manifest["artifact_id"]],
        clock=clock,
    )
    assert first["state"] == "completed"
    recovered_manifest = mailbox.turn_artifacts(
        db,
        token=turn["token"],
        clock=clock,
    )
    assert recovered_manifest["artifact_ids"] == [manifest["artifact_id"]]
    assert recovered_manifest["artifacts"] == [manifest]
    recovered = mailbox.complete_turn(
        db,
        token=turn["token"],
        artifact_ids=[manifest["artifact_id"]],
        clock=clock,
    )
    assert recovered["state"] == "completed"
    assert recovered["idempotent"] is True
    assert outbox.list(scope) == [manifest]


def test_reaper_finalizes_legacy_completing_turn_before_retention_cleanup(
    tmp_path, monkeypatch
):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    manifest = outbox.put_path(scope=scope, path=shared)

    with sqlite3.connect(db) as conn:
        conn.execute(
            """UPDATE desktop_room_turn_grants
                  SET state='completing', artifact_ids_json=?, updated_at=?
                WHERE task_id=?""",
            (json.dumps([manifest["artifact_id"]]), clock.value, turn["task_id"]),
        )

    clock.value += mailbox.TURN_RETENTION_SECONDS + 1
    mailbox.reap_stale_turns(db, profile="reviewer", clock=clock)
    with sqlite3.connect(db) as conn:
        state = conn.execute(
            "SELECT state FROM desktop_room_turn_grants WHERE task_id=?",
            (turn["task_id"],),
        ).fetchone()[0]
    assert state == "completed"
    assert outbox.list(scope) == [manifest]

    clock.value += mailbox.TERMINAL_RETENTION_SECONDS + 1
    mailbox.reap_stale_turns(db, profile="reviewer", clock=clock)
    assert outbox.list(scope) == []


def test_turn_survives_desktop_presence_gap_for_same_owner(tmp_path, monkeypatch):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )

    clock.value += mailbox.PRESENCE_TTL_SECONDS + 1
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="stale"):
        mailbox.turn_artifacts(db, token=turn["token"], clock=clock)

    mailbox.claim_commands(
        db,
        consumer_id="desktop:owner",
        room_authorities=authorities("room-1"),
        clock=clock,
    )
    resumed = begin_room_turn(db, clock)
    assert resumed["token"] == turn["token"]
    assert mailbox.turn_artifacts(
        db, token=turn["token"], clock=clock
    )["state"] == "submitted"


def test_cancel_discards_uncommitted_output_and_cannot_be_reopened(
    tmp_path, monkeypatch
):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    outbox.put_path(scope=scope, path=shared)

    cancelled = mailbox.cancel_turn(db, token=turn["token"], clock=clock)
    replay = mailbox.cancel_turn(db, token=turn["token"], clock=clock)
    assert cancelled["state"] == "cancelled"
    assert replay["idempotent"] is True
    assert outbox.list(scope) == []
    with pytest.raises(mailbox.DesktopRoomMailboxError, match="no longer active"):
        begin_room_turn(db, clock)


def test_cancel_retries_cleanup_after_a_crash(tmp_path, monkeypatch):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    outbox.put_path(scope=scope, path=shared)
    real_discard = outbox.discard
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("process stopped before discard")
        return real_discard(*args, **kwargs)

    monkeypatch.setattr(outbox, "discard", fail_once)
    with pytest.raises(OSError, match="stopped before discard"):
        mailbox.cancel_turn(db, token=turn["token"], clock=clock)

    cancelled = mailbox.cancel_turn(db, token=turn["token"], clock=clock)
    assert cancelled["state"] == "cancelled"
    assert outbox.list(scope) == []


def test_stale_turn_cleanup_is_profile_scoped_and_purges_private_bytes(
    tmp_path, monkeypatch
):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    outbox.put_path(scope=scope, path=shared)

    clock.value += mailbox.TURN_RETENTION_SECONDS + 1
    assert mailbox.reap_stale_turns(
        db,
        profile="reviewer",
        artifact_db_path=tmp_path / "reviewer-state.db",
        clock=clock,
    ) == 1
    assert outbox.list(scope) == []


def test_stale_turn_cleanup_retries_after_discard_crash(tmp_path, monkeypatch):
    db = tmp_path / "desktop_room_mailbox.db"
    outbox = RoomArtifactOutbox(
        tmp_path / "reviewer-state.db",
        root=tmp_path / "reviewer-outbox",
    )
    monkeypatch.setattr(mailbox, "_artifact_outbox", lambda *_args: outbox)
    clock = Clock()
    own_room(db, clock)
    turn = begin_room_turn(db, clock)
    verified = mailbox.verify_turn_submission(
        db,
        token=turn["token"],
        session_key="session-1",
        profile="reviewer",
        clock=clock,
    )
    scope = RoomArtifactScope.from_mapping(verified["scope"])
    shared = tmp_path / "handoff.md"
    shared.write_text("ROOM_HANDOFF", encoding="utf-8")
    outbox.put_path(scope=scope, path=shared)
    clock.value += mailbox.TURN_RETENTION_SECONDS + 1
    real_discard = outbox.discard
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("process stopped before terminal state")
        return real_discard(*args, **kwargs)

    monkeypatch.setattr(outbox, "discard", fail_once)
    with pytest.raises(OSError, match="stopped before terminal"):
        mailbox.reap_stale_turns(
            db,
            profile="reviewer",
            artifact_db_path=tmp_path / "reviewer-state.db",
            clock=clock,
        )

    assert mailbox.reap_stale_turns(
        db,
        profile="reviewer",
        artifact_db_path=tmp_path / "reviewer-state.db",
        clock=clock,
    ) == 1
    assert outbox.list(scope) == []
