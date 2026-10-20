"""The classic CLI dials the gateway again after it restarts (#134889).

GatewayConnection kept its finished loop thread, so after a gateway restart every call raised
"Gateway disconnected" and the CLI never re-attached, even once another client had started a new
gateway. Each prompt then failed with copy that read as a model failure ("Run /model to switch").

A socket the owner closed while it ran the turn on (its send deadline, #106369) left that turn "outcome
unknown" though it completed: the turn's own redial now re-attaches and settles it from its receipt.
"""
import asyncio
import json
import threading
import time
import types
from contextlib import asynccontextmanager

import pytest

from hermes_cli.cli_chat_error_copy import chat_error_response
from hermes_cli.gateway_agent import GatewayAgent
from hermes_cli.gateway_client import GatewayClient, GatewayClientError
from hermes_cli.gateway_connection import DISCONNECTED, GatewayConnection, lost_before
from tests.gateway.fixtures.replay_gap_owner import FINAL, Owner

SID = "local-abc"


def _frame(kind, payload):
    return {"jsonrpc": "2.0", "method": "event", "params": {
        "type": kind, "session_id": SID, "admission_id": "adm-1", "execution_generation": 7, "payload": payload}}


class Life:
    """One gateway process as one client sees it: from a dial until it hangs up."""

    def __init__(self, number, *, drop_on_submit=False, resume_errors=None, lost=None):
        self.number = number
        self.events = asyncio.Queue()
        self.calls = []
        self.drop_on_submit = drop_on_submit
        self.resume_errors = resume_errors if resume_errors is not None else []
        self.lost = lost if lost is not None else []  # admissions a life went away while running
        self.stop_after_receipt = False  # ``gateway stop`` once the receipt has been answered

    async def rpc(self, method, *, _timeout=None, **params):
        self.calls.append((method, params))
        if method == "session.resume" and self.resume_errors:
            raise self.resume_errors.pop(0)
        if method == "session.resume":
            snapshot = {"stored_session_id": params["session_id"], "subscription_id": f"sub-{self.number}",
                        "revision": 1, "execution_generation": 7, "messages": []}
            if self.lost:  # a restarted owner lists the turn its last life was running as unknown
                snapshot["pending"] = [{"admission_id": admission, "status": "unknown"} for admission in self.lost]
            return snapshot
        if method == "prompt.submit":
            loop = asyncio.get_running_loop()
            if self.drop_on_submit:  # restarted mid-turn: the outcome is unknown to this client
                self.lost.append("adm-1")
                loop.call_soon(self.events.put_nowait, GatewayClientError("Gateway disconnected"))
            else:
                loop.call_soon(self.events.put_nowait, _frame(
                    "message.complete", {"text": f"reply {self.number}", "outcome": "completed", "status": "complete"}))
            return {"admission_id": "adm-1", "status": "queued", "execution_generation": 7}
        if method == "prompt.receipt":
            if self.stop_after_receipt:  # the loss is read before the answer: the next call finds no socket
                self.events.put_nowait(GatewayClientError("Gateway disconnected"))
            return {"result": {"final_response": f"reply {self.number}", "completed": True}, "usage": {}}
        if method in ("prompt.background", "prompt.btw"):
            return {"task_id": f"task-{self.number}"}
        return {"status": "ok", "life": self.number}


class Gateway:
    """Each dial reaches a fresh gateway life; ``refusals`` makes the next dials fail first. ``up``: a
    gateway serves the home (discovery finds it ready); a dial while none does starts one, as
    ``connect_gateway`` does, and is recorded in ``started``. ``starting``: a replacement owns the home but
    is not ready yet; a dial waits for it (``ensure_gateway_runtime``) and starts none."""

    def __init__(self):
        self.up = True
        self.starting = False
        self.started = []  # the lives a dial had to start a gateway for
        self.probes = 0  # owner discoveries (``discover_gateway_endpoint``)
        self.lives = []
        self.refusals = 0
        self.drop_next_submit = False
        self.resume_errors = []  # raised by the next session.resume calls, across lives
        self.lost = []  # admissions lost with a life, which every later life lists as unknown
        self.dialing = threading.Event()
        self.hold = None  # an Event the next dials wait on before the socket opens

    @asynccontextmanager
    async def dial(self):
        self.dialing.set()
        if self.hold is not None:
            await asyncio.to_thread(self.hold.wait, 5)
        if self.refusals:
            self.refusals -= 1
            raise GatewayClientError("Gateway starting: deadline. Your message was not sent.")
        if self.starting:
            self.starting, self.up = False, True
        elif not self.up:
            self.started.append(len(self.lives) + 1)
            self.up = True
        life = Life(len(self.lives) + 1, drop_on_submit=self.drop_next_submit, resume_errors=self.resume_errors,
                    lost=self.lost)
        self.drop_next_submit = False
        self.lives.append(life)
        yield life

    def hang_up(self, conn):
        """The current life goes away (``gateway restart``); the loop thread ends with it."""
        thread = conn._thread
        conn.loop.call_soon_threadsafe(self.lives[-1].events.put_nowait, GatewayClientError("Gateway disconnected"))
        thread.join(5)
        assert not thread.is_alive()

    def stop(self, conn):
        """``gateway stop``: the life goes away and no gateway serves the home until one is started."""
        self.up = False
        self.hang_up(conn)

    def discover(self, _home, timeout):
        """``discover_gateway_endpoint`` for this home: ready, starting, or absent."""
        from hermes_cli.gateway_runtime import GatewayDiscovery
        self.probes += 1
        if self.starting:
            return GatewayDiscovery("starting")
        return GatewayDiscovery("ready", endpoint=object()) if self.up else GatewayDiscovery("absent")


