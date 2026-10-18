"""Review G3: real bounded client + real consumers, owner frames before resume reply."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest


def event(kind, seq=1):
    return {'jsonrpc': '2.0', 'method': 'event', 'params': {
        'session_id': 's', 'type': kind, 'replay_epoch': 'e', 'seq': seq,
        'payload': {'text': ''}}}


class Wire:
    def __init__(self, burst):
        self.burst = burst
        self.frames = asyncio.Queue()
        self.answer_read = asyncio.Event()
        self.resume_sent = asyncio.Event()

    async def send(self, raw):
        req = json.loads(raw)
        assert req['method'] == 'session.resume'
        self.resume_sent.set()
        # An already attached, healthy owner can send a batch before its resume
        # response (owner fanout is 16,384 frames, the client queue only 4,096).
        for seq in range(2, self.burst + 2):
            self.frames.put_nowait(event('message.delta', seq))
        self.frames.put_nowait({'jsonrpc': '2.0', 'id': req['id'], 'result': {
            'stored_session_id': 's', 'replay_epoch': 'e', 'last_sequence': 1,
            'pending': [], 'prompts': []}})

    async def __aiter__(self):
        while True:
            frame = await self.frames.get()
            if 'id' in frame:
                self.answer_read.set()
            yield json.dumps(frame)


@pytest.mark.asyncio
@pytest.mark.parametrize('consumer', ['chat', 'acp'])
@pytest.mark.parametrize('burst', [4096, 4097])
async def test_resume_reply_behind_burst_remains_reachable(monkeypatch, consumer, burst):
    from hermes_cli import gateway_client
    monkeypatch.setattr(gateway_client, 'DEFAULT_RPC_TIMEOUT', 2)
    monkeypatch.setattr(gateway_client, 'EVENT_STALL_TIMEOUT', 3, raising=False)
    wire = Wire(burst)
    async with gateway_client.GatewayClient(wire) as client:
        if consumer == 'chat':
            from hermes_cli.gateway_chat_view import GatewayChatView
            view = GatewayChatView(client, {'stored_session_id': 's'}, quiet=True)
            pump = asyncio.create_task(view.render())
            failure = lambda: view.failure
        else:
            from acp_adapter.gateway_server import GatewayACPAgent
            view = GatewayACPAgent()
            view._gateway, view._conn = client, AsyncMock()
            view._snapshots['s'] = {'replay_epoch': 'e', 'last_sequence': 0}
            pump = view._event_task = asyncio.create_task(view._events())
            failure = lambda: view._failure
        try:
            wire.frames.put_nowait(event('session.replay_gap'))
            answered = asyncio.create_task(wire.answer_read.wait())
            done, _ = await asyncio.wait({pump, answered}, timeout=5,
                                         return_when=asyncio.FIRST_COMPLETED)
            assert wire.answer_read.is_set(), (
                f'{consumer}: {type(failure()).__name__} {failure()}; '
                f'resume_sent={wire.resume_sent.is_set()}, queued={client.events.qsize()}')
            assert failure() is None
        finally:
            pump.cancel()
            answered.cancel()
            await asyncio.gather(pump, answered, return_exceptions=True)
