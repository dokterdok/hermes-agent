"""Real-store delegated control ordering; only RPC/worker effects are synthetic."""
import threading
import time
import sqlite3
from types import SimpleNamespace

import pytest

from gateway import hosted_room_driver as driver, hosted_rooms
from gateway.hosted_room_delegated_control import DelegatedControl, DelegatedControlUncertain
from tui_gateway.hosted_room_peer_transport import PeerMemberRoute
from hermes_state import SessionDB
from hermes_state_errors import StateDbReplacedError
from hermes_state_runtime import RuntimeStoreError
from tui_gateway.hosted_room_service import HostedRoomService


def _setup(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    server = SimpleNamespace(_methods={}, _sessions={}, _sessions_lock=threading.Lock())
    service = HostedRoomService(server, db_path=db.db_path)
    service.local_profiles = lambda: ("default", "ops")
    service.create_room(room_id="room-1", name="Room", members=[
        {"member_id": "default", "profile": "default", "handle": "default"},
        {"member_id": "ops", "profile": "ops", "handle": "ops"}])
    # Exercise the real lifecycle-lock admission primitive without launching an
    # inference worker in a source-only regression.
    service.runtime._thread = SimpleNamespace(is_alive=lambda: True)
    db._execute_write(lambda conn: conn.execute(
        "INSERT INTO state_meta(key,value) VALUES('control-consent','active')"))

    def require(conn):
        row = conn.execute("SELECT value FROM state_meta WHERE key='control-consent'").fetchone()
        if row is None or row[0] != "active":
            raise RuntimeStoreError("permission_denied")

    control = DelegatedControl(db, service.runtime, service.runtime.process_generation,
                               require, require, "recipient:consent-generation-1")
    return db, service, control


def _revoke(db):
    db._execute_write(lambda conn: conn.execute(
        "UPDATE state_meta SET value='revoked' WHERE key='control-consent'"))


def _running_task(service):
    service.send(room_id="room-1", event_id="message-1",
                 payload={"text": "@ops work", "thread_id": "thread-1"})
    task = driver.list_tasks(service.db_path, room_id="room-1", status="queued")[0]
    binding = service.bindings()[0]
    lease = driver.acquire_lease(service.db_path, room_id="room-1", gateway_id=binding.gateway_id,
                                 authority_epoch=binding.authority_epoch,
                                 process_generation=service.runtime.process_generation,
                                 ttl_seconds=30, clock=time.time)
    driver.start_task(service.db_path, task["identity"], lease,
                      expected_cancel_generation=0, clock=time.time)
    task = driver.get_task(service.db_path, task["identity"])
    service._set_pending_action("room-1", "ops", {
        "kind": "approval", "task_id": task["identity"].task_id,
        "execution_generation": task["execution_generation"],
        "session_id": "exact-local-session", "request_id": "request-1",
        "approval": {"choices": ["once", "deny"]}})
    return {"room_id": "room-1", "member_id": "ops", "task_id": task["identity"].task_id,
            "execution_generation": task["execution_generation"], "choice": "once",
            "request_id": "request-1"}


def test_stop_revocation_before_fence_and_replay_never_cancels_successor(tmp_path, monkeypatch):
    db, service, control = _setup(tmp_path)
    try:
        def revoke_in_writer(conn):
            conn.execute("UPDATE state_meta SET value='revoked' WHERE key='control-consent'")
        denied = DelegatedControl(db, service.runtime, control.runtime_generation,
                                   revoke_in_writer, control.authorize_commit,
                                   control.delegation_identity)
        with pytest.raises(RuntimeStoreError, match="permission_denied"):
            service.stop_room("room-1", cancel_id="stop-1", delegated_control=denied)
        assert not any(e["kind"] == "room.stop_requested" for e in
                       hosted_rooms.read_events(service.db_path, room_id="room-1")["events"])
        db._execute_write(lambda conn: conn.execute(
            "UPDATE state_meta SET value='active' WHERE key='control-consent'"))
        monkeypatch.setattr(service, "prepare_room", lambda binding: None)
        assert service.stop_room("room-1", cancel_id="stop-1", delegated_control=control) == 0
        def no_successor(*args, **kwargs):
            raise AssertionError("replayed stop touched successor work")
        monkeypatch.setattr(service, "_list_tasks", no_successor)
        _revoke(db)
        assert service.stop_room("room-1", cancel_id="stop-1", delegated_control=control) == 0
        assert sum(e["kind"] == "room.stop_requested" for e in
                   hosted_rooms.read_events(service.db_path, room_id="room-1")["events"]) == 1
    finally:
        db.close()


def test_local_approval_revocation_before_reservation_and_settled_replay(tmp_path, monkeypatch):
    db, service, control = _setup(tmp_path)
    try:
        args = _running_task(service)
        calls = []
        service.rpc = SimpleNamespace(approve=lambda **kw: calls.append(kw) or {"resolved": 1})
        def revoke_in_writer(conn):
            conn.execute("UPDATE state_meta SET value='revoked' WHERE key='control-consent'")
        denied = DelegatedControl(db, service.runtime, control.runtime_generation,
                                   revoke_in_writer, control.authorize_commit,
                                   control.delegation_identity)
        with pytest.raises(RuntimeStoreError, match="permission_denied"):
            service.approve_room_task(**args, delegated_control=denied)
        assert not calls
        db._execute_write(lambda conn: conn.execute(
            "UPDATE state_meta SET value='active' WHERE key='control-consent'"))

        assert service.approve_room_task(**args, delegated_control=control) == {"resolved": 1}
        _revoke(db)
        assert service.approve_room_task(**args, delegated_control=control) == {"resolved": 1}
        assert calls == [{"session_id": "exact-local-session", "request_id": "request-1", "choice": "once"}]
    finally:
        db.close()


def test_reserved_approval_is_uncertain_and_not_resent_after_lost_reply(tmp_path):
    db, service, control = _setup(tmp_path)
    try:
        args = _running_task(service)
        calls = []
        def lost_reply(**kwargs):
            calls.append(kwargs)
            _revoke(db)  # No owner or DB lock is held across external egress.
            raise OSError("lost target reply")
        service.rpc = SimpleNamespace(approve=lost_reply)
        with pytest.raises(DelegatedControlUncertain):
            service.approve_room_task(**args, delegated_control=control)
        with pytest.raises(DelegatedControlUncertain):
            service.approve_room_task(**args, delegated_control=control)
        assert len(calls) == 1
    finally:
        db.close()


def test_peer_deny_captures_persisted_grant_and_never_looks_up_replacement(tmp_path):
    db, service, control = _setup(tmp_path)
    try:
        args = _running_task(service)
        args["choice"] = "deny"
        route = PeerMemberRoute(
            home_install_id=hosted_rooms.local_authority_gateway_id(), member_id="ops",
            target_install_id="peer-install", target_profile="ops",
            capability_digest="catalog-digest", cancellation_scope_id="cancel-room",
            trace_id="trace-room", grant="signed.peer.grant")
        calls = []
        class Peer:
            base_url = "https://peer.example.test"
            def approve_receipt(self, **kwargs):
                calls.append(kwargs)
                service.peer_routes[("room-1", "ops")] = PeerMemberRoute(
                    home_install_id=route.home_install_id, member_id="ops",
                    target_install_id="replacement", target_profile="ops",
                    capability_digest="replacement", cancellation_scope_id="cancel-room",
                    trace_id="trace-room", grant="replacement-grant")
                _revoke(db)
                return {"resolved": 1}
        service.peer_routes[("room-1", "ops")] = route
        service.peer_clients[("room-1", "ops")] = Peer()
        service._persisted_peer_route_keys.add(("room-1", "ops"))
        with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
            conn.execute("INSERT INTO hosted_room_links(room_id,member_id,target_url,target_profile,grant,"
                         "catalog_json,cancellation_scope_id,trace_id,transport_security,status,updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         ("room-1", "ops", "https://peer.example.test", "ops", route.grant,
                          '{"installation_id":"peer-install"}', "cancel-room", "trace-room",
                          "tls", "ready", time.time()))
        with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
            conn.execute("UPDATE hosted_room_links SET grant='replacement-grant' "
                         "WHERE room_id='room-1' AND member_id='ops'")
        with pytest.raises(RuntimeStoreError, match="stale_generation"):
            service.approve_room_task(**args, delegated_control=control)
        assert not calls
        with hosted_rooms._transaction(service.db_path, immediate=True) as conn:
            conn.execute("UPDATE hosted_room_links SET grant=? "
                         "WHERE room_id='room-1' AND member_id='ops'", (route.grant,))
        assert service.approve_room_task(**args, delegated_control=control) == {"resolved": 1}
        assert calls == [{"task_id": args["task_id"], "execution_generation": 1,
                          "request_id": "request-1", "choice": "deny", "grant": route.grant}]
        assert service.approve_room_task(**args, delegated_control=control) == {"resolved": 1}
        assert len(calls) == 1
    finally:
        db.close()


