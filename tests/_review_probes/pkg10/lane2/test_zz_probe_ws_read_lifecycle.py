"""Review G2 and shutdown: exercise the real WS dispatcher with a gated handler."""
import asyncio
import json

import pytest


class Socket:
    def __init__(self):
        self.inbound = asyncio.Queue()
        self.received = 0
        self.all_received = asyncio.Event()
        self.target = 1000

    async def accept(self):
        pass

    async def send_text(self, line):
        pass

    async def receive_text(self):
        from tui_gateway import ws
        item = await self.inbound.get()
        if item is None:
            raise ws._WebSocketDisconnect()
        self.received += 1
        if self.received == self.target:
            self.all_received.set()
        return json.dumps(item)

    async def close(self):
        pass


def harness(monkeypatch):
    from tui_gateway import server, ws
    entered, release = asyncio.Event(), asyncio.Event()
    started = []

    async def dispatch(authority, req, method, transport):
        started.append(req['id'])
        entered.set()
        await release.wait()
        return {'jsonrpc': '2.0', 'id': req['id'], 'result': {}}

    # Keep the real dispatcher; replace only the expensive handler with a gate.
    monkeypatch.setattr(ws, '_dispatch_request', dispatch)
    monkeypatch.setattr(ws, '_start_backend_liveness', lambda: None, raising=False)
    monkeypatch.setattr(server, '_ensure_skin_watcher', lambda: None)
    monkeypatch.setattr(server, '_ensure_lease_watcher', lambda: None)
    monkeypatch.setattr(server, '_WS_ORPHAN_REAP_GRACE_S', 0)
    sock = Socket()
    return ws, sock, entered, release, started


def request(i):
    return {'jsonrpc': '2.0', 'id': i, 'method': 'session.context_breakdown', 'params': {}}


@pytest.mark.asyncio
async def test_waiting_context_reads_remain_bounded(monkeypatch):
    ws, sock, entered, release, started = harness(monkeypatch)
    task = asyncio.create_task(ws.handle_ws(sock))
    for i in range(sock.target):
        sock.inbound.put_nowait(request(i))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        try:
            await asyncio.wait_for(sock.all_received.wait(), 2)
        except TimeoutError:
            pass  # A bounded reader correctly stops before consuming this burst.
        assert sock.received <= ws._WS_DISPATCH_QUEUE_MAX + 3, (sock.received, len(started))
    finally:
        release.set()
        sock.inbound.put_nowait(None)
        await asyncio.wait_for(task, 10)


@pytest.mark.asyncio
async def test_server_cancellation_does_not_wait_for_context_read(monkeypatch):
    ws, sock, entered, release, started = harness(monkeypatch)
    task = asyncio.create_task(ws.handle_ws(sock))
    sock.inbound.put_nowait(request(1))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=2)
        assert task in done, 'socket shutdown still waits for the slow context handler'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
