"""Disposable source/receiver fixtures. No runtime, model, or source Stop flow."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gateway import hosted_room_driver as driver
from gateway import hosted_room_passive_lineage as lineage
from gateway import hosted_room_peer as peer
from gateway import hosted_room_replica_ingress as ingress
from gateway import hosted_room_replica_retirement as retirement
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms

HOME = "install:home"
SUCCESSOR = "install:successor"
TARGET = "install:target"
OWNER_KEY = "disposable-passive-test-api-key"
MEMBERS = [
    {"member_id": "writer", "profile": "default", "handle": "writer"},
    {"member_id": "reviewer", "profile": "default", "handle": "reviewer", "target": {
        "kind": "peer", "installation_id": TARGET, "profile": "default",
        "peer_id": "peer-reviewer", "capability_digest": "b" * 64}},
]


def signed_grant(secret, *, gateway=HOME, epoch=1, permissions=("replicate", "work_records"), grant_id="fixture"):
    # Parent issuer opt-in is not registered yet. These are donor-format signed
    # fixtures, verified by the real shared decoder; no auth bypass is patched.
    token = peer.issue_room_grant(secret, grant_id=grant_id, room_id="room", home_install_id=gateway,
        authority_gateway_id=gateway, authority_epoch=epoch, member_id="reviewer",
        target_install_id=TARGET, target_profile="default", permissions=("status",))
    payload = json.loads(peer._split_token(token)[0])
    payload["permissions"] = list(permissions)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    def b64(value):
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return b64(encoded) + "." + b64(hmac.new(secret, encoded, hashlib.sha256).digest())


@dataclass
class Pair:
    source: Path
    target: Path
    secret: bytes = field(repr=False)
    token: str = field(repr=False)
    members: list
    gateway: str = HOME
    epoch: int = 1

    def reserve(self):
        claims = peer.decode_room_grant(self.secret, self.token, permission="replicate")
        rooms.reserve_peer_room(self.target, claims=claims, expires_at=claims["status_expires_at"])

    def page(self, **kwargs):
        page = rooms.read_events(self.source, room_id="room", **kwargs)
        if self.epoch == 2:
            with rooms._transaction(self.source) as conn:
                _, digest = lineage.source_locked(conn, "room", page["authority"])
            page.update(replica_version=2, lineage_sha256=digest)
        return page

    def copy(self, **kwargs):
        return ingress.ingest_granted_page(self.target, token=self.token, secret=self.secret,
            target_install_id=TARGET, target_profile="default", room_id="room", room_name="Workshop",
            members=self.members, page=self.page(**kwargs))

    def record(self):
        return work.capture(self.source, room_id="room", local_gateway_id=self.gateway)

    def ingest_work(self, record=None):
        return work.ingest(self.target, record=self.record() if record is None else record,
            token=self.token, secret=self.secret, target_install_id=TARGET, target_profile="default")

    def enrollment(self):
        value = dict(enrollment_id="enrollment", room_id="room", authority_gateway_id=self.gateway,
            authority_epoch=self.epoch, target_install_id=TARGET,
            roster_sha256=retirement.roster_digest(self.members), nonce="fixture-closing-nonce")
        history = None
        if self.epoch == 2:
            with rooms._transaction(self.source) as conn:
                history, digest = lineage.source_locked(conn, "room", {"gateway_id": self.gateway, "epoch": 2})
            value.update(version=2, lineage_sha256=digest)
        closing_value = retirement._closing_value(self.secret, value)
        value["commitment"] = retirement._commitment(closing_value, value)
        return retirement._public(value), history, closing_value

    def enroll(self):
        public, history, closing_value = self.enrollment()
        retirement.enroll_target(self.target, enrollment=public, authority_history=history, target_install_id=TARGET)
        return public, closing_value

    def successor_fixture(self):
        # Model an already-retained canonical claim, never execute a takeover.
        with rooms._transaction(self.source, immediate=True) as conn:
            conn.execute("INSERT INTO hosted_room_events VALUES ('room',3,'claim','authority.claimed',?,2,?,12)",
                (json.dumps({"kind": "system", "id": "authority-control"}), json.dumps({
                    "previous_gateway_id": HOME, "authority_gateway_id": SUCCESSOR, "authority_epoch": 2})))
            conn.execute("UPDATE hosted_rooms SET authority_gateway_id=?,authority_epoch=2,next_seq=4 WHERE room_id='room'", (SUCCESSOR,))
        self.gateway, self.epoch = SUCCESSOR, 2
        self.token = signed_grant(self.secret, gateway=SUCCESSOR, epoch=2, grant_id="successor")
        self.reserve()


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    monkeypatch.setattr(rooms, "default_db_path", lambda: target)
    monkeypatch.setattr(rooms, "local_authority_gateway_id", lambda: TARGET)
    secret = peer.gateway_room_grant_secret()
    rooms.create_room(source, room_id="room", name="Workshop", members=MEMBERS, authority_gateway_id=HOME)
    for seq in (1, 2):
        rooms.append_event(source, room_id="room", event_id=f"input-{seq}", kind="message.user",
            actor={"kind": "user", "id": "alice"}, payload={"text": "hello"}, authority_gateway_id=HOME, authority_epoch=1)
    driver.admit_task(source, driver.TaskIdentity("room", "task", "thread", "turn"),
        payload={"target_profile": "default", "target_member_id": "writer", "prompt": "private input", "source_event_seq": 1}, clock=lambda: 10)
    result = Pair(source, target, secret, signed_grant(secret), rooms.room_state(source, room_id="room")["members"])
    result.reserve()
    return result