@pytest.mark.parametrize("entry", ["stop", "approval"])
@pytest.mark.parametrize("replacement", [False, True])
def test_delegated_lifetime_rejects_replaced_store_and_runtime_generation(tmp_path, entry, replacement):
    db, service, control = _setup(tmp_path)
    try:
        service.runtime.process_generation = "replacement-runtime"
        with pytest.raises(RuntimeStoreError, match="runtime_coordination_required"):
            if entry == "stop":
                service.stop_room("room-1", cancel_id="generation-stop", delegated_control=control)
            else:
                service.approve_room_task("room-1", member_id="ops", task_id="missing",
                    execution_generation=1, request_id="missing", choice="deny", delegated_control=control)
        service.runtime.process_generation = control.runtime_generation
        db.db_path.rename(tmp_path / "retired-state.db")
        if replacement:
            # A foreign pathname with a recognizable sentinel; inspect it without
            # reopening SQLite against the retired owner's old WAL/SHM generation.
            db.db_path.write_bytes(b"foreign-store-sentinel")
        with pytest.raises((sqlite3.Error, StateDbReplacedError)):
            if entry == "stop":
                service.stop_room("room-1", cancel_id="generation-stop", delegated_control=control)
            else:
                service.approve_room_task("room-1", member_id="ops", task_id="missing",
                    execution_generation=1, request_id="missing", choice="deny", delegated_control=control)
        if replacement:
            assert db.db_path.read_bytes() == b"foreign-store-sentinel"
    finally:
        db.close()


