"""A resume covering an unacknowledged completed turn must not lose post-ack recovery."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest


def frame(kind, sid='s', seq=1):
    return {'jsonrpc': '2.0', 'method': 'event', 'params': {
        'session_id': sid, 'type': kind, 'admission_id': 'other',
        'replay_epoch': 'e', 'seq': seq, 'payload': {'text': 'x'}}}


class Wire:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.submit_id = None
        self.receipts = []

    async def send(self, raw):
        req = json.loads(raw)
        if req['method'] == 'prompt.submit':
            self.submit_id = req['id']
            self.frames.put_nowait(frame('session.replay_gap'))
        elif req['method'] == 'session.resume':
            # The first turn completed in the gap before its ack reached ACP.
            self.frames.put_nowait({'id': req['id'], 'result': {
                'stored_session_id': 's', 'replay_epoch': 'e', 'last_sequence': 1,
                'pending': [], 'prompts': []}})
            self.frames.put_nowait(frame('message.delta', 't'))
        elif req['method'] == 'prompt.receipt':
            self.receipts.append(req['params']['admission_id'])
            self.frames.put_nowait({'id': req['id'], 'result': {
                'admission_id': 'a', 'status': 'terminal', 'outcome': 'completed',
                'result': {'final_response': 'finished'}}})
        else:
            raise AssertionError(req)

    async def __aiter__(self):
        while True:
            yield json.dumps(await self.frames.get())


@pytest.mark.asyncio
async def test_full_queue_does_not_discard_post_ack_recovery():
    import acp
    from acp_adapter.gateway_server import GatewayACPAgent
    from hermes_cli.gateway_client import GatewayClient
    from tests.waiting import async_wait_until

    wire = Wire()
    entered, release = asyncio.Event(), asyncio.Event()
    async with GatewayClient(wire) as client:
        agent = GatewayACPAgent()
        agent._gateway, agent._conn = client, AsyncMock()
        agent._snapshots.update({sid: {'replay_epoch': 'e', 'last_sequence': 0} for sid in ('s', 't')})

        async def deliver(**kwargs):
            if kwargs['session_id'] == 't' and not entered.is_set():
                entered.set()
                await release.wait()

        agent._conn.session_update.side_effect = deliver
        pump = agent._event_task = asyncio.create_task(agent._events())
        prompt = asyncio.create_task(agent.prompt([acp.text_block('q')], 's'))
        try:
            await async_wait_until(lambda: entered.is_set() or prompt.done() or agent._failure is not None, timeout=5)
            assert entered.is_set(), f'recovery failed before the post-ack race: {agent._failure}'
            for seq in range(2, client.events.maxsize + 2):
                wire.frames.put_nowait(frame('message.delta', 't', seq))
            wire.frames.put_nowait({'id': wire.submit_id, 'result': {'admission_id': 'a', 'status': 'queued'}})
            await async_wait_until(lambda: agent._admissions.get('s') == 'a', timeout=5)
            assert client.events.full(), 'probe did not exercise the full-queue branch'
            release.set()
            await async_wait_until(lambda: client.events.empty() or prompt.done(), timeout=5)
            done, _ = await asyncio.wait({prompt}, timeout=2)
            assert prompt in done, ('completed admission has no recovery left', wire.receipts, agent._failure)
            assert (await prompt).stop_reason == 'end_turn'
        finally:
            release.set()
            prompt.cancel()
            pump.cancel()
            await asyncio.gather(prompt, pump, return_exceptions=True)