@pytest.fixture
def gateway(monkeypatch):
    gw = Gateway()
    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    monkeypatch.setattr("hermes_cli.gateway_runtime.discover_gateway_endpoint", gw.discover)
    return gw


@pytest.fixture
def conn(gateway):
    connection = GatewayConnection(connect=gateway.dial)
    connection.start(5)
    connection.state(SID).adopt_snapshot(connection.call("session.resume", session_id=SID))
    yield connection
    connection.close()


def test_the_next_call_after_a_restart_reconnects_and_resubscribes(gateway, conn):
    assert conn.epoch == 1
    gateway.hang_up(conn)

    assert conn.call("status") == {"status": "ok", "life": 2}
    assert conn.epoch == 2
    # Resubscribed before the reconnect counts as ready, so no frame of the new life is missed.
    assert gateway.lives[1].calls[0] == ("session.resume", {"session_id": SID})
    assert conn.state(SID).subscription_id == "sub-2"
    lost = conn.events.get_nowait()
    assert isinstance(lost, GatewayClientError) and lost_before(lost, conn.epoch)


def test_close_ends_the_viewer_for_good(gateway, conn):
    conn.close()
    with pytest.raises(GatewayClientError, match="disconnected"):
        conn.call("status")
    assert len(gateway.lives) == 1


def test_a_failed_redial_is_retried_on_the_next_call(gateway, conn):
    gateway.hang_up(conn)
    gateway.refusals = 1
    with pytest.raises(GatewayClientError, match="Gateway starting"):
        conn.call("status")
    assert conn.call("status") == {"status": "ok", "life": 2}


def test_a_failed_redial_is_retried_even_while_its_loop_thread_is_still_exiting(gateway, conn):
    """The failed dial's loop thread reports ready before it returns; the next call must not read
    that still-alive thread as a dial in progress and re-raise its stale error."""
    gateway.hang_up(conn)
    gateway.refusals = 1
    released = threading.Event()
    run = conn._run

    def run_then_linger():
        run()
        released.wait(5)  # still alive after ``_ready.set()``, as on a loaded runner

    conn._run = run_then_linger
    with pytest.raises(GatewayClientError, match="Gateway starting"):
        conn.call("status")
    old = conn._thread
    assert old.is_alive()
    join = old.join

    def release_then_join(timeout=None):  # only the fixed path joins the exiting thread
        released.set()
        join(timeout)

    old.join = release_then_join
    try:
        assert conn.call("status") == {"status": "ok", "life": 2}
    finally:
        released.set()


def test_a_first_connect_failure_is_still_final(gateway):
    gateway.refusals = 2
    connection = GatewayConnection(connect=gateway.dial)
    for _ in range(2):
        with pytest.raises(GatewayClientError, match="Gateway starting"):
            connection.call("status")
    assert gateway.refusals == 1  # never redialed: nothing to come back to


def test_a_turn_lost_to_a_restart_is_reported_plainly_and_the_next_turn_runs(gateway, conn):
    agent = GatewayAgent(connection=conn, session_id=SID)
    gateway.lives[0].drop_on_submit = True

    lost = agent.run_conversation("first")
    assert lost["failed"] and lost["failure_reason"] == "gateway_connection"
    assert "turn outcome is unknown" in lost["error"] or "Gateway disconnected" in lost["error"]
    # The panel shows the transport error itself, not "The model request failed. Run /model ...".
    assert chat_error_response(lost["error"], failure_reason=lost["failure_reason"]) == lost["error"]
    # The turn's redial re-attached to the restarted gateway, which lists the turn as unknown: nothing resent.
    assert [method for method, _ in gateway.lives[1].calls] == ["session.resume", "session.resume"]

    result = agent.run_conversation("second")
    assert not result.get("failed") and result["final_response"] == "reply 2"
    assert [params["text"] for method, params in gateway.lives[1].calls if method == "prompt.submit"] == ["second"]


def test_a_turn_lost_to_gateway_stop_reports_it_and_starts_no_gateway(gateway, conn):
    """``gateway stop`` mid-turn: the turn's recovery would redial through ``connect_gateway``, which
    starts a gateway when none serves the home. With none serving it keeps the unknown outcome and dials
    nothing; the next prompt starts one, as before."""
    agent = GatewayAgent(connection=conn, session_id=SID)
    gateway.lives[0].drop_on_submit = True
    gateway.up = False  # stopped: its loss is the turn's

    lost = agent.run_conversation("first")
    assert lost["failed"] and lost["failure_reason"] == "gateway_connection" and lost["error"] == "Gateway disconnected"
    assert gateway.probes == 1 and gateway.started == [] and len(gateway.lives) == 1


def test_a_gateway_stop_after_the_receipt_dials_no_gateway_for_the_stats(gateway, conn):
    """``gateway stop`` once the turn's receipt was read on the open socket: the end-of-turn
    ``session.stats`` read redialed through ``connect_gateway`` and started a gateway. It is skipped; the
    turn's result is the receipt's."""
    agent = GatewayAgent(connection=conn, session_id=SID)
    gateway.lives[0].stop_after_receipt = True
    gateway.up = False

    result = agent.run_conversation("first")
    assert not result.get("failed") and result["final_response"] == "reply 1"
    assert [method for method, _ in gateway.lives[0].calls][-1] == "prompt.receipt"
    assert gateway.started == [] and len(gateway.lives) == 1


