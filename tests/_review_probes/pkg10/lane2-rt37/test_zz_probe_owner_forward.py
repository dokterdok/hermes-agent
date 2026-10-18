"""RT-37 review: real legacy dispatch and owner admission; replace discovery/socket only.

The safety assertions also run on main, where a busy owner is refused before admission.
No provider calls or production profile state are used.
"""
import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import (
    admit_session_input, begin_runtime_epoch, claim_session_input,
    list_session_admissions,
)
from tui_gateway import server
from tests.waiting import wait_until


class OwnerSocket:
    def __init__(self, rig):
        from gateway.session_controls import AuthorityConnection
        self.rig = rig
        self.loop = asyncio.get_running_loop()
        self.events = asyncio.Queue()
        self.closed = threading.Event()
        self.conn = AuthorityConnection(rig.owner, self, {"user_id": "desktop"})

    def write(self, frame):
        self.loop.call_soon_threadsafe(self.events.put_nowait, frame)
        return True

    def close(self):
        pass

    async def rpc(self, method, **params):
        from hermes_cli.gateway_client import GatewayClientError, rpc_error
        reply = await self.conn.dispatch({"jsonrpc": "2.0", "id": 1,
                                          "method": method, "params": params})
        if "error" in reply:
            raise rpc_error(reply["error"])
        if method == "prompt.submit" and self.rig.lose_next_ack:
            self.rig.lose_next_ack = False
            raise GatewayClientError("Gateway disconnected; turn outcome is unknown")
        return reply["result"]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    from gateway.session_authority import LiveSession, SessionAuthority
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    for name in ("_schedule_agent_build", "_schedule_session_cap_enforcement",
                 "_register_session_cwd", "_start_agent_build"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_restart_completed_failed_agent_build", lambda *_a: False)
    turns, emitted = [], []
    monkeypatch.setattr(server, "_run_after_agent_ready", lambda *a: turns.append(a[1]))
    monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None:
                        emitted.append((event, sid, payload)) or True)
    created = server.handle_request({"id": "c", "method": "session.create",
                                     "params": {"source": "desktop"}})["result"]
    sid, key = created["session_id"], created["stored_session_id"]
    db.create_session(key, "test")
    epoch = begin_runtime_epoch(db, instance_id="gateway")
    admit_session_input(db, epoch=epoch, principal_id="bot", session_id=key,
                        request_id="bot-turn", payload={"text": "running bot delivery"})
    claim_session_input(db, epoch=epoch, session_id=key)
    owner = SessionAuthority(SimpleNamespace(_draining=False), profile_id="owned",
                             instance_id="gateway", db=db, epoch=epoch)
    owner.sessions[key] = LiveSession(SimpleNamespace(platform=None, user_id="desktop"), "route")
    monkeypatch.setattr(owner, "_schedule", lambda ref: None)
    result = SimpleNamespace(db=db, owner=owner, sid=sid, key=key, epoch=epoch,
                             session=server._sessions[sid], turns=turns, emitted=emitted,
                             clients=[], threads=[], lose_next_ack=False)
    try:
        from tui_gateway import owner_forward
    except ImportError:
        owner_forward = None
    if owner_forward is not None:
        @contextlib.asynccontextmanager
        async def connect(home, endpoint):
            client = OwnerSocket(result)
            result.clients.append(client)
            try:
                yield client
            finally:
                await client.conn.close()
                client.closed.set()

        import agent.memory_provider as memory_provider
        original_spawn = memory_provider.spawn_context_thread

        def spawn(*args, **kwargs):
            thread = original_spawn(*args, **kwargs)
            result.threads.append(thread)
            return thread

        monkeypatch.setattr(memory_provider, "spawn_context_thread", spawn)
        monkeypatch.setattr(owner_forward, "discover_owner", lambda home: "ready-owner")
        monkeypatch.setattr(owner_forward, "connect_owner", connect)
    yield result
    result.session["_finalized"] = True
    for client in result.clients:
        if not client.closed.is_set() and not client.loop.is_closed():
            client.loop.call_soon_threadsafe(client.events.put_nowait, ConnectionError("probe teardown"))
    for thread in result.threads:
        thread.join(timeout=5)
    server._sessions.pop(sid, None)
    db.close()


def submit(rig, text="perform this once", **extra):
    return server.handle_request({"id": "same-wire-request", "method": "prompt.submit",
                                  "params": {"session_id": rig.sid, "text": text, **extra}})


