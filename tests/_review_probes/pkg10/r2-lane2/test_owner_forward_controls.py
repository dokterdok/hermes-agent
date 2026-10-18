"""Round-2 review: exercise accepted forwarding through public legacy RPCs.

On the introducing commit's parent, refusal with no admitted input is safe.
At head, an accepted prompt must retain its controls and pending visibility.
Real DB, authority, admission and event fanout; discovery/socket only are replaced.
"""
import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import (
    admit_session_input, begin_runtime_epoch, claim_session_input,
    list_session_admissions, settle_session_input,
)
from tui_gateway import server
from tests.waiting import wait_until


class OwnerClient:
    def __init__(self, authority):
        from gateway.session_controls import AuthorityConnection
        self.loop, self.events = asyncio.get_running_loop(), asyncio.Queue()
        self.conn = AuthorityConnection(authority, self, {"user_id": "desktop"})

    def write(self, frame):
        self.loop.call_soon_threadsafe(self.events.put_nowait, frame)
        return True

    def close(self):
        pass

    async def rpc(self, method, **params):
        reply = await self.conn.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in reply:
            from hermes_cli.gateway_client import rpc_error
            raise rpc_error(reply["error"])
        return reply["result"]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    from gateway.session_authority import LiveSession, SessionAuthority
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    for name in ("_schedule_agent_build", "_schedule_session_cap_enforcement", "_register_session_cwd", "_start_agent_build"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_restart_completed_failed_agent_build", lambda *_a: False)
    monkeypatch.setattr(server, "_run_after_agent_ready", lambda *_a: pytest.fail("local execution"))
    emitted = []
    monkeypatch.setattr(server, "_emit", lambda e, s, p=None: emitted.append((e, s, p)) or True)
    result = server.handle_request({"id": "c", "method": "session.create", "params": {"source": "desktop"}})["result"]
    sid, key = result["session_id"], result["stored_session_id"]
    session = server._sessions[sid]
    db.create_session(key, "test")
    epoch = begin_runtime_epoch(db, instance_id="gateway")
    admit_session_input(db, epoch=epoch, principal_id="bot", session_id=key, request_id="dm-1", payload={"text": "bot turn"})
    bot = claim_session_input(db, epoch=epoch, session_id=key)
    authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id="owned", instance_id="gateway", db=db, epoch=epoch)
    authority.sessions[key] = LiveSession(SimpleNamespace(platform=None, user_id="desktop"), "route")
    monkeypatch.setattr(authority, "_schedule", lambda _ref: None)
    clients, closed = [], []
    try:
        from tui_gateway import owner_forward
    except ImportError:
        owner_forward = None
    if owner_forward is not None:
        @contextlib.asynccontextmanager
        async def connect(_home, _endpoint):
            client = OwnerClient(authority)
            clients.append(client)
            try:
                yield client
            finally:
                await client.conn.close()
                closed.append(client)
        monkeypatch.setattr(owner_forward, "discover_owner", lambda _home: "ready")
        monkeypatch.setattr(owner_forward, "connect_owner", connect)
        monkeypatch.setattr(owner_forward, "RELAY_FINALIZE_POLL_S", 0.02)
    state = SimpleNamespace(db=db, sid=sid, key=key, session=session, epoch=epoch, bot=bot,
                            authority=authority, emitted=emitted)
    try:
        yield state
    finally:
        session["_finalized"] = True
        if clients:
            assert wait_until(lambda: len(closed) == len(clients), timeout=5)
        server._sessions.pop(sid, None)
        db.close()


def submit(rig, text="my question"):
    reply = server.handle_request({"id": "p", "method": "prompt.submit", "params": {"session_id": rig.sid, "text": text}})
    if "error" in reply:
        assert reply["error"]["data"]["reason"] == "SESSION_NOT_OWNED", reply
        assert len(list_session_admissions(rig.db, session_id=rig.key)) == 1
        return None
    assert reply["result"]["forwarded"] is True
    return reply["result"]["admission_id"]


def start_forwarded(rig, admission_id):
    from gateway.session_contract import SessionRef
    settle_session_input(rig.db, epoch=rig.epoch, admission_id=rig.bot["admission_id"], generation=rig.bot["generation"], outcome="completed")
    row = claim_session_input(rig.db, epoch=rig.epoch, session_id=rig.key)
    assert row["admission_id"] == admission_id
    live = rig.authority.sessions[rig.key]
    rig.authority._stamp_claim(SessionRef("owned", rig.key), live, row)
    assert wait_until(lambda: any(e == "message.start" for e, _, _ in rig.emitted), timeout=5)
    return live, row


def test_accepted_forwarded_queue_card_can_be_cancelled(rig):
    admission = submit(rig)
    if admission is None:
        return
    reply = server.handle_request({"id": "cancel", "method": "prompt.cancel", "params": {"session_id": rig.sid, "admission_id": admission}})
    assert "error" not in reply, f"accepted queue card cannot cancel its owner admission: {reply}"
    remaining = list_session_admissions(rig.db, session_id=rig.key)
    assert not any(row["admission_id"] == admission for row in remaining)