def test_native_unscoped_stop_and_approval_remain_available(tmp_path):
    db, service, _control = _setup(tmp_path)
    try:
        assert service.stop_room("room-1", cancel_id="native-stop") == 0
        assert any(e["kind"] == "room.stop_requested" for e in
                   hosted_rooms.read_events(service.db_path, room_id="room-1")["events"])
        service._set_pending_action("room-1", "ops", {
            "kind": "approval", "task_id": "native-task", "execution_generation": 1,
            "session_id": "native-session", "request_id": "native-request",
            "approval": {"choices": ["once", "deny"]}})
        calls = []
        service.rpc = SimpleNamespace(approve=lambda **kw: calls.append(kw) or {"resolved": 1})
        assert service.approve_room_task("room-1", member_id="ops", task_id="native-task",
                                         execution_generation=1, choice="deny",
                                         request_id="native-request") == {"resolved": 1}
        assert calls == [{"session_id": "native-session", "request_id": "native-request",
                          "choice": "deny"}]
        with pytest.raises(hosted_rooms.HostedRoomError, match="invalid delegated control"):
            service.stop_room("room-1", cancel_id="invalid", delegated_control={"partial": True})
    finally:
        db.close()


def test_commit_guard_rolls_back_stop_and_post_commit_failure_is_uncertain(tmp_path, monkeypatch):
    db, service, control = _setup(tmp_path)
    try:
        def revoke_inside_writer(conn):
            conn.execute("UPDATE state_meta SET value='revoked' WHERE key='control-consent'")
        guarded = DelegatedControl(db, service.runtime, control.runtime_generation,
                                   revoke_inside_writer, control.authorize_commit,
                                   control.delegation_identity)
        with pytest.raises(RuntimeStoreError, match="permission_denied"):
            service.stop_room("room-1", cancel_id="commit-denied", delegated_control=guarded)
        assert not any(e["kind"] == "room.stop_requested" for e in
                       hosted_rooms.read_events(service.db_path, room_id="room-1")["events"])
        with db._read_ctx() as conn:
            assert conn.execute("SELECT value FROM state_meta WHERE key='control-consent'").fetchone()[0] == "active"
        def fail_after_fence(_binding):
            raise RuntimeError("worker unavailable after durable fence")
        monkeypatch.setattr(service, "prepare_room", fail_after_fence)
        with pytest.raises(DelegatedControlUncertain):
            service.stop_room("room-1", cancel_id="admitted-uncertain", delegated_control=control)
        assert sum(e["kind"] == "room.stop_requested" for e in
                   hosted_rooms.read_events(service.db_path, room_id="room-1")["events"]) == 1
        with pytest.raises(DelegatedControlUncertain):
            service.stop_room("room-1", cancel_id="admitted-uncertain", delegated_control=control)
    finally:
        db.close()


