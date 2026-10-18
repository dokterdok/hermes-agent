"""Claude r2 lane2 probes for 3ad36cf9cdc (EventQueue ordered overflow). Run on head and on its parent."""
import asyncio
import json

import pytest

from hermes_cli import gateway_client
from hermes_cli.gateway_client import GatewayClient

SID = "probe-sid"


def _frame(i):
    return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
        "type": "message.delta", "session_id": SID, "payload": {"text": str(i)}}})


class Socket:
    """Owner: answers the first RPC behind a burst of already-buffered frames, then streams on live."""

    def __init__(self, burst, live):
        self.burst, self.live = burst, live
        self.sent = asyncio.Event()
        self.rid = None

    async def send(self, raw):
        self.rid = json.loads(raw)["id"]
        self.sent.set()

    async def __aiter__(self):
        await self.sent.wait()
        for i in range(self.burst):
            yield _frame(i)
        yield json.dumps({"jsonrpc": "2.0", "id": self.rid, "result": {"ok": True}})
        for i in range(self.burst, self.burst + self.live):
            await asyncio.sleep(0.002)  # the turn keeps streaming
            yield _frame(i)
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_a_steady_consumer_is_not_failed_after_an_rpc_from_another_task(monkeypatch):
    """Scaled: queue 20, stall 0.3 s, 100 frames behind a full queue while an RPC (another task: ACP's
    prompt.submit, the chat view's composer) is awaited. The consumer takes one event every 10 ms,
    far inside the stall bound, so it has not stalled and the link must live."""
    monkeypatch.setattr(gateway_client, "EVENT_STALL_TIMEOUT", 0.3)
    queue_size, overflow, live = 20, 100, 20
    total = queue_size + overflow + live
    client = GatewayClient(Socket(queue_size + overflow, live))
    client.events = type(client.events)(maxsize=queue_size)
    seen = []
    async with client:
        answer = asyncio.create_task(client.rpc("session.stats"))

        async def consume():
            while len(seen) < total:
                item = await client.events.get()
                seen.append(item)
                if isinstance(item, Exception):
                    return
                await asyncio.sleep(0.01)  # slow but steady: one event per 10 ms

        await asyncio.wait_for(consume(), 15)
        result = await asyncio.wait_for(answer, 5)
    failures = [i for i, item in enumerate(seen) if isinstance(item, Exception)]
    assert not failures, f"link failed at event #{failures[0]} with a consumer taking an event every 10 ms"
    assert result == {"ok": True}
    assert [item["params"]["payload"]["text"] for item in seen] == [str(i) for i in range(total)]