def test_the_previous_connections_loss_never_fails_a_live_turn(gateway, conn):
    """Idle when the gateway restarted: the loss is queued, and the next prompt reconnects and
    submits. Reading that loss as its own outcome would invite the user to resend accepted work."""
    agent = GatewayAgent(connection=conn, session_id=SID)
    gateway.hang_up(conn)
    result = agent.run_conversation("hi")
    assert not result.get("failed") and result["final_response"] == "reply 2"


class Socket:
    """One socket of a real ``GatewayClient`` to the real owner (``replay_gap_owner.Owner``). ``close_at``:
    the owner closes it at that delta of the turn, as its send deadline closes the socket of a viewer
    stopped mid-stream (#106369). ``hold``: its re-attach (the second ``session.resume``) sets ``held`` and
    waits on it."""

    def __init__(self, owner, close_at=None, hold=None, held=None):
        from gateway.session_controls import AuthorityConnection
        self.owner, self.close_at, self.hold, self.held, self.deltas, self.resumes = owner, close_at, hold, held, 0, 0
        self.loop, self.inbox = asyncio.get_running_loop(), asyncio.Queue()
        self.viewer = AuthorityConnection(owner.authority, self, {"user_id": "viewer"})

    def write(self, frame):  # the owner's fanout writer
        self.deltas += frame["params"]["type"] == "message.delta"
        if self.deltas == self.close_at:
            self.loop.call_soon_threadsafe(self.inbox.put_nowait, None)
            return False
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, json.dumps(frame))
        return True

    def close(self):
        return None

    async def send(self, raw):
        request = json.loads(raw)
        method, params = request["method"], request["params"]
        self.resumes += method == "session.resume"
        if method == "session.resume" and self.hold is not None and self.resumes == 2:
            self.held.set()
            await asyncio.to_thread(self.hold.wait, 30)
        verb = {"session.resume": "resume", "prompt.receipt": "receipt"}.get(method)
        if method == "prompt.submit":
            result = await self.owner.rpc(method, **params)
        elif verb:
            result = await getattr(self.viewer, verb)(self.owner.ref, {} if verb == "resume" else params)
        else:
            result = {}
        self.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))

    async def __aiter__(self):
        while (raw := await self.inbox.get()) is not None:
            yield raw
        await self.viewer.close()  # the owner's teardown of the socket it closed


def _discovers(monkeypatch, state):
    """``discover_gateway_endpoint`` answers ``state()`` for this home (ready, starting or absent), so
    ``gateway_serving`` decides on a discovery as it does live."""
    from hermes_cli.gateway_runtime import GatewayDiscovery
    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    monkeypatch.setattr("hermes_cli.gateway_runtime.discover_gateway_endpoint", lambda _home, timeout: GatewayDiscovery(
        state(), endpoint=object() if state() == "ready" else None))


@pytest.fixture
def owned(tmp_path, monkeypatch):
    """A connection whose first socket the owner closes mid-turn, while the owner keeps serving the home.
    That socket's teardown lasts until the turn has completed (the viewer was away while it finished, and
    its loss is read before the teardown ends); a redial then reaches the owner, is ``refused`` (no gateway
    to come back to), or is held on ``dial_hold`` (a slow connect) after setting ``dialing``. ``discovered``:
    what owner discovery reports meanwhile."""
    rig = types.SimpleNamespace(owner=None, sockets=[], refused=False, hold=None, held=threading.Event(),
                                dial_hold=None, dialing=threading.Event(), discovered="ready")
    _discovers(monkeypatch, lambda: rig.discovered)

    @asynccontextmanager
    async def dial():
        if rig.owner is None:
            rig.owner = Owner(tmp_path / "state.db", pause=None)
        elif rig.refused:
            raise GatewayClientError("Gateway starting: deadline. Your message was not sent.")
        elif rig.dial_hold is not None:
            rig.dialing.set()
            await asyncio.to_thread(rig.dial_hold.wait, 15)
        first = not rig.sockets
        rig.sockets.append(Socket(rig.owner, close_at=300 if first else None, hold=None if first else rig.hold,
                                  held=rig.held))
        async with GatewayClient(rig.sockets[-1]) as client:
            yield client
        if first and rig.owner.turns:
            await asyncio.to_thread(rig.owner.turns[-1].join, 10)

    rig.connection = GatewayConnection(connect=dial)
    rig.connection.start(timeout=30)
    rig.connection.state("s").adopt_snapshot(rig.connection.call("session.resume", session_id="s"))
    rig.agent, rig.streamed = GatewayAgent(connection=rig.connection, session_id="s"), []
    rig.agent.stream_delta_callback = rig.streamed.append
    yield rig
    for hold in (rig.hold, rig.dial_hold):
        if hold is not None:
            hold.set()
    rig.connection.close()
    rig.owner.finish()


def _turn_in_thread(agent, text):
    out = {}
    worker = threading.Thread(target=lambda: out.setdefault("result", agent.run_conversation(text)), daemon=True)
    worker.start()
    return worker, out


