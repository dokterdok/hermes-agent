"""A client's own event backlog never fails its turn (#134889).

The frames a socket already buffered (a CLI resumed from Ctrl-Z) are read in one burst that never
yields to the loop, so the 4,096-frame client queue filled before its consumer had a turn: the
reader reported the turn's outcome as unknown, and the next prompt failed while the socket closed.
A Kanban worker, subscribed by ``kanban.run`` but reading only receipts, filled it the same way.
"""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from hermes_cli import gateway_client
from hermes_cli.gateway_client import GatewayClient, GatewayClientError
from hermes_cli.gateway_connection import GatewayConnection

SID = "local-backlog"


def _frame(kind, text):
    return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
        "type": kind, "session_id": SID, "payload": {"text": text}}})


class Backlog:
    """A socket whose frames are all buffered already: iterating them never yields to the loop."""

    def __init__(self, frames):
        self.frames = frames

    async def __aiter__(self):
        for raw in self.frames:
            yield raw
        await asyncio.Event().wait()  # the socket stays open


def test_backlog_larger_than_the_client_queue_reaches_the_cli_intact():
    deltas = [str(i) for i in range(6000)]
    frames = [_frame("message.delta", text) for text in deltas] + [_frame("message.complete", "done")]

    @asynccontextmanager
    async def connect():
        async with GatewayClient(Backlog(frames)) as client:
            yield client

    conn = GatewayConnection(connect=connect)
    conn.start(timeout=5)
    try:
        seen = []
        while not seen or not (isinstance(seen[-1], Exception) or seen[-1]["params"]["type"] == "message.complete"):
            seen.append(conn.events.get(timeout=5))
    finally:
        conn.close()
    assert not isinstance(seen[-1], Exception), seen[-1]
    assert [frame["params"]["payload"]["text"] for frame in seen[:-1]] == deltas


@pytest.mark.asyncio
async def test_a_consumer_that_stops_draining_is_still_failed_with_the_queue_bounded(monkeypatch):
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 0.05, raising=False)
    client = GatewayClient(Backlog([_frame("message.delta", str(i)) for i in range(6000)]))
    pending = asyncio.get_running_loop().create_future()
    client.pending[1] = pending
    await asyncio.wait_for(client._read(), 5)
    with pytest.raises(GatewayClientError, match="outcome is unknown"):
        await pending
    assert client.events.full()
    queued = [client.events.get_nowait() for _ in range(client.events.maxsize)]
    assert isinstance(queued[-1], GatewayClientError)


class CardOwner:
    """A Kanban card's owner as the worker's socket sees it: ``kanban.run`` subscribes the socket,
    the card's turn streams ``events`` frames, and the next receipt poll finds it terminal."""

    def __init__(self, events):
        self.events = events
        self.inbox = asyncio.Queue()

    async def send(self, raw):
        request = json.loads(raw)
        if request["method"] == "kanban.run":
            result = {"session_id": SID, "receipt": {"status": "running", "admission_id": "a1"}}
            frames = [{"jsonrpc": "2.0", "id": request["id"], "result": result}]
            frames += [json.loads(_frame("message.delta", str(i))) for i in range(self.events)]
        else:
            frames = [{"jsonrpc": "2.0", "id": request["id"], "result": {"status": "terminal", "admission_id": "a1"}}]
        for frame in frames:
            self.inbox.put_nowait(json.dumps(frame))

    async def __aiter__(self):
        while True:
            yield await self.inbox.get()


@pytest.mark.asyncio
async def test_kanban_worker_settles_a_card_whose_turn_outstreams_the_client_queue(monkeypatch, tmp_path):
    from gateway import session_kanban
    from hermes_cli import kanban_worker_client

    @asynccontextmanager
    async def connect():
        async with GatewayClient(CardOwner(6000)) as client:
            yield client

    monkeypatch.setattr(gateway_client, "connect_gateway", connect)
    monkeypatch.setattr(session_kanban, "worker_result", lambda *_: {"exit_code": 0, "last_output": "card closed"})
    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    assert await asyncio.wait_for(kanban_worker_client.run({"task_id": "t1"}, str(tmp_path / "kanban.db")), 5) == 0


@pytest.mark.asyncio
async def test_closing_as_a_waiting_frame_lands_still_ends_the_reader():
    """A close that lands just as the consumer makes room still ends the reader, so ``__aexit__``
    returns instead of waiting on the open socket forever."""
    client = await GatewayClient(Backlog([_frame("message.delta", str(i)) for i in range(4097)])).__aenter__()
    while not client.events.full():  # the reader yields only once it waits on the last frame
        await asyncio.sleep(0)
    client.events.get_nowait()
    closing = asyncio.create_task(client.__aexit__(None, None, None))
    try:
        await asyncio.wait_for(asyncio.shield(closing), 2)
    finally:
        client.reader.cancel()
        await asyncio.gather(closing, return_exceptions=True)


def _event(kind, payload, seq=1):
    return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
        "type": kind, "session_id": SID, "admission_id": "a", "payload": payload, "replay_epoch": "e", "seq": seq}})


