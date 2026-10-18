"""Review probe (lane 2, G3): a replay-gap re-attach awaited from the view's only event consumer.

``GatewayChatView.render`` awaits ``session.resume`` inside its event loop (``_reattach``). The owner
attaches the new subscription before it answers, so the turn's frames reach the socket ahead of
the reply. CLI-37 parks ``GatewayClient._read`` on a full queue (4,096): with more frames than that
ahead of the answer, the reader cannot reach it while the consumer cannot drain, and the view fails
with ``session_replay_gap`` after ``EVENT_STALL_TIMEOUT`` (30 s; shortened here).
"""
import asyncio
import json

import pytest

from hermes_cli import gateway_client
from hermes_cli.gateway_client import GatewayClient

SID = "s"


def _event(kind, payload, admission="a"):
    return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
        "type": kind, "session_id": SID, "admission_id": admission, "payload": payload}})


class ResumingOwner:
    """The owner's side of one socket: on ``session.resume`` the re-attached subscription streams
    ``burst`` frames of the running turn before the resume reply is written."""

    def __init__(self, burst):
        self.burst, self.inbox = burst, asyncio.Queue()

    async def send(self, raw):
        request = json.loads(raw)
        if request["method"] == "session.resume":
            for i in range(self.burst):
                self.inbox.put_nowait(_event("message.delta", {"text": f"{i}|"}))
            self.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {
                "pending": [{"admission_id": "a", "status": "started"}], "prompts": []}}))
        else:
            self.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}))

    async def __aiter__(self):
        while True:
            yield await self.inbox.get()


async def _gap_then_settle(burst):
    from hermes_cli.gateway_chat_view import GatewayChatView
    owner = ResumingOwner(burst)
    async with GatewayClient(owner) as client:
        view = GatewayChatView(client, {"stored_session_id": SID}, quiet=True)
        renderer = asyncio.create_task(view.render())
        try:
            owner.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
                "type": "session.replay_gap", "session_id": SID, "payload": {}}}))
            deadline = asyncio.get_running_loop().time() + 10
            attaching = getattr(view, "_attaching", None)  # main has no re-attach: a gap fails the view
            while (getattr(view, "gaps", 0) == 0 or (attaching and attaching.locked())) and view.failure is None:
                assert asyncio.get_running_loop().time() < deadline, "re-attach neither finished nor failed"
                await asyncio.sleep(0.02)
            return view.failure
        finally:
            renderer.cancel()
            await asyncio.gather(renderer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("burst", [1000, 5000])
async def test_reattach_survives_a_burst_ahead_of_the_resume_answer(monkeypatch, burst):
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 0.5, raising=False)
    failure = await asyncio.wait_for(_gap_then_settle(burst), 20)
    assert failure is None, f"burst={burst}: re-attach failed the view: {failure!r}"


async def _acp_gap(burst):
    from unittest.mock import AsyncMock
    from acp_adapter.gateway_server import GatewayACPAgent
    owner = ResumingOwner(burst)
    async with GatewayClient(owner) as client:
        agent = GatewayACPAgent()
        agent._snapshots[SID] = {"replay_epoch": "e", "last_sequence": 0, "pending": []}
        agent._gateway, agent._conn = client, AsyncMock()
        pump = agent._event_task = asyncio.create_task(agent._events())
        try:
            owner.inbox.put_nowait(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
                "type": "session.replay_gap", "session_id": SID, "payload": {}}}))
            deadline = asyncio.get_running_loop().time() + 10
            while not getattr(agent, "_adopted", {}).get(SID) and agent._failure is None:
                assert asyncio.get_running_loop().time() < deadline, "ACP re-attach neither finished nor failed"
                await asyncio.sleep(0.02)
            return agent._failure, pump.done()
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("burst", [1000, 5000])
async def test_acp_pump_survives_a_burst_ahead_of_the_resume_answer(monkeypatch, burst):
    """ACP multiplexes every editor session on one socket; its pump is the one consumer."""
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 0.5, raising=False)
    failure, stopped = await asyncio.wait_for(_acp_gap(burst), 20)
    assert failure is None and not stopped, f"burst={burst}: ACP pump stopped: {failure!r}"