def _admissions(owner):
    from hermes_state_runtime import list_session_admissions
    return list_session_admissions(owner.db, session_id="s", pending_only=False)


@pytest.mark.parametrize("discovered", ["ready", "starting"])
def test_a_turn_whose_socket_the_owner_closed_settles_from_its_receipt(owned, discovered):
    """The owner closed the socket of a CLI stopped past its send deadline (Ctrl-Z, then ``fg``) and
    finished the turn: the CLI said "turn outcome is unknown" and never showed the reply. The redial
    re-attaches and settles the turn from its receipt, painted once, never resent. An owner discovery
    still ``starting`` (a replacement coming up) serves too: the redial waits for it."""
    owned.discovered = discovered
    worker, out = _turn_in_thread(owned.agent, "write a long answer")
    worker.join(30)
    result = out.get("result")
    assert result is not None and not result["failed"], result
    assert result["final_response"] == FINAL
    assert "".join(text for text in owned.streamed if text) == FINAL  # the live prefix, then the rest once
    assert len(owned.sockets) == 2 and not owned.owner.gaps  # a lost socket, not a replay gap
    assert len(_admissions(owned.owner)) == 1


def test_a_lost_socket_whose_redial_fails_keeps_the_unknown_outcome(owned):
    owned.refused = True
    worker, out = _turn_in_thread(owned.agent, "write a long answer")
    worker.join(30)
    result = out.get("result")
    assert result is not None and result["failed"] and result["error"] == DISCONNECTED
    assert result["failure_reason"] == "gateway_connection"
    assert len(_admissions(owned.owner)) == 1


def test_a_stop_during_the_recovery_ends_the_turn_without_a_resend(owned, monkeypatch):
    """Ctrl-C while the redial's re-attach goes unanswered (held 30 s): the turn ends at the settle bound."""
    monkeypatch.setattr("hermes_cli.gateway_agent.INTERRUPT_SETTLE_SECONDS", 0)
    owned.hold = threading.Event()
    worker, out = _turn_in_thread(owned.agent, "write a long answer")
    assert owned.held.wait(15), "the re-attach never started"
    owned.agent.interrupt()
    worker.join(10)
    assert not worker.is_alive(), "the stop waited out the re-attach"
    assert out["result"]["interrupted"] and not out["result"]["failed"]
    assert len(_admissions(owned.owner)) == 1 and len(owned.owner.turns) == 1


def test_ctrl_c_while_the_redial_still_connects_ends_the_turn_at_the_settle_bound(owned, monkeypatch):
    """The redial's connect (a cold start, the WebSocket open, the resubscribe) has not answered: Ctrl-C
    ends the turn at its settle bound, and neither the interrupt nor the end-of-turn reads wait it out."""
    monkeypatch.setattr("hermes_cli.gateway_agent.INTERRUPT_SETTLE_SECONDS", 1.0)
    owned.dial_hold = threading.Event()
    worker, out = _turn_in_thread(owned.agent, "write a long answer")
    assert owned.dialing.wait(20), "the redial never started"
    pressed = time.monotonic()
    owned.agent.interrupt()
    interrupt_took = time.monotonic() - pressed
    worker.join(5)
    assert not worker.is_alive(), "the turn waited out the redial"
    assert interrupt_took < 5, f"the interrupt waited out the redial ({interrupt_took:.1f}s)"
    assert out["result"]["interrupted"] and not out["result"]["failed"]
    assert not owned.dial_hold.is_set() and len(_admissions(owned.owner)) == 1


class _View:
    """One socket to :class:`PromptOwner`."""

    def __init__(self, owner, number):
        self.owner, self.number, self.resumes, self.events = owner, number, 0, asyncio.Queue()

    async def rpc(self, method, *, _timeout=None, **params):
        return self.owner.answer(self, method, params)


class PromptOwner:
    """One owner across sockets. The first is lost after the submit; on the second, the turn's ``kind``
    control is published live right after the redial's own resume, so the turn's re-attach (the second
    resume) lists it too. An answer completes the turn. ``drops`` closes the socket on the next answers
    instead of acknowledging them: ``"lost_ack"`` after the owner took the answer, ``"undelivered"`` before."""

    def __init__(self, kind, drops=()):
        self.kind, self.views, self.answers, self.drops = kind, [], [], list(drops)
        self.prompt = {"kind": kind, "prompt_id": "p1", "execution_generation": 7, "command": "rm x",
                       "description": "d", "question": "which?", "choices": ["once", "deny"]}

    @asynccontextmanager
    async def dial(self):
        self.views.append(_View(self, len(self.views) + 1))
        yield self.views[-1]

    def answer(self, view, method, params):
        if method == "session.resume":
            view.resumes += 1
            running = view.number > 1 and not self.answers
            if view.number == 2 and view.resumes == 1:
                view.events.put_nowait(_frame(f"{self.kind}.request", self.prompt))
            pending = [{"admission_id": "adm-1", "status": "started", "execution_generation": 7}]
            return {"stored_session_id": SID, "subscription_id": f"sub-{view.number}", "messages": [],
                    "pending": pending if running else [],
                    "prompts": [self.prompt] if running and (view.number > 2 or view.resumes == 2) else []}
        if method == "prompt.submit":
            asyncio.get_running_loop().call_soon(view.events.put_nowait, GatewayClientError(DISCONNECTED))
            return {"admission_id": "adm-1", "status": "queued", "execution_generation": 7}
        if method in ("approval.respond", "clarify.respond"):
            drop = self.drops.pop(0) if self.drops else None
            if drop != "undelivered":
                self.answers.append(params["prompt_id"])
            if drop:
                view.events.put_nowait(GatewayClientError(DISCONNECTED))
                raise GatewayClientError(DISCONNECTED)
            view.events.put_nowait(_frame("message.complete", {"text": "done", "outcome": "completed"}))
            return {"status": "resolved", "prompt_id": params["prompt_id"]}
        if method == "prompt.receipt":
            if not self.answers:
                return {"admission_id": "adm-1", "status": "started"}
            return {"admission_id": "adm-1", "status": "terminal", "outcome": "completed",
                    "result": {"final_response": "done", "completed": True}}
        return {}


