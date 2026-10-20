"""The classic CLI (``hermes``, ``hermes chat`` -> GatewayAgent) on a session paused by a turn lost in an
owner restart (#134889 GW-10, #139532).

The restarted owner fences the lost turn ``unknown`` and holds the session's FIFO until a person discards
it; the next prompt is saved behind it. The classic CLI used to wait for that prompt's completion forever,
spinner running, with nothing on screen. Now it says why and how to go on, ``/discard`` settles the lost
turn through the owner (never resent), and the saved prompt then runs once, painted as a typed turn.

Real owners (a second ``initialize_session_authority`` on the same state.db is the restart), real
``AuthorityConnection.dispatch`` over a socket the owner publishes to, real ``GatewayConnection``,
``GatewaySessionStore`` and ``GatewayAgent``. The owner's turn runner answers in place.
"""
import asyncio
import queue
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli.gateway_client import GatewayClientError, rpc_error
from hermes_cli.gateway_connection import DISCONNECTED, GatewayConnection
from hermes_cli.gateway_session_store import GatewaySessionStore
from tests.waiting import async_wait_until


class _Life:
    """One socket to the owner that was current when the CLI dialled."""

    def __init__(self, owners, reader):
        from gateway.session_controls import AuthorityConnection
        self.owners, self.reader, self.events = owners, reader, asyncio.Queue()
        self.connection = AuthorityConnection(owners.current, self, {'user_id': 'owner'})

    def write(self, frame):  # the owner publishes to this viewer
        self.reader.call_soon_threadsafe(self.events.put_nowait, frame)
        return True

    def hang_up(self):  # the gateway process died mid-turn (taskkill /F, SIGKILL)
        self.reader.call_soon_threadsafe(self.events.put_nowait, GatewayClientError(DISCONNECTED))

    async def rpc(self, method, *, _timeout=None, **params):
        frame = {'id': 1, 'method': method, 'params': params}
        reply = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
            self.connection.dispatch(frame), self.owners.loop))
        if 'error' in reply:
            raise rpc_error(reply['error'])
        return reply['result']


class _Owners:
    async def start(self, tmp_path, monkeypatch, *, hold_first=True):
        from gateway import run
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        from gateway.session_controls import AuthorityConnection
        from gateway.session_local import create_local_session
        monkeypatch.setattr(run, '_load_gateway_config', lambda *a, **k: {'platform_toolsets': {'cli': []}})
        self.profile, self.loop, self.lives, self.executed = str(tmp_path), asyncio.get_running_loop(), [], []
        self.store = SessionStore(tmp_path / 'sessions', GatewayConfig())
        self.first = await self.boot('first')
        if hold_first:
            self.first._schedule = lambda ref: None  # its turn is the one the restart loses
        actor = AuthorityConnection(self.first, object(), {'user_id': 'owner'}).actor
        self.sid = create_local_session(self.first, actor, {'cwd': self.profile, 'model': 'm', 'toolsets': [],
                                                            'request_id': 'gw10'}).session_id
        return self

    async def _answer(self, event):
        from gateway.session_results import execution_result
        self.executed.append(event.text)
        execution_result.get()['result'] = {'final_response': 'ACK_' + event.text, 'completed': True, 'messages': []}
        return 'ACK_' + event.text

    async def boot(self, instance_id):
        from gateway.session_authority import initialize_session_authority
        owner = SimpleNamespace(session_store=self.store, _session_db=self.store._db, adapters={}, _draining=False,
                                _cached_agent_for=lambda route: None, _evict_cached_agent=lambda route: None,
                                _handle_message=self._answer)
        owner._adapter_for_source = lambda source: None
        self.current = await initialize_session_authority(owner, profile_id=self.profile, instance_id=instance_id)
        return self.current

    @asynccontextmanager
    async def dial(self):
        reader = asyncio.get_running_loop()

        async def open_life():
            self.lives.append(_Life(self, reader))
            return self.lives[-1]
        yield await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(open_life(), self.loop))

    def statuses(self, text):
        from hermes_state_runtime import list_session_admissions
        return [row['status'] for row in list_session_admissions(self.store._db, session_id=self.sid, pending_only=False)
                if row['payload'].get('text') == text]

    async def close(self, conn):
        await asyncio.to_thread(conn.close)
        for life in self.lives:
            await life.connection.close()
        self.store._db.close()