def test_approval_commit_denial_rolls_back_and_local_target_is_captured(tmp_path):
    db, service, control = _setup(tmp_path)
    try:
        args = _running_task(service)
        old_calls, replacement_calls = [], []
        service.rpc = SimpleNamespace(approve=lambda **kw: old_calls.append(kw) or {"resolved": 1})
        def change_target_and_revoke(conn):
            service.rpc = SimpleNamespace(
                approve=lambda **kw: replacement_calls.append(kw) or {"resolved": 1})
            conn.execute("UPDATE state_meta SET value='revoked' WHERE key='control-consent'")
        denied = DelegatedControl(db, service.runtime, control.runtime_generation,
                                  change_target_and_revoke, control.authorize_commit,
                                  control.delegation_identity)
        with pytest.raises(RuntimeStoreError, match="permission_denied"):
            service.approve_room_task(**args, delegated_control=denied)
        assert not old_calls and not replacement_calls
        with db._read_ctx() as conn:
            assert conn.execute("SELECT value FROM state_meta WHERE key='control-consent'").fetchone()[0] == "active"
        service.rpc = SimpleNamespace(approve=lambda **kw: old_calls.append(kw) or {"resolved": 1})
        def switch_after_capture(conn):
            service.rpc = SimpleNamespace(
                approve=lambda **kw: replacement_calls.append(kw) or {"resolved": 1})
            control.authorize_new(conn)
        winning = DelegatedControl(db, service.runtime, control.runtime_generation,
                                   switch_after_capture, control.authorize_commit,
                                   control.delegation_identity)
        assert service.approve_room_task(**args, delegated_control=winning) == {"resolved": 1}
        assert old_calls == [{"session_id": "exact-local-session", "request_id": "request-1",
                              "choice": "once"}]
        assert not replacement_calls
    finally:
        db.close()


def test_concurrent_admission_is_uncertain_until_settled_and_choice_cannot_change(tmp_path):
    db, service, control = _setup(tmp_path)
    try:
        args = _running_task(service)
        calls = []
        def approve(**kw):
            calls.append(kw)
            with pytest.raises(DelegatedControlUncertain):
                service.approve_room_task(**args, delegated_control=control)
            with pytest.raises(RuntimeStoreError, match="admission_conflict"):
                service.approve_room_task(**{**args, "choice": "deny"}, delegated_control=control)
            _revoke(db)  # Admission won; the exact captured RPC may finish.
            return {"resolved": 1}
        service.rpc = SimpleNamespace(approve=approve)
        assert service.approve_room_task(**args, delegated_control=control) == {"resolved": 1}
        assert service.approve_room_task(**args, delegated_control=control) == {"resolved": 1}
        assert len(calls) == 1
    finally:
        db.close()