def _prompted_turn(owner, monkeypatch):
    """One turn against ``owner``; returns ``(result, asked)``, every question its modals showed."""
    from tools.terminal_tool import set_approval_callback
    _discovers(monkeypatch, lambda: "ready")
    asked = []
    connection = GatewayConnection(connect=owner.dial)
    connection.start(5)
    connection.state(SID).adopt_snapshot(connection.call("session.resume", session_id=SID))
    agent = GatewayAgent(connection=connection, session_id=SID,
                         clarify_callback=lambda questions: asked.append(questions) or "a")
    out = {}

    def turn():  # the approval callback is per-thread, as main registers it on the agent thread
        set_approval_callback(lambda command, description, **kw: asked.append(command) or "once")
        out["result"] = agent.run_conversation("q")
    worker = threading.Thread(target=turn, daemon=True)
    worker.start()
    try:
        worker.join(15)
    finally:
        connection.close()
    assert out.get("result") is not None and not out["result"]["failed"], out
    return out["result"], asked


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_a_control_published_between_the_redials_two_resumes_is_asked_once(kind, monkeypatch):
    owner = PromptOwner(kind)
    _result, asked = _prompted_turn(owner, monkeypatch)
    assert len(owner.views) == 2 and len(asked) == 1 and owner.answers == ["p1"]


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_a_control_the_owner_took_is_not_reopened_after_its_ack_was_lost(kind, monkeypatch):
    """The owner took the answer, then the socket closed before its acknowledgement: the request still
    buffered from before the loss must not reopen it, and the next re-attach no longer lists it."""
    owner = PromptOwner(kind, drops=["lost_ack"])
    result, asked = _prompted_turn(owner, monkeypatch)
    assert result["final_response"] == "done"
    assert len(owner.views) == 3 and len(asked) == 1 and owner.answers == ["p1"]


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_a_control_whose_answer_never_arrived_is_asked_again_from_the_next_snapshot(kind, monkeypatch):
    owner = PromptOwner(kind, drops=["undelivered"])
    result, asked = _prompted_turn(owner, monkeypatch)
    assert result["final_response"] == "done"
    assert len(owner.views) == 3 and len(asked) == 2 and owner.answers == ["p1"]


def _resumes(life):
    return [params["session_id"] for method, params in life.calls if method == "session.resume"]


@pytest.fixture
def store(conn):
    from hermes_cli.gateway_session_store import GatewaySessionStore
    db = GatewaySessionStore(conn)
    db.attach(SID)
    return db


def test_ending_a_session_after_a_reconnect_detaches_its_current_subscription(gateway, conn, store):
    """The redial resubscribed under a new id; detaching with the first one is refused gateway-side,
    leaving a closed session attached and its close bookkeeping skipped."""
    gateway.hang_up(conn)
    conn.call("status")
    store.end_session(SID, "new_session")
    assert gateway.lives[1].calls[-1] == ("session.detach", {
        "session_id": SID, "subscription_id": "sub-2", "end_reason": "new_session"})
    # Left by this viewer: a later reconnect does not resume it again.
    gateway.hang_up(conn)
    conn.call("status")
    assert _resumes(gateway.lives[2]) == []


def test_a_deleted_session_is_not_resumed_by_later_reconnects(gateway, conn, store):
    gateway.hang_up(conn)
    conn.call("status")
    store.delete_session(SID)
    gateway.hang_up(conn)
    conn.call("status")
    assert _resumes(gateway.lives[2]) == []


def test_a_session_gone_during_the_restart_is_skipped_and_not_retried(gateway, conn):
    gateway.resume_errors.append(GatewayClientError("not_found"))
    gateway.hang_up(conn)
    assert conn.call("status") == {"status": "ok", "life": 2}
    assert conn.state(SID).subscription_id is None
    gateway.hang_up(conn)
    assert conn.call("status") == {"status": "ok", "life": 3}
    assert _resumes(gateway.lives[2]) == []


@pytest.mark.parametrize("failure", [GatewayClientError("storage_unavailable"), TimeoutError()],
                         ids=["storage_unavailable", "timeout"])
def test_a_transient_resume_failure_fails_the_reconnect_and_the_next_call_retries(gateway, conn, failure):
    """Ready without the subscription, every later prompt.submit is refused (permission_denied) on a
    live socket that never redials: the reconnect must fail instead, so the next call dials again."""
    gateway.resume_errors.append(failure)
    gateway.hang_up(conn)
    with pytest.raises(GatewayClientError):
        conn.call("status")
    assert conn.call("status") == {"status": "ok", "life": 3}
    assert _resumes(gateway.lives[2]) == [SID]
    assert conn.state(SID).subscription_id == "sub-3"


