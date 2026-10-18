"""A serial reader's restart must await a burst's durable commit, including behind capture."""
import asyncio
import threading

import pytest

from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_restart_waits_for_burst_queued_behind_initial_capture(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from gateway.session_authority import SessionAuthority, initialize_session_authority
    from gateway.session_ingress_burst import holds_busy_burst
    import gateway.session_envelope as envelope
    from plugins.platforms.signal.adapter import SignalAdapter

    monkeypatch.setenv('SIGNAL_ALLOWED_USERS', '+15550001')
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig())
    adapter = SignalAdapter(PlatformConfig(enabled=True, extra={'account': '+15559999'}))
    adapter.gateway_runner = runner
    runner.adapters = {Platform.SIGNAL: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='restart-probe')
    runner._wire_adapter_handlers(adapter, busy_text_mode='queue')
    source = adapter.build_source(chat_id='+15550001', user_id='+15550001', chat_type='dm')
    key = runner._session_key_for_source(source)
    entered, release = threading.Event(), threading.Event()
    real_capture = envelope.capture_native_media

    def held(paths):
        entered.set()
        assert release.wait(30)
        return real_capture(paths)

    async def send(*args, **kwargs):
        return SendResult(success=True, message_id='sent')

    at_restart = []

    def restart(**kwargs):
        at_restart.append([row[0] for row in authority.db._read_all('SELECT request_id FROM session_admissions ORDER BY seq')])
        runner._draining = True

    monkeypatch.setattr(envelope, 'capture_native_media', held)
    monkeypatch.setattr(adapter, 'send', send)
    monkeypatch.setattr(runner, 'request_restart', restart)
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video bytes' * 100)
    second = command = None
    try:
        # Idle input is a background task even on Signal; the serial reader can read B next.
        await adapter.handle_message(MessageEvent(text='A', source=source, message_id='A',
            message_type=MessageType.VIDEO, media_urls=[str(clip)], media_types=['video/mp4']))
        assert await asyncio.to_thread(entered.wait, 30)
        second = asyncio.create_task(adapter.handle_message(MessageEvent(text='B', source=source, message_id='B')))
        await asyncio.wait_for(second, 30)  # the serial reader cannot read /restart until B returns
        command = asyncio.create_task(adapter.handle_message(MessageEvent(text='/restart', source=source, message_id='R')))
        # Deterministically wait until either the restart fires too soon, or it is held on main's
        # already committed B; then release capture. Main's B can overtake A, a separate known bug.
        await async_wait_until(lambda: at_restart or authority.db._read_all(
            "SELECT 1 FROM session_admissions WHERE request_id='B'"), timeout=30)
        release.set()
        await asyncio.wait_for(command, 30)
        if at_restart and 'B' not in at_restart[0]:
            await async_wait_until(lambda: not adapter._background_tasks, timeout=30)
            assert not authority.db._read_all("SELECT 1 FROM session_admissions WHERE request_id='B'"), (
                'B unexpectedly committed after restart closed admission')
        assert at_restart and 'B' in at_restart[0], f'restart closed admission before B committed: {at_restart}'
    finally:
        release.set()
        runner._draining = False
        for task in (second, command):
            if task is not None:
                await asyncio.wait_for(task, 30)
        await adapter.cancel_background_tasks()
        from gateway.session_runtime_workers import mutation_tasks
        await async_wait_until(lambda: not mutation_tasks(authority), timeout=30)
        runner.session_store.close_all_db_handles()