def test_accepted_forwarded_turn_stop_reaches_owner(rig, monkeypatch):
    admission = submit(rig)
    if admission is None:
        return
    start_forwarded(rig, admission)
    interrupts = []
    async def interrupt(*args, **kwargs):
        interrupts.append((args, kwargs))
        return {}
    monkeypatch.setattr(rig.authority, "interrupt", interrupt)
    reply = server.handle_request({"id": "stop", "method": "session.interrupt", "params": {"session_id": rig.sid}})
    assert "error" not in reply, reply
    assert interrupts, f"Stop returned {reply} without interrupting gateway execution"


def test_two_forwarded_prompts_remain_visible_in_pending_snapshot(rig):
    first = submit(rig, "first")
    if first is None:
        return
    second = submit(rig, "second")
    assert wait_until(lambda: any(e == "session.info" and any(r["admission_id"] == second for r in p.get("pending_submissions", [])) for e, _, p in rig.emitted), timeout=5)
    snapshots = [p["pending_submissions"] for e, _, p in rig.emitted if e == "session.info"]
    assert {r["admission_id"] for r in snapshots[-1]} == {first, second}, snapshots


def test_forwarded_clarification_is_delivered_to_desktop(rig):
    admission = submit(rig)
    if admission is None:
        return
    live, row = start_forwarded(rig, admission)
    entry = SimpleNamespace(clarify_id="question-1", question="Which file?", choices=["A", "B"], multi_select=False, event=threading.Event())
    live.controls.register_clarify(rig.key, row["generation"], entry)
    # A later relayed frame is a deterministic barrier: the question precedes it in the same FIFO.
    live.event_stream.publish(rig.key, {"text": "barrier"}, event_type="message.complete")
    assert wait_until(lambda: any(e == "message.complete" for e, _, _ in rig.emitted), timeout=5)
    assert any("clarify" in e for e, _, _ in rig.emitted), rig.emitted


def test_post_commit_storage_error_does_not_offer_a_duplicate_retry(rig, monkeypatch):
    """The actual owner can return a structured storage error AFTER its admission commits."""
    import sqlite3
    from hermes_state_runtime import recover_session_inputs

    publish = rig.authority._publish_pending
    failed = False
    def fail_first_publication(ref):
        nonlocal failed
        if not failed:
            failed = True
            # _publish_pending normally reads get_session / list_session_admissions here.
            raise sqlite3.OperationalError("disk I/O error")
        return publish(ref)
    monkeypatch.setattr(rig.authority, "_publish_pending", fail_first_publication)
    request = {"id": "p", "method": "prompt.submit", "params": {"session_id": rig.sid, "text": "perform the operation"}}
    reply = server.handle_request(request)
    mine = [r for r in list_session_admissions(rig.db, session_id=rig.key) if r["payload"].get("text") == "perform the operation"]
    if not mine:
        assert reply["error"]["data"]["reason"] == "SESSION_NOT_OWNED"
        return
    assert len(mine) == 1
    if reply["error"]["data"]["reason"] == "SESSION_OWNER_BUSY":
        retry = server.handle_request(request)
        assert retry["result"]["forwarded"] is True
        mine = [r for r in list_session_admissions(rig.db, session_id=rig.key) if r["payload"].get("text") == "perform the operation"]
        assert len(mine) == 2  # Following the advertised Retry duplicates durable work.
        epoch = begin_runtime_epoch(rig.db, instance_id="restart")
        recover_session_inputs(rig.db, epoch=epoch)
        assert all(r["status"] == "queued" for r in list_session_admissions(rig.db, session_id=rig.key) if r["admission_id"] in {m["admission_id"] for m in mine})
    assert reply["error"]["data"]["reason"] == "SESSION_OWNER_UNCONFIRMED", (reply, len(mine))


def test_forwarded_voice_prompt_keeps_its_context_and_model_route(rig):
    from gateway.session_surface import arm_surface_turn, surface_turn_scope
    params = {"session_id": rig.sid, "text": "Send it to her", "surface": "voice-live",
              "voice_context": "We discussed sending report.pdf to Alice.", "voice_turn": True}
    reply = server.handle_request({"id": "voice", "method": "prompt.submit", "params": params})
    if "error" in reply:
        assert reply["error"]["data"]["reason"] == "SESSION_NOT_OWNED"
        assert len(list_session_admissions(rig.db, session_id=rig.key)) == 1
        return
    admission = reply["result"]["admission_id"]
    row = next(r for r in list_session_admissions(rig.db, session_id=rig.key) if r["admission_id"] == admission)
    agent = SimpleNamespace(valid_tool_names=[])
    with surface_turn_scope(row["payload"].get("surface_v1", {})):
        arm_surface_turn(agent)
    assert agent._voice_turn_pending is True, f"accepted voice prompt lost auxiliary.voice_chat routing: {row['payload']}"
    assert row["payload"]["surface_v1"]["voice_context"] == params["voice_context"]
