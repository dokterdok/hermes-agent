"""Review probe (lane 2, G2): waiting ``session.context_breakdown`` reads are not bounded.

The semaphore bounds RUNNING reads (2). Each read frame leaves the bounded ``pending`` queue
(``_WS_DISPATCH_QUEUE_MAX``) at once and becomes a retained task, so the reader never parks:
a client sending reads faster than they finish grows tasks without limit, and the disconnect
teardown then runs every one of them before it parks the transport's sessions.
Self-contained harness (same shape as ``tests/tui_gateway/test_tui_gateway_ws.py``'s) so the
probe runs unchanged on main, where that harness does not block context reads.
"""
import asyncio
import json
import threading

from tui_gateway import server
from tui_gateway import ws as ws_mod


def _harness(monkeypatch):
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0)
    sent, log, release = [], [], threading.Event()

    def fake_dispatch(req, transport):
        log.append(f"dispatch:{req.get('id')}")
        if req.get("method") == "session.context_breakdown":
            release.wait(10)  # a slow read: thousands of stored rows
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": {}}

    def fake_close_sessions(transport, end_reason):
        log.append("teardown")
        return 0, 0

    monkeypatch.setattr(server, "dispatch", fake_dispatch)
    monkeypatch.setattr(server, "_close_sessions_for_transport", fake_close_sessions)
    inbound: asyncio.Queue = asyncio.Queue()

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, line):
            sent.append(json.loads(line))

        async def receive_text(self):
            frame = await inbound.get()
            if frame is None:
                raise ws_mod._WebSocketDisconnect()
            return json.dumps(frame)

        async def close(self):
            pass

    return FakeWS(), inbound, sent, log, release


async def _wait_for(predicate, timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def test_waiting_context_reads_keep_reader_backpressure(monkeypatch):
    ws, inbound, sent, log, release = _harness(monkeypatch)
    burst = ws_mod._WS_DISPATCH_QUEUE_MAX * 4
    observed = {}

    async def scenario():
        task = asyncio.create_task(ws_mod.handle_ws(ws))
        try:
            for n in range(burst):
                inbound.put_nowait({"jsonrpc": "2.0", "id": f"c{n}", "method": "session.context_breakdown",
                                    "params": {"session_id": "s"}})
            # main: the first read blocks the serial lane, ``pending`` fills and the reader parks with
            # frames left unread. Head: every frame is read and turned into a waiting task.
            observed["drained"] = await _wait_for(lambda: inbound.qsize() == 0, timeout=1.5)
            observed["tasks"] = len(asyncio.all_tasks())
            observed["left"] = inbound.qsize()
        finally:
            release.set()
        inbound.put_nowait(None)
        await asyncio.wait_for(task, 60)

    asyncio.run(scenario())
    limit = ws_mod._WS_DISPATCH_QUEUE_MAX + 8
    assert not observed["drained"] and observed["tasks"] < limit, (
        f"reader consumed all {burst} read frames (left={observed['left']}) and the loop holds "
        f"{observed['tasks']} tasks; nothing bounds waiting reads")