def test_close_after_a_failed_resume_is_still_final(gateway, conn):
    gateway.resume_errors.append(GatewayClientError("storage_unavailable"))
    gateway.hang_up(conn)
    with pytest.raises(GatewayClientError):
        conn.call("status")
    conn.close()
    with pytest.raises(GatewayClientError):
        conn.call("status")
    assert len(gateway.lives) == 2


class _Unanswered:
    """A socket that takes every request and never answers one (a gateway stalled mid-reconnect)."""

    def __init__(self):
        self.sent = asyncio.Event()

    async def send(self, raw):
        self.sent.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await asyncio.Future()


@pytest.mark.asyncio
async def test_a_cancelled_reconnect_cancels_its_session_resume():
    """The viewer closes while a reconnect's ``session.resume`` goes unanswered: cancelling the
    resubscribe cancels the request with it. One left pending on the client held a worker thread
    that process exit joins until the request timed out."""
    from hermes_cli.gateway_client import GatewayClient
    conn = GatewayConnection()
    conn.state(SID).subscription_id = "sub-1"
    socket = _Unanswered()
    async with GatewayClient(socket) as client:
        resubscribe = asyncio.create_task(conn._resubscribe(client))
        await asyncio.wait_for(socket.sent.wait(), 5)
        resubscribe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await resubscribe
        assert client.pending == {}


def _cli(store):
    return types.SimpleNamespace(_session_db=store, _gateway_session_open=True)


@pytest.fixture
def tools(conn, monkeypatch):
    from hermes_cli import gateway_tools
    monkeypatch.setattr(gateway_tools, "_view", None)
    gateway_tools.bind(types.SimpleNamespace(connection=conn, session_id=SID))
    return gateway_tools


def test_a_hung_up_store_stays_open_and_its_next_call_redials(gateway, conn, store):
    """/sessions, /resume, /branch and /handoff gate on the store: a hang-up is not "Hermes can't
    open its session history right now"; the next call dials the restarted gateway."""
    gateway.hang_up(conn)
    assert store
    assert store.call("status") == {"status": "ok", "life": 2}
    assert store


def test_bg_and_btw_after_a_hang_up_run_on_the_gateway_not_in_this_process(gateway, conn, store):
    """None here sends /bg down the in-process AIAgent path (a local ``bg_HHMMSS_xxxxxx`` task)."""
    from hermes_cli import gateway_side
    gateway.hang_up(conn)
    assert gateway_side.connection_for(_cli(store)) is conn
    task_id, _wait = gateway_side.start(conn, SID, "background", "later")
    assert task_id == "task-2" and gateway.lives[1].calls[-1][0] == "prompt.background"


def test_a_side_answer_lost_to_a_restart_fails_instead_of_waiting_forever(gateway, conn):
    """The task died with the old gateway; another thread's redial must not make the wait endless."""
    from hermes_cli import gateway_side
    _task, wait = gateway_side.start(conn, SID, "btw", "q")
    gateway.hang_up(conn)
    conn.call("status")
    outcome = []
    waiter = threading.Thread(target=lambda: outcome.append(pytest.raises(RuntimeError, wait)), daemon=True)
    waiter.start()
    waiter.join(10)
    assert not waiter.is_alive() and "outcome is unknown" in str(outcome[0].value)


def test_a_redial_between_admission_and_the_waiter_still_ends_the_wait(gateway, conn, monkeypatch):
    """Another caller redials after the old gateway admitted the task but before its waiter exists:
    the wait belongs to the admitting life, not to whatever epoch is current by then."""
    from hermes_cli import gateway_side
    register = conn.side_waiter

    def redial_first(task_id):
        gateway.hang_up(conn)
        conn.call("status")
        return register(task_id)

    monkeypatch.setattr(conn, "side_waiter", redial_first)
    task, wait = gateway_side.start(conn, SID, "btw", "q")
    assert task == "task-1" and conn.epoch == 2
    outcome = []
    waiter = threading.Thread(target=lambda: outcome.append(pytest.raises(RuntimeError, wait)), daemon=True)
    waiter.start()
    waiter.join(10)
    assert not waiter.is_alive() and "outcome is unknown" in str(outcome[0].value)


def test_the_dock_and_stop_after_a_hang_up_redial(gateway, conn, tools):
    gateway.hang_up(conn)
    view = tools.process_view()
    assert view is not None and tools.process_registry() is view
    view.kill_all()
    assert [method for method, _ in gateway.lives[1].calls][:2] == ["session.resume", "process.stop"]


def test_shared_keeps_a_hung_up_connection_instead_of_opening_a_second(gateway, conn, monkeypatch):
    monkeypatch.setattr(GatewayConnection, "_shared", conn)
    gateway.hang_up(conn)
    assert GatewayConnection.shared() is conn


def test_new_after_a_hang_up_redials_and_detaches_with_the_end_reason(gateway, conn, store):
    """Dropping the subscription without ``session.detach`` leaves the owner never ending the row."""
    gateway.hang_up(conn)
    store.end_session(SID, "new_session")
    assert gateway.lives[1].calls == [("session.resume", {"session_id": SID}), ("session.detach", {
        "session_id": SID, "subscription_id": "sub-2", "end_reason": "new_session"})]
    gateway.hang_up(conn)
    conn.call("status")
    assert _resumes(gateway.lives[2]) == []