def _idle_cli(store, sid):
    from cli import HermesCLI
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = sid
    cli._pending_input = queue.Queue()
    cli._pending_resume_sessions = None
    cli._session_db = store
    cli._agent_running = False
    cli.config = {}
    return cli


def _command(cli, text):
    with patch("cli._cprint") as printed:
        assert cli.process_command(text) is True
    return " ".join(str(call.args[0]) for call in printed.call_args_list)


async def _turn(agent, text, timeout=10):
    """``run_conversation`` on its own thread, as main's agent thread runs it; None while it still waits."""
    worker = asyncio.ensure_future(asyncio.to_thread(agent.run_conversation, text))
    done, _ = await asyncio.wait({worker}, timeout=timeout)
    return worker.result() if done else None


async def _attached(owners):
    from hermes_cli.gateway_agent import GatewayAgent
    conn = GatewayConnection(connect=owners.dial)
    store = GatewaySessionStore(conn)
    await asyncio.to_thread(store.attach, owners.sid)
    # Off this loop: its constructor sends config.set, which the owners on this loop answer.
    agent = await asyncio.to_thread(GatewayAgent, connection=conn, session_id=owners.sid)
    printed = []
    agent._print_fn = printed.append
    return conn, store, agent, printed


async def _lose_a_turn(owners, agent):
    """A classic CLI turn runs; the gateway restarts mid-turn and the new owner fences it ``unknown``."""
    lost = asyncio.ensure_future(_turn(agent, "LOST"))
    await async_wait_until(lambda: owners.statuses("LOST") == ['queued'], what="the first turn admitted")
    from hermes_state_runtime import claim_session_input
    claim_session_input(owners.store._db, epoch=owners.first.epoch, session_id=owners.sid)  # it runs
    await owners.boot('restarted')
    for life in owners.lives:  # the gateway process died: every socket to it drops
        life.hang_up()
    assert owners.statuses("LOST") == ['unknown']
    first = await lost
    assert first is not None and first["failed"] and "outcome is unknown" in first["error"], first


@pytest.mark.asyncio
async def test_a_prompt_behind_a_restart_lost_turn_says_why_and_discard_runs_it_once(tmp_path, monkeypatch):
    owners = await _Owners().start(tmp_path, monkeypatch)
    conn, store, agent, printed = await _attached(owners)
    try:
        await _lose_a_turn(owners, agent)

        second = await _turn(agent, "NEXT")
        assert second is not None, "the next prompt waits forever behind the lost turn, with no word why"
        assert not second["failed"] and second["final_response"] == "", second
        notice = " ".join(printed)
        assert "restarted" in notice and "/discard" in notice and "/new" in notice, printed
        assert owners.statuses("NEXT") == ['queued']  # saved behind the lost turn, not dropped

        cli = _idle_cli(store, owners.sid)
        refused = await asyncio.to_thread(_command, cli, "/discard not-an-admission")
        assert "no unknown" in refused.lower() and owners.statuses("LOST") == ['unknown'], refused
        reply = await asyncio.to_thread(_command, cli, "/discard")
        assert "1" in reply and "discard" in reply.lower(), reply
        await async_wait_until(lambda: owners.executed == ['NEXT'], what="the saved prompt ran")
        assert owners.statuses("LOST") == ['terminal']  # settled through the owner, never resent

        def adopted():
            cli._drain_process_notifications("cli-idle")
            return not cli._pending_input.empty()
        await async_wait_until(adopted, what="the idle CLI adopts the turn the owner ran for the saved prompt")
        item = cli._pending_input.get_nowait()
        assert str(item) == "NEXT" and type(item).__name__ == "_SeededQueryMessage"
        cli._tui_unwrap_input(item)
        painted = await _turn(agent, "NEXT")
        assert painted is not None and painted["final_response"] == "ACK_NEXT", painted
        assert owners.executed == ['NEXT'] and owners.statuses("NEXT") == ['terminal']  # once, never resubmitted
    finally:
        agent._turn = None  # a turn still waiting stops reading, so teardown never hangs on it
        await owners.close(conn)


