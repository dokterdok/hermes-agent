"""Probe (claude, r2 lane5): after 7cf9ce7d80e a serial reader stalls behind another input's slow capture.
Signal, queue mode: idle video V (slow capture), text B (a burst, flushed and queued behind V), then a
later text C from the same chat. C's handle_message is the reader's await; it must not wait for V's capture
(MSG-26: the reader never waits for a capture). Every other chat on the Signal account is unread meanwhile."""
import asyncio

import pytest

from gateway.config import GatewayConfig
from gateway.platforms.base import SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session_authority import initialize_session_authority
from gateway.session_results import execution_result
from tests.gateway.test_busy_burst_serial_reader import _hold_capture, _signal
from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_a_later_text_is_read_on_while_an_earlier_video_still_captures(monkeypatch, tmp_path):
    adapter, source = _signal(monkeypatch)
    entered, release = _hold_capture(monkeypatch)
    runner = GatewayRunner(GatewayConfig())

    async def send(chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id='sent')
    monkeypatch.setattr(adapter, 'send', send)
    monkeypatch.setattr(adapter, 'send_typing', lambda *a, **k: asyncio.sleep(0))
    adapter.gateway_runner = runner
    runner.adapters = {adapter.platform: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter, busy_text_mode='queue')

    async def turn(event, source, quick_key, run_generation):
        execution_result.get()['result'] = {'final_response': 'ok', 'messages': []}
        return 'ok'
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video-bytes' * 1000)
    video = MessageEvent(text='', message_type=MessageType.VIDEO, source=source, message_id='V',
                         media_urls=[str(clip)], media_types=['video/mp4'])
    third = None
    try:
        await adapter.handle_message(video)
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.wait_for(adapter.handle_message(MessageEvent(text='B', source=source, message_id='B')), 5)
        # B's burst window ends; its commit queues behind V's route reservation.
        await async_wait_until(lambda: any(entry[1] >= 2 for entry in authority._route_turns.values()),
                               timeout=10, what='B queued behind the capturing V')
        third = asyncio.create_task(adapter.handle_message(MessageEvent(text='C', source=source, message_id='C')))
        done, _ = await asyncio.wait({third}, timeout=2.0)
        assert done, 'the serial reader waited for V\'s capture before reading past C'
    finally:
        release.set()
        if third is not None:
            await asyncio.wait_for(third, 15)
        await adapter.cancel_background_tasks()
        await asyncio.to_thread(authority.db.close)