def test_after_close_every_reader_reports_the_connection_gone(gateway, conn, store, tools, monkeypatch):
    from hermes_cli import gateway_side
    monkeypatch.setattr(GatewayConnection, "_shared", conn)
    gateway.hang_up(conn)
    conn.close()
    assert not store
    assert gateway_side.connection_for(_cli(store)) is None
    assert tools.process_view() is None
    assert GatewayConnection.shared() is not conn
    store.end_session(SID, "new_session")
    with pytest.raises(GatewayClientError, match="disconnected") as refused:
        conn.call("status")
    assert "outcome is unknown" not in str(refused.value)  # nothing was sent
    assert len(gateway.lives) == 1


def _gateway_cli(store):
    c = _cli(store)
    c.session_id = SID
    return c


def test_observing_a_local_command_never_starts_a_stopped_gateway(gateway, conn, store):
    """``gateway stop``, then a local command: ``command.observe`` redialed, and the dial started a gateway
    (``connect_gateway``) and waited for it. Undelivered, the observation is skipped as before; a prompt
    after it still starts one, as on main."""
    from hermes_cli import gateway_plugin_commands
    c = _gateway_cli(store)
    assert gateway_plugin_commands.observe(c, "quit", "") is True
    assert gateway.lives[0].calls[-1] == ("command.observe", {"session_id": SID, "name": "quit", "arg": ""})
    gateway.stop(conn)
    gateway.dialing.clear()
    assert gateway_plugin_commands.observe(c, "quit", "") is True
    assert not gateway.dialing.is_set() and len(gateway.lives) == 1
    assert conn.call("status") == {"status": "ok", "life": 2} and gateway.started == [2]


def test_the_first_local_command_after_a_restart_reaches_the_new_gateways_observers(gateway, conn, store):
    from hermes_cli import gateway_plugin_commands
    c = _gateway_cli(store)
    gateway.hang_up(conn)  # ``gateway restart``: a new gateway serves the home
    assert gateway_plugin_commands.observe(c, "quit", "") is True
    assert gateway.lives[1].calls[-1] == ("command.observe", {"session_id": SID, "name": "quit", "arg": ""})
    assert gateway.started == []


def test_completion_never_redials_a_hung_up_gateway(gateway, conn, store, monkeypatch):
    """The prompt's completer reads the session's skills and plugin commands from the gateway; typing
    while it is down must not dial (and start) one, nor look for one on every key."""
    import cli as cli_mod
    from prompt_toolkit.document import Document
    from hermes_cli.commands_completion import cli_slash_completer
    monkeypatch.setattr(cli_mod, "get_skill_bundles", dict)
    c = _gateway_cli(store)
    c._command_available = lambda _cmd: True
    completer = cli_slash_completer(c)
    list(completer.get_completions(Document("/"), None))
    assert [method for method, _ in gateway.lives[0].calls].count("commands.catalog") == 1
    gateway.stop(conn)
    gateway.dialing.clear()
    assert "/help" in {item.display_text for item in completer.get_completions(Document("/"), None)}
    assert not gateway.dialing.is_set() and len(gateway.lives) == 1 and gateway.probes == 0


def test_completion_never_dials_while_a_redial_is_in_progress(gateway, conn, store, monkeypatch):
    """A prompt is redialing (no socket yet, ``closed`` already False): completion that misses its cache
    (here the session's first read) lists none instead of waiting on that dial and reading from it."""
    import cli as cli_mod
    from prompt_toolkit.document import Document
    from hermes_cli.commands_completion import cli_slash_completer
    monkeypatch.setattr(cli_mod, "get_skill_bundles", dict)
    c = _gateway_cli(store)
    c._command_available = lambda _cmd: True
    gateway.stop(conn)
    gateway.dialing.clear()
    gateway.hold = threading.Event()
    prompt = threading.Thread(target=lambda: conn.call("status"), daemon=True)
    prompt.start()
    try:
        assert gateway.dialing.wait(5)
        assert "/help" in {item.display_text for item in cli_slash_completer(c).get_completions(Document("/"), None)}
    finally:
        gateway.hold.set()
    prompt.join(10)
    assert all(m != "commands.catalog" for life in gateway.lives for m, _ in life.calls)


def test_gateway_serving_is_discovery_only(monkeypatch):
    """The check before the exit or an observation redials: an owner discovery finds, or an explicit
    gateway URL; an unknown answer is no gateway (nothing is dialed)."""
    from hermes_cli import gateway_connection, gateway_runtime
    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    assert gateway_connection.gateway_serving() is False  # this test's home has no gateway
    for found, serving in ((gateway_runtime.GatewayDiscovery("ready", endpoint=object()), True),
                           (gateway_runtime.GatewayDiscovery("starting"), True),  # a replacement still starting
                           (gateway_runtime.GatewayDiscovery("absent"), False),
                           (gateway_runtime.GatewayDiscovery("inaccessible", reason_code="control_timeout"), False)):
        monkeypatch.setattr(gateway_runtime, "discover_gateway_endpoint", lambda _home, timeout, _f=found: _f)
        assert gateway_connection.gateway_serving() is serving, found

    def unknown(_home, timeout):
        raise OSError("control pipe")
    monkeypatch.setattr(gateway_runtime, "discover_gateway_endpoint", unknown)
    assert gateway_connection.gateway_serving() is False
    monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", "ws://127.0.0.1:18950/ws")
    assert gateway_connection.gateway_serving() is True