class ResumingOwner:
    """A submit whose subscription the owner then retires (``session.replay_gap``). On ``session.resume``
    the re-attached subscription streams ``burst`` frames of the running turn before the answer; the
    turn's completion follows the answer."""

    def __init__(self, burst):
        self.burst, self.inbox = burst, asyncio.Queue()

    async def send(self, raw):
        request = json.loads(raw)
        method, result, after = request["method"], {}, []
        if method == "prompt.submit":
            result, after = {"admission_id": "a", "status": "queued"}, [_event("session.replay_gap", {})]
        elif method == "session.resume":
            for seq in range(1, self.burst + 1):
                self.inbox.put_nowait(_event("message.delta", {"text": f"{seq}|"}, seq))
            result = {"stored_session_id": SID, "replay_epoch": "e", "last_sequence": 0, "prompts": [],
                      "pending": [{"admission_id": "a", "status": "started"}]}
            after = [_event("message.complete", {"text": "done", "outcome": "completed"}, self.burst + 1)]
        elif method == "prompt.receipt":
            result = {"admission_id": "a", "status": "terminal", "outcome": "completed",
                      "result": {"final_response": "done", "completed": True}}
        self.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))
        for frame in after:
            self.inbox.put_nowait(frame)

    async def __aiter__(self):
        while True:
            yield await self.inbox.get()


@pytest.mark.asyncio
async def test_a_reattach_answer_behind_a_full_queue_still_settles_the_oneshot(monkeypatch, capsys):
    """More frames ahead of the ``session.resume`` answer than the queue holds: the view awaits that
    answer from its only consumer, so it keeps taking frames instead of parking the reader short of it."""
    from hermes_cli.gateway_chat_view import GatewayChatView
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 2)
    async with GatewayClient(ResumingOwner(5000)) as client:
        view = GatewayChatView(client, {"stored_session_id": SID}, quiet=True)
        assert await asyncio.wait_for(view.run("q", oneshot=True), 20) == 0
    assert capsys.readouterr().out.count("done") == 1


@pytest.mark.asyncio
async def test_acp_pump_reattaches_behind_a_full_queue_and_the_prompt_ends(monkeypatch):
    """ACP multiplexes every editor session on one socket, and its pump is the one consumer."""
    from unittest.mock import AsyncMock
    import acp
    from acp_adapter.gateway_server import GatewayACPAgent
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 2)
    async with GatewayClient(ResumingOwner(5000)) as client:
        agent = GatewayACPAgent()
        agent._snapshots[SID] = {"replay_epoch": "e0", "last_sequence": 0, "pending": [], "prompts": []}
        agent._gateway, agent._conn = client, AsyncMock()
        pump = agent._event_task = asyncio.create_task(agent._events())
        try:
            response = await asyncio.wait_for(agent.prompt([acp.text_block("q")], SID), 20)
            assert response.stop_reason == "end_turn" and agent._failure is None and not pump.done()
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_second_gap_during_a_submits_recovery_read_does_not_lock_out_the_consumer(monkeypatch):
    """After ``/new`` the view's socket still carries the previous session's subscription. A submit acked
    across a gap reads its receipt holding the re-attach lock; a second gap then sends the renderer for that
    lock while the other session's frames fill the queue ahead of the receipt. It waits taking frames."""
    from hermes_cli.gateway_chat_view import GatewayChatView
    first_resume = asyncio.Event()

    def event(kind, text="", session="s"):
        return {"method": "event", "params": {"session_id": session, "type": kind,
                                              "admission_id": "successor", "payload": {"text": text}}}

    class Wire:
        def __init__(self):
            self.inbox, self.submit_ack = asyncio.Queue(), None

        def put(self, frame):
            self.inbox.put_nowait(json.dumps(frame))

        async def send(self, raw):
            request = json.loads(raw)
            answer = {"prompt.submit": self.submitted, "session.create": self.created,
                      "session.resume": self.resumed, "prompt.receipt": self.receipt}.get(request["method"], self.empty)
            answer({"jsonrpc": "2.0", "id": request["id"]})

        def empty(self, reply):  # session.info, runtime.describe
            self.put({**reply, "result": {}})

        def submitted(self, reply):
            self.submit_ack = {**reply, "result": {"admission_id": "a", "status": "queued"}}
            self.put(event("session.replay_gap"))

        def created(self, reply):
            self.put({**reply, "result": {"stored_session_id": "s", "execution_generation": 0}})

        def resumed(self, reply):
            self.put({**reply, "result": {"stored_session_id": "s", "execution_generation": 0,
                                          "pending": [], "prompts": []}})
            if self.submit_ack is not None:
                first_resume.set()

        def receipt(self, reply):
            self.put(event("session.replay_gap"))  # s retires again; t, still subscribed, streams on
            for seq in range(5000):
                self.put(event("message.delta", str(seq), session="t"))
            self.put({**reply, "result": {"admission_id": "a", "status": "terminal", "outcome": "completed",
                                          "result": {"final_response": "done", "completed": True}}})

        async def __aiter__(self):
            while True:
                yield await self.inbox.get()

    wire = Wire()
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 2)
    async with GatewayClient(wire) as client:
        view = GatewayChatView(client, {"stored_session_id": "t"}, quiet=True)
        await view.command("/new")
        renderer = asyncio.create_task(view.render())
        submit = asyncio.create_task(view.submit("q"))
        try:
            await asyncio.wait_for(first_resume.wait(), 5)
            async with view._attaching:  # the ack lands once that re-attach adopted its snapshot
                wire.put(wire.submit_ack)
            await asyncio.wait_for(submit, 10)
            assert view.completions.get("a", {}).get("text") == "done" and not client.reader.done()
        finally:
            for task in (submit, renderer):
                task.cancel()
            await asyncio.gather(submit, renderer, return_exceptions=True)