@pytest.mark.asyncio
async def test_a_one_shot_refused_before_its_submit_leaves_nothing_and_a_retry_runs_once(tmp_path, monkeypatch):
    """``-q`` / ``-Q`` with the loss already listed: nothing is staged or sent, exit 3 ("nothing was
    submitted"). Acknowledging the lost turn then retrying, as the copy says, runs the prompt once."""
    from cli import _single_query_exit_code
    owners = await _Owners().start(tmp_path, monkeypatch)
    conn, store, agent, _printed = await _attached(owners)
    try:
        await _lose_a_turn(owners, agent)
        await asyncio.to_thread(conn.start)  # redialled: the restarted owner's resume lists the loss
        agent._finite = True
        refused = await _turn(agent, "DEPLOY")
        assert refused is not None and "nothing was submitted" in refused["error"], refused
        assert _single_query_exit_code(refused) == 3 and owners.statuses("DEPLOY") == []
        await asyncio.to_thread(_command, _idle_cli(store, owners.sid), "/discard")
        retried = await _turn(agent, "DEPLOY")
        assert retried is not None and retried["final_response"] == "ACK_DEPLOY", retried
        assert owners.executed == ['DEPLOY'] and owners.statuses("LOST") == ['terminal']
    finally:
        agent._turn = None
        await owners.close(conn)


@pytest.mark.asyncio
@pytest.mark.parametrize("last_viewer", [False, True], ids=["another-viewer-attached", "last-viewer"])
async def test_a_one_shot_held_after_its_submit_exits_3_and_its_prompt_runs_once(tmp_path, monkeypatch,
                                                                                 last_viewer):
    """The loss shows only after the one-shot's submit (its redial): exit 3, the input is retained and
    queued once, and the one-shot sends no ``prompt.cancel`` or ``session.interrupt`` of its own. Recovered
    without resending, it runs once: after a ``/discard`` from the viewer still attached, or when this
    last viewer's close lets the owner settle the lost turn."""
    from cli import _single_query_exit_code
    owners = await _Owners().start(tmp_path, monkeypatch)
    conn, store, agent, _printed = await _attached(owners)
    other = None if last_viewer else GatewayConnection(connect=owners.dial)
    try:
        if other is not None:
            await asyncio.to_thread(GatewaySessionStore(other).attach, owners.sid)
        await _lose_a_turn(owners, agent)
        if other is not None:
            await asyncio.to_thread(other.start)  # that viewer is back on the restarted owner
        sent, call = [], conn.call
        monkeypatch.setattr(conn, "call", lambda method, **kw: (sent.append(method), call(method, **kw))[1])
        agent._finite = True
        held = await _turn(agent, "DEPLOY")
        assert held is not None and "retained" in held["error"] and "do not resend" in held["error"], held
        assert _single_query_exit_code(held) == 3 and owners.statuses("DEPLOY") == ['queued']
        assert not {"prompt.cancel", "session.interrupt"} & set(sent), sent
        await asyncio.to_thread(store.end_session, owners.sid, "cli_close")  # the one-shot exits
        if other is not None:
            assert owners.statuses("DEPLOY") == ['queued'] and owners.executed == []
            await asyncio.to_thread(_command, _idle_cli(GatewaySessionStore(other), owners.sid), "/discard")
        # Settlement commits just after the run is recorded: wait for both.
        await async_wait_until(lambda: owners.executed == ['DEPLOY'] and owners.statuses("DEPLOY") == ['terminal'],
                               what="the retained prompt ran and settled")
        assert owners.executed == ['DEPLOY'] and owners.statuses("LOST") == ['terminal']
    finally:
        agent._turn = None
        if other is not None:
            await asyncio.to_thread(other.close)
        await owners.close(conn)


@pytest.mark.asyncio
async def test_a_healthy_session_runs_its_prompt_and_discard_says_nothing_was_lost(tmp_path, monkeypatch):
    owners = await _Owners().start(tmp_path, monkeypatch, hold_first=False)
    conn, store, agent, printed = await _attached(owners)
    try:
        result = await _turn(agent, "HELLO")
        assert result is not None and result["final_response"] == "ACK_HELLO", result
        assert not any("/discard" in line for line in printed), printed
        reply = await asyncio.to_thread(_command, _idle_cli(store, owners.sid), "/discard")
        assert "nothing to discard" in reply.lower(), reply
        assert owners.executed == ['HELLO']
    finally:
        agent._turn = None
        await owners.close(conn)


