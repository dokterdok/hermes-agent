"""G4: a reset during an idle skill rewrite must not enqueue it on the fresh route."""
import asyncio
import threading

import pytest

from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_idle_rewrite_cannot_admit_after_new(monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import SessionAuthority, initialize_session_authority
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    adapter.gateway_runner = runner
    runner.adapters = {Platform.DISCORD: adapter}
    monkeypatch.setattr(runner, '_read_user_config', lambda: {'approvals': {'destructive_slash_confirm': False}})
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='probe')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    key = runner._session_key_for_source(source)
    old = authority.register(source).session_id
    entered, release = threading.Event(), threading.Event()
    replying, reply_release = asyncio.Event(), asyncio.Event()
    legacy_turn, provider_gate = asyncio.Event(), asyncio.Event()

    def slow_skill(event, source, quick_key, command):
        if command == 'demo':
            entered.set()
            assert release.wait(30)
            event.text = '[demo skill] old command'
        return None

    async def send(chat_id, content, reply_to=None, metadata=None):
        replying.set()
        await reply_release.wait()  # a slow transport sending the /new reply
        return SendResult(success=True, message_id='sent')

    async def no_provider(*args):
        legacy_turn.set()
        await provider_gate.wait()
        return 'stand-in reply'

    monkeypatch.setattr(runner, '_hm_skill_slash_rewrite', slow_skill)
    monkeypatch.setattr(runner, '_handle_message_with_agent', no_provider)
    monkeypatch.setattr(adapter, 'send', send)
    new_task = None
    try:
        await adapter.handle_message(MessageEvent(text='/demo old command', source=source, message_id='old'))
        assert await asyncio.to_thread(entered.wait, 30)
        original_task = adapter._session_tasks[key]
        new_task = asyncio.create_task(adapter.handle_message(MessageEvent(text='/new', source=source, message_id='new')))
        await asyncio.wait_for(replying.wait(), 30)
        fresh = runner.session_store.peek_session_id(key)
        assert fresh != old
        release.set()

        def rows():
            return authority.db._read_all("SELECT target_session_id FROM session_admissions WHERE request_id='old'")

        await async_wait_until(lambda: rows() or legacy_turn.is_set() or original_task.done(), timeout=30)
        reply_release.set()
        await asyncio.wait_for(new_task, 30)  # original adapter task has now been cancelled
        committed = [row[0] for row in rows()]
        assert committed == [], f'old command admitted after /new onto {committed}; fresh={fresh}'
    finally:
        release.set()
        reply_release.set()
        if new_task is not None:
            await asyncio.wait_for(new_task, 30)
        await adapter.cancel_background_tasks()
        runner.session_store.close_all_db_handles()
