"""Probe (RT-37 focus 4): ``owner_forward.connect_owner`` dials the owner without ``max_size``, so the
websockets client keeps its 1 MiB default. Its very first RPC is ``session.resume``, whose reply carries
the FULL transcript (``local_history``: ``get_messages_as_conversation`` with no limit). A Bot Chat
whose stored transcript is over 1 MiB therefore can never be forwarded (and any later >1 MiB event
frame, e.g. the bot turn's large tool result, kills the relay). ``connect_gateway`` and
``session_a2a`` pass ``max_size=8 MiB`` for exactly this."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from hermes_cli import gateway_client
from hermes_cli.gateway_client import GATEWAY_WS_PROTOCOL

try:
    from tui_gateway import owner_forward
except ImportError:  # main
    owner_forward = None

_ROWS = [{"role": "tool", "content": "x" * 4096}] * 400  # ~1.6 MiB of transcript


async def _serve_resume_then(dial):
    from websockets.asyncio.server import serve

    async def handler(ws):
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {
                "session_id": "s", "messages": _ROWS, "message_count": len(_ROWS)}}))

    async with serve(handler, "127.0.0.1", 0, subprotocols=[GATEWAY_WS_PROTOCOL], max_size=None) as srv:
        port = srv.sockets[0].getsockname()[1]
        endpoint = SimpleNamespace(api_origin=f"http://127.0.0.1:{port}", profile_id="p", instance_id="i")
        return await dial(endpoint)


@pytest.fixture
def _no_ticket(monkeypatch):
    monkeypatch.setattr(gateway_client, "_session_ticket", lambda *_a, **_k: "ticket")


def test_owner_forward_dial_reads_a_long_chats_resume_reply(_no_ticket, tmp_path):
    if owner_forward is None:
        pytest.skip("main: no owner_forward")

    async def dial(endpoint):
        async with owner_forward.connect_owner(tmp_path, endpoint) as client:
            return await client.rpc("session.resume", session_id="s")

    result = asyncio.run(_serve_resume_then(dial))
    assert result["message_count"] == len(result["messages"]) == 400


def test_control_connect_gateway_reads_the_same_reply(_no_ticket):
    async def dial(endpoint):
        async with gateway_client.connect_gateway(endpoint=endpoint) as client:
            return await client.rpc("session.resume", session_id="s")

    result = asyncio.run(_serve_resume_then(dial))
    assert result["message_count"] == len(result["messages"]) == 400
