"""Route-owned admission for explicitly delegated Stop and approval controls.

The consumer supplies consent predicates, not an elevated native actor. A reservation
is a durable *admission*, never a queue to execute or a license to retry an RPC.
"""
from __future__ import annotations

from contextlib import contextmanager
import dataclasses
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable

from gateway import hosted_room_route_schema as route_schema
from hermes_state_runtime import RuntimeStoreError


class DelegatedControlUncertain(RuntimeError):
    """Admission may have committed; the caller must not resubmit the side effect."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _key(room_id, member_id, task_id, generation, request_id):
    digest = hashlib.sha256(_json([room_id, member_id, task_id, generation, request_id]).encode()).hexdigest()
    return "gateway.hosted.delegated_approval.v1:" + digest

def _stop_key(room_id, cancel_id):
    digest = hashlib.sha256(_json([room_id, cancel_id]).encode()).hexdigest()
    return "gateway.hosted.delegated_stop.v1:" + digest


@dataclass(frozen=True)
class DelegatedControl:
    """An explicit Permission-supplied consent bound to a live SessionDB/runtime.

    authorize_new and authorize_commit inspect the *supplied* immediate writer,
    including the consumer's distinct persisted consent generation and lineage.
    They must fail closed; they cannot open another connection to read consent.
    The external writer guard pins the physical SessionDB generation and rejects
    a replaced pathname. No guard is retained over an approval RPC.
    """
    db: Any
    runtime: Any
    runtime_generation: str
    authorize_new: Callable[[sqlite3.Connection], None]
    authorize_commit: Callable[[sqlite3.Connection], None]
    delegation_identity: str

    def __post_init__(self):
        if (self.db is None or self.runtime is None
                or not isinstance(self.runtime_generation, str) or not self.runtime_generation
                or not callable(self.authorize_new) or not callable(self.authorize_commit)
                or not isinstance(self.delegation_identity, str) or not self.delegation_identity):
            raise RuntimeStoreError("invalid_params")

    @contextmanager
    def writer(self, service):
        if (service.runtime is not self.runtime
                or service.runtime.process_generation != self.runtime_generation
                or Path(service.db_path).resolve() != Path(self.db.db_path).resolve()):
            raise RuntimeStoreError("runtime_coordination_required")
        with self.runtime.new_event_admission():
            with self.db.live_external_writer_lifetime() as require_external:
                def verify(conn):
                    require_external(conn)
                    if (service.runtime is not self.runtime
                            or self.runtime.process_generation != self.runtime_generation):
                        raise RuntimeStoreError("runtime_coordination_required")
                yield verify

    def new(self, verify, conn):
        verify(conn)
        self.authorize_new(conn)

    def commit(self, verify, conn):
        verify(conn)
        self.authorize_commit(conn)


def reserve_stop(service, control, room_id, cancel_id, require_acknowledged, stoppable):
    """Fence and snapshot in one event writer. Only a fresh receipt owns effects."""
    from gateway import hosted_rooms, hosted_room_driver as driver
    from gateway.hosted_rooms_common import table_exists

    key = _stop_key(room_id, cancel_id)
    committed = False
    try:
        # This lifetime must precede _owned_authority's path-opening room_state.
        with control.writer(service) as verify:
            gateway_id, epoch = service._owned_authority(room_id)
            expected = {"version": 1, "room_id": room_id, "cancel_id": cancel_id,
                        "gateway_id": gateway_id, "epoch": epoch,
                        "runtime_generation": control.runtime_generation,
                        "delegation_identity": control.delegation_identity,
                        "require_acknowledged": require_acknowledged}
            record = None

            def commit(conn):
                nonlocal record
                control.commit(verify, conn)
                rows = (conn.execute(
                    "SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND status IN ("
                    + ",".join("?" for _ in stoppable) + ") ORDER BY source_event_seq,created_at,task_id",
                    (room_id, *stoppable)).fetchall()
                    if table_exists(conn, "hosted_room_driver_tasks") else ())
                snapshot = []
                for row in rows:
                    task = driver._task_from_row(row)
                    snapshot.append({"identity": dataclasses.asdict(task["identity"]),
                                     "execution_generation": task["execution_generation"],
                                     "status": task["status"], "cancel_id": task["cancel_id"]})
                record = {**expected, "state": "reserved", "snapshot": snapshot}
                conn.execute("INSERT INTO state_meta(key,value) VALUES(?,?)", (key, _json(record)))

            event = hosted_rooms.request_room_stop(
                service.db_path, room_id=room_id, cancel_id=cancel_id,
                expected_gateway_id=gateway_id, expected_epoch=epoch,
                authorize_new=lambda conn: control.new(verify, conn), authorize_commit=commit)
            committed = True
            if event.get("idempotent"):
                with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
                    verify(conn)
                    row = conn.execute("SELECT value FROM state_meta WHERE key=?", (key,)).fetchone()
                    if row is None:
                        raise DelegatedControlUncertain("delegated Stop fence has no completion receipt")
                    record = json.loads(row[0])
                    if any(record.get(k) != v for k, v in expected.items()):
                        raise RuntimeStoreError("admission_conflict")
                return "replay", record, gateway_id, epoch
            if record is None:
                raise DelegatedControlUncertain("delegated Stop receipt was not captured")
        return "fresh", record, gateway_id, epoch
    except Exception as exc:
        if isinstance(exc, DelegatedControlUncertain):
            raise
        if committed and not isinstance(exc, RuntimeStoreError) or isinstance(exc, sqlite3.Error):
            raise DelegatedControlUncertain("delegated Stop admission may have committed") from exc
        raise


def settle_stop(service, control, record, result):
    from gateway import hosted_rooms

    key = _stop_key(record["room_id"], record["cancel_id"])
    try:
        with control.writer(service) as verify:
            with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
                verify(conn)
                row = conn.execute("SELECT value FROM state_meta WHERE key=?", (key,)).fetchone()
                if row is None or json.loads(row[0]) != record:
                    raise RuntimeStoreError("admission_conflict")
                conn.execute("UPDATE state_meta SET value=? WHERE key=?",
                             (_json({**record, "state": "settled", "result": result}), key))
        return result
    except Exception as exc:
        raise DelegatedControlUncertain("delegated Stop admitted; receipt is uncertain") from exc


def approval_identity(conn, *, room_id, member_id, task_id, generation, binding, runtime,
                      action, peer):
    """Verify live work, captured pending action, owner lease and exact egress target."""
    room = conn.execute("SELECT members_json,authority_gateway_id,authority_epoch FROM hosted_rooms "
                        "WHERE room_id=? AND disbanded_at IS NULL", (room_id,)).fetchone()
    if (room is None or room["authority_gateway_id"] != binding.gateway_id
            or room["authority_epoch"] != binding.authority_epoch):
        raise RuntimeStoreError("stale_generation")
    route_schema.require_room_work_open(conn, room_id, error=RuntimeStoreError)
    row = conn.execute("SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?",
                       (room_id, task_id)).fetchone()
    if (row is None or row["status"] != "running" or row["execution_generation"] != generation
            or row["run_gateway_id"] != binding.gateway_id
            or row["run_process_generation"] != runtime.process_generation):
        raise RuntimeStoreError("stale_generation")
    payload = json.loads(row["payload_json"])
    profile = payload.get("target_profile")
    if ((payload.get("target_member_id") or profile) != member_id
            or not any(m.get("member_id") == member_id and m.get("profile") == profile
                       for m in json.loads(room["members_json"]))):
        raise RuntimeStoreError("stale_generation")
    lease = conn.execute("SELECT * FROM hosted_room_driver_leases WHERE room_id=?", (room_id,)).fetchone()
    if (lease is None or lease["gateway_id"] != binding.gateway_id
            or lease["authority_epoch"] != binding.authority_epoch
            or lease["process_generation"] != runtime.process_generation
            or lease["lease_generation"] != row["run_lease_generation"]
            or lease["released_at"] is not None or lease["expires_at"] <= time.time()):
        raise RuntimeStoreError("stale_generation")
    if peer is not None:
        link = conn.execute("SELECT grant,target_profile,target_url,catalog_json,status FROM hosted_room_links "
                            "WHERE room_id=? AND member_id=?", (room_id, member_id)).fetchone()
        if (link is None or link["grant"] != peer["grant"]
                or link["target_profile"] != peer["profile"]
                or link["target_url"] != peer["url"] or link["status"] != "ready"
                or json.loads(link["catalog_json"])["installation_id"] != peer["install"]):
            raise RuntimeStoreError("stale_generation")
    elif not action.get("session_id"):
        raise RuntimeStoreError("stale_generation")


def reserve_approval(service, control, *, identity, binding, action, peer):
    """Return (state, record) from the one immediate writer; no external I/O."""
    from gateway import hosted_rooms

    room_id, member_id, task_id, generation, request_id, choice = identity
    key = _key(room_id, member_id, task_id, generation, request_id)
    target = (dict(peer) if peer is not None else {"session_id": str(action["session_id"])})
    expected = {"version": 1, "identity": list(identity), "target": target,
                "gateway_id": binding.gateway_id, "epoch": binding.authority_epoch,
                "runtime_generation": control.runtime_generation,
                "delegation_identity": control.delegation_identity}
    committed = False
    try:
        with control.writer(service) as verify:
            with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
                verify(conn)
                saved_row = conn.execute("SELECT value FROM state_meta WHERE key=?", (key,)).fetchone()
                if saved_row is not None:
                    saved = json.loads(saved_row[0])
                    if any(saved.get(k) != v for k, v in expected.items()):
                        raise RuntimeStoreError("admission_conflict")
                    # The prior writer decided the authorization order. Never send again.
                    return saved["state"], saved
                approval_identity(conn, room_id=room_id, member_id=member_id, task_id=task_id,
                                  generation=generation, binding=binding, runtime=service.runtime,
                                  action=action, peer=peer)
                control.new(verify, conn)
                record = {**expected, "state": "reserved"}
                conn.execute("INSERT INTO state_meta(key,value) VALUES(?,?)", (key, _json(record)))
                control.commit(verify, conn)
            committed = True
    except Exception as exc:
        if committed or isinstance(exc, sqlite3.Error):
            raise DelegatedControlUncertain("delegated approval admission may have committed") from exc
        raise
    return "reserved-new", record


def replay_approval(service, control, identity, binding):
    """Recover only a durable exact admission, even after pending was cleared."""
    from gateway import hosted_rooms

    with control.writer(service) as verify:
        with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
            verify(conn)
            row = conn.execute("SELECT value FROM state_meta WHERE key=?",
                               (_key(*identity[:5]),)).fetchone()
            if row is None:
                return None
            saved = json.loads(row[0])
            if (saved.get("identity") != list(identity)
                    or saved.get("gateway_id") != binding.gateway_id
                    or saved.get("epoch") != binding.authority_epoch
                    or saved.get("runtime_generation") != control.runtime_generation
                    or saved.get("delegation_identity") != control.delegation_identity):
                raise RuntimeStoreError("admission_conflict")
            return saved


def settle_approval(service, control, identity, record, result):
    from gateway import hosted_rooms

    key = _key(*identity[:5])
    try:
        receipt = json.loads(_json(result))
        with control.writer(service) as verify:
            with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
                verify(conn)
                row = conn.execute("SELECT value FROM state_meta WHERE key=?", (key,)).fetchone()
                if row is None or json.loads(row[0]) != record:
                    raise RuntimeStoreError("admission_conflict")
                conn.execute("UPDATE state_meta SET value=? WHERE key=?",
                             (_json({**record, "state": "settled", "result": receipt}), key))
        return receipt
    except Exception as exc:
        raise DelegatedControlUncertain("delegated approval admitted; receipt is uncertain") from exc