def forwarded_rows(rig):
    return [row for row in list_session_admissions(rig.db, session_id=rig.key)
            if row["principal_id"] != "bot"]


def test_retry_after_lost_owner_ack_does_not_admit_twice(rig):
    rig.lose_next_ack = True
    first = submit(rig)
    second = submit(rig)
    rows = forwarded_rows(rig)
    assert rig.turns == []
    assert len(rows) <= 1, {"admissions": [(r["request_id"], r["payload"]) for r in rows],
                            "first": first, "second": second}


def test_lost_owner_ack_does_not_claim_nothing_was_sent(rig):
    rig.lose_next_ack = True
    reply = submit(rig)
    rows = forwarded_rows(rig)
    assert not rows or "Nothing was sent" not in reply.get("error", {}).get("message", ""), reply


def test_pending_snapshot_keeps_all_forwarded_queued_inputs(rig):
    submit(rig, "first question")
    if rig.clients:
        wait_until(lambda: any(e == "session.info" and p.get("pending_submissions")
                               for e, _, p in rig.emitted), timeout=10)
    submit(rig, "second question")
    rows = forwarded_rows(rig)
    ids = {row["admission_id"] for row in rows}
    if rows:
        second = rows[-1]["admission_id"]
        wait_until(lambda: any(e == "session.info" and any(r["admission_id"] == second
                   for r in p.get("pending_submissions", [])) for e, _, p in rig.emitted), timeout=10)
    snapshots = [p["pending_submissions"] for e, _, p in rig.emitted
                 if e == "session.info" and "pending_submissions" in p]
    visible = {row["admission_id"] for row in snapshots[-1]} if snapshots else set()
    assert ids <= visible, {"queued_at_owner": ids, "latest_pending_snapshot": visible}


@pytest.mark.parametrize("fields", [
    {"display_kind": "hidden", "title_preview": "short display title"},
    {"surface": "voice-live", "voice_context": "previous spoken question", "voice_turn": True},
])
def test_forward_preserves_supported_prompt_payload(rig, fields):
    from gateway.session_display import admit_display
    from gateway.session_surface import admit_surface
    expected_payload = {**admit_display(fields), **admit_surface(fields)}
    submit(rig, **fields)
    for row in forwarded_rows(rig):
        for name, expected in expected_payload.items():
            assert row["payload"].get(name) == expected, row["payload"]


def test_finalized_session_releases_its_owner_relay(rig):
    submit(rig)
    if rig.clients:
        wait_until(lambda: any(e == "session.info" for e, _, _ in rig.emitted), timeout=10)
    server._finalize_session(rig.session)
    assert all(client.closed.wait(2) for client in rig.clients), "relay still holds its thread/socket after finalization"


def test_desktop_stop_reaches_started_forwarded_turn(rig):
    from gateway.session_contract import SessionRef
    from hermes_state_runtime import settle_session_input

    reply = submit(rig)
    if not rig.clients:
        assert reply["error"]["data"]["reason"] == "SESSION_NOT_OWNED"
        return  # main never starts this work, so there is no uninterruptible forwarded turn
    bot = list_session_admissions(rig.db, session_id=rig.key)[0]
    settle_session_input(rig.db, epoch=rig.epoch, admission_id=bot["admission_id"],
                         generation=bot["generation"], outcome="completed")
    row = claim_session_input(rig.db, epoch=rig.epoch, session_id=rig.key)
    assert row["admission_id"] == reply["result"]["admission_id"]
    ref = SessionRef("owned", rig.key)
    rig.owner._stamp_claim(ref, rig.owner.sessions[rig.key], row)
    stopped = threading.Event()
    rig.owner.adopted[rig.key] = (row["generation"], SimpleNamespace(hard_interrupt=stopped.set))
    wait_until(lambda: any(e == "message.start" for e, _, _ in rig.emitted), timeout=10)
    result = server.handle_request({"id": "stop", "method": "session.interrupt",
                                    "params": {"session_id": rig.sid}})
    reached_owner = stopped.wait(2)
    # Control: the same owner's canonical interrupt reaches this exact claimed generation.
    client = rig.clients[0]
    asyncio.run_coroutine_threadsafe(client.rpc("session.interrupt", session_id=rig.key,
                                    execution_generation=row["generation"]), client.loop).result(timeout=10)
    assert stopped.is_set(), "invalid probe setup: canonical interrupt could not stop the owner agent"
    assert reached_owner, {"legacy_stop_reply": result, "owner_generation_still_unstopped": row["generation"]}