def test_stop_first_fence_captures_only_original_attempt(tmp_path, monkeypatch):
    db, service, control = _setup(tmp_path)
    try:
        original = _running_task(service)
        append_stop = hosted_rooms.request_room_stop
        successor = []
        def interleave(*args, **kwargs):
            result = append_stop(*args, **kwargs)
            if not result.get("idempotent") and not successor:
                event = service.send(room_id="room-1", event_id="message-successor",
                                     payload={"text": "@ops successor", "thread_id": "thread-successor"})
                successor.append(driver.admit_task(service.db_path,
                    driver.TaskIdentity("room-1", "successor-task", "thread-successor", "turn-successor"),
                    payload={"target_profile": "ops", "prompt": "successor",
                             "source_event_seq": event["seq"]}, clock=time.time))
            return result
        monkeypatch.setattr(hosted_rooms, "request_room_stop", interleave)
        monkeypatch.setattr(service, "prepare_room", lambda binding: None)
        assert service.stop_room("room-1", cancel_id="fenced-stop", delegated_control=control) == 1
        assert successor and driver.get_task(service.db_path, successor[0]["identity"])["status"] == "queued"
        assert service.stop_room("room-1", cancel_id="fenced-stop", delegated_control=control) == 1
        assert driver.get_task(service.db_path, successor[0]["identity"])["status"] == "queued"
    finally:
        db.close()


def test_stop_snapshot_survives_failure_before_effect_and_replay_is_uncertain(tmp_path, monkeypatch):
    db, service, control = _setup(tmp_path)
    try:
        task = _running_task(service)
        def fail(*a, **kw):
            raise OSError("before first captured effect")
        monkeypatch.setattr(service.runtime, "cancel", fail)
        with pytest.raises(DelegatedControlUncertain):
            service.stop_room("room-1", cancel_id="failed-stop", delegated_control=control)
        current = driver.list_tasks(service.db_path, room_id="room-1")[0]
        assert current["identity"].task_id == task["task_id"] and current["status"] == "running"
        monkeypatch.setattr(service.runtime, "cancel", lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("replay must not recancel")))
        with pytest.raises(DelegatedControlUncertain):
            service.stop_room("room-1", cancel_id="failed-stop", delegated_control=control)
    finally:
        db.close()


def test_acknowledged_stop_cannot_settle_pending_attempt_or_recancel_on_replay(tmp_path, monkeypatch):
    db, service, control = _setup(tmp_path)
    try:
        task = _running_task(service)
        monkeypatch.setattr(service.runtime, "finish_cancel", lambda binding, captured, **kw: captured)
        monkeypatch.setattr(service, "prepare_room", lambda binding: None)
        with pytest.raises(DelegatedControlUncertain):
            service.stop_room("room-1", cancel_id="pending-stop", require_acknowledged=True,
                              delegated_control=control)
        current = driver.list_tasks(service.db_path, room_id="room-1")[0]
        assert current["identity"].task_id == task["task_id"]
        assert current["status"] == "stopping"
        monkeypatch.setattr(service.runtime, "cancel", lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("replay must not recancel pending work")))
        with pytest.raises(DelegatedControlUncertain):
            service.stop_room("room-1", cancel_id="pending-stop", require_acknowledged=True,
                              delegated_control=control)
        with pytest.raises(RuntimeStoreError, match="admission_conflict"):
            service.stop_room("room-1", cancel_id="pending-stop", require_acknowledged=False,
                              delegated_control=control)
    finally:
        db.close()


def test_runtime_generation_drift_during_driver_transition_is_fenced(tmp_path, monkeypatch):
    db, service, _ = _setup(tmp_path)
    try:
        task = _running_task(service)
        identity = next(t["identity"] for t in driver.list_tasks(service.db_path, room_id="room-1"))
        binding = service.bindings()[0]
        lease = driver.acquire_lease(service.db_path, room_id="room-1", gateway_id=binding.gateway_id,
            authority_epoch=binding.authority_epoch, process_generation=service.runtime.process_generation,
            ttl_seconds=30, clock=time.time)
        original = driver.begin_task_cancel
        def race(db_path, identity, **kw):
            attempt = driver.TaskAttempt(identity, lease, task["execution_generation"], 0)
            driver.requeue_not_admitted_task(db_path, attempt, clock=time.time)
            driver.start_task(db_path, identity, lease, expected_cancel_generation=0, clock=time.time)
            return original(db_path, identity, **kw)
        monkeypatch.setattr(driver, "begin_task_cancel", race)
        with pytest.raises(driver.StaleTaskError):
            service.runtime.cancel(identity, cancel_id="old-attempt", capture_only=True,
                                   expected_execution_generation=task["execution_generation"])
        current = driver.get_task(service.db_path, identity)
        assert current["status"] == "running" and current["execution_generation"] > task["execution_generation"]
    finally:
        db.close()