def _quit(store):
    """``/quit`` in the open CLI on ``store``'s session, before any turn (the exit prunes the empty row);
    returns the seconds from the command to the end of the exit's teardown."""
    import time as clock
    from unittest.mock import patch
    import cli as cli_mod
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    writer = SessionDB(db_path=get_hermes_home() / "state.db")
    writer.create_session(SID, source="local")
    writer.close()
    c = cli_mod.HermesCLI()
    c.agent, c._session_db, c._gateway_session_open, c.session_id = None, store, True, SID
    started = clock.monotonic()
    assert c.process_command("/quit") is False
    with patch.object(cli_mod, "_run_cleanup"), patch.object(c, "_print_exit_summary"), \
            patch.object(c, "_release_active_session"):
        c._tui_shutdown()
    return clock.monotonic() - started


def test_quit_after_gateway_stop_exits_without_starting_one(gateway, conn, store):
    """``gateway stop``, then ``/quit`` in the open CLI: the observation, the exit's ``session.detach`` and
    its empty-row prune each redialed, starting a new gateway the user had just stopped and waiting for
    it. Nothing on the way out dials now; the row stays as the stopped gateway left it."""
    gateway.stop(conn)
    gateway.dialing.clear()
    assert _quit(store) < 3
    assert not gateway.dialing.is_set() and len(gateway.lives) == 1 and gateway.started == []


def test_quit_after_gateway_restart_ends_and_prunes_the_session_on_the_new_gateway(gateway, conn, store):
    """``gateway restart`` while the CLI idles, then ``/quit``: the gateway now serving the home gets the
    exit's detach (it ends the row with ``cli_close``) and deletes the empty session, as on main."""
    gateway.hang_up(conn)
    _quit(store)
    calls = gateway.lives[1].calls
    assert ("session.detach", {"session_id": SID, "subscription_id": "sub-2", "end_reason": "cli_close"}) in calls
    assert [p["operation"] for m, p in calls if m == "session.mutate"] == ["delete"]
    assert len(gateway.lives) == 2 and gateway.started == []


def test_quit_while_the_restarted_gateway_is_still_starting_waits_for_it_and_ends_the_session(
        gateway, conn, store):
    """``gateway restart``, and ``/quit`` before the replacement is ready: discovery says ``starting``, not
    "no gateway". The exit takes main's redial, which waits for that owner, then detaches with
    ``cli_close`` and deletes the empty session there; skipping it left the row open (ended_at NULL)."""
    gateway.hang_up(conn)
    gateway.starting = True
    _quit(store)
    calls = gateway.lives[1].calls
    assert ("session.detach", {"session_id": SID, "subscription_id": "sub-2", "end_reason": "cli_close"}) in calls
    assert [p["operation"] for m, p in calls if m == "session.mutate"] == ["delete"]
    assert len(gateway.lives) == 2 and gateway.started == []


class _Waiters(threading.Event):
    """``_ready`` that opens ``release`` once ``count`` callers are waiting on it."""

    def __init__(self, release, count):
        super().__init__()
        self.release, self.count = release, count

    def wait(self, timeout=None):
        self.count -= 1
        if self.count <= 0:
            self.release.set()
        return super().wait(timeout)


def test_a_call_racing_a_redial_waits_for_it_and_is_sent(gateway, conn):
    """The second caller saw the dialing thread alive and no client yet: "turn outcome is unknown, do
    not resend" for a request that was never sent."""
    gateway.hang_up(conn)
    gateway.hold, gateway.dialing = threading.Event(), threading.Event()
    conn._ready = _Waiters(gateway.hold, 2)
    results = []
    first = threading.Thread(target=lambda: results.append(conn.call("status")), daemon=True)
    first.start()
    assert gateway.dialing.wait(5)
    try:
        second = conn.call("status")
    finally:
        gateway.hold.set()
    first.join(10)
    assert second == {"status": "ok", "life": 2} and results == [second]
    assert len(gateway.lives) == 2


def test_lost_before():
    error = GatewayClientError("Gateway disconnected")
    assert not lost_before(error, 2)  # untagged: a live loss
    error.connection_epoch = 1
    assert lost_before(error, 2) and not lost_before(error, 1) and not lost_before(error, None)
    assert not lost_before({"connection_epoch": 1}, 2)


def test_chat_q_says_why_a_turn_was_interrupted(capsys):
    """A restart drain settles a running `chat -q` turn interrupted with no reply text: exit 130
    as before, but no longer silently."""
    from hermes_cli.gateway_chat_view import GatewayChatView

    class Client:
        def __init__(self):
            self.events = asyncio.Queue()

        async def rpc(self, method, **params):
            if method == "prompt.submit":
                asyncio.get_running_loop().call_soon(self.events.put_nowait, _frame(
                    "message.complete", {"text": "", "outcome": "cancelled", "status": "interrupted"}))
                return {"admission_id": "adm-1", "status": "queued", "execution_generation": 7}
            if method == "prompt.receipt":
                return {"result": {"final_response": "", "interrupted": True,
                                   "interrupt_message": "Gateway shutting down"}}
            return {}

    code = asyncio.run(GatewayChatView(Client(), {"stored_session_id": SID}).run("hi", oneshot=True))
    assert code == 130
    assert "Turn interrupted (Gateway shutting down); it was not retried." in capsys.readouterr().err