def _protocol_agent(monkeypatch, frames, *, finite=False):
    """A GatewayAgent on the protocol fake whose ``prompt.submit`` answers only once the owner's frames for
    it (and the pending list they publish) are already queued: the agent thread then reads them in order."""
    from hermes_cli.gateway_agent import GatewayAgent
    from tests.hermes_cli.test_gateway_agent import SID, FakeClient, connect_with
    client = FakeClient([])
    conn = GatewayConnection(connect=connect_with(client))
    conn.start(5)
    original = client.rpc

    async def rpc(method, **params):
        if method != "prompt.submit":
            return await original(method, **params)
        for item in frames:
            await client.events.put(item)
        await async_wait_until(lambda: conn.state(SID).pending, what="the published pending list")
        return {"admission_id": "adm-1", "status": "queued", "execution_generation": 7}
    monkeypatch.setattr(client, "rpc", rpc)
    conn.state(SID)
    agent = GatewayAgent(connection=conn, session_id=SID)
    agent._finite, printed = finite, []
    agent._print_fn = printed.append
    return agent, conn, client, printed


@pytest.mark.parametrize("finite", [False, True], ids=["interactive", "one-shot"])
def test_a_turn_that_ran_is_reported_as_it_settled_when_a_later_turn_is_lost(monkeypatch, finite):
    """This prompt ran and settled; another viewer's next turn was lost (``unknown``) before the agent
    thread read the buffered completion. Only a prompt still ``queued`` is held: this one is reported as it
    settled, never as saved behind the loss."""
    from tests.hermes_cli.test_gateway_agent import frame
    lost_next = frame("session.info", {"pending": [{"admission_id": "adm-B", "status": "unknown", "sequence": 2,
                                                    "execution_generation": 8, "text": "B"}]}, admission=None)
    agent, conn, client, printed = _protocol_agent(monkeypatch, [
        frame("message.delta", {"text": "done"}), frame("message.complete", {"text": "done", "status": "complete"}),
        lost_next], finite=finite)
    try:
        result = agent.run_conversation("A")
        assert not result["failed"] and result.get("completed") is True and result["final_response"] == "done", result
        assert not result.get("detached") and not printed and not conn.state(agent.session_id).queued_inputs
        assert "prompt.cancel" not in [method for method, _ in client.calls]
    finally:
        conn.close()


def test_a_refused_discard_says_what_was_refused(monkeypatch):
    """A lost turn the owner refuses to settle leaves the session paused: ``/discard`` says so, never
    "Nothing to discard"."""
    from hermes_cli.gateway_client import rpc_error
    from tests.hermes_cli.test_gateway_agent import SID, FakeClient, connect_with
    client = FakeClient([])
    conn = GatewayConnection(connect=connect_with(client))
    conn.start(5)
    original = client.rpc

    async def rpc(method, **params):
        if method == "prompt.resolve_unknown":
            raise rpc_error({"code": 4009, "message": "stale_generation"})
        return await original(method, **params)
    monkeypatch.setattr(client, "rpc", rpc)
    conn.state(SID).pending = [{"admission_id": "adm-0", "status": "unknown", "execution_generation": 3}]
    try:
        reply = _command(_idle_cli(GatewaySessionStore(conn), SID), "/discard")
        assert "stale_generation" in reply and "adm-0" in reply and "nothing to discard" not in reply.lower(), reply
    finally:
        conn.close()


def test_help_lists_discard_but_no_messaging_command_the_cli_cannot_run():
    """``/discard`` is a messaging command the classic CLI also handles, so its ``/help`` lists it; a
    messaging-only command it has no handler for (``/approve``) stays out."""
    from cli import HermesCLI
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id, cli.config = None, {}
    with patch("cli._cprint") as printed:
        cli.show_help("")
    shown = " ".join(str(call.args[0]) for call in printed.call_args_list)
    assert "/discard" in shown and "/approve" not in shown
