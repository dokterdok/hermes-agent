"""Round-2 review: a stop must fence an older rewrite before yielding to SQLite."""
import asyncio
from contextvars import ContextVar
import threading

import pytest

from tests.gateway.test_messaging_slash_command_fifo import _owner, _skill
from tests.waiting import async_wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize('reset', ['/stop', '/new'])
async def test_stop_fences_rewrite_while_its_snapshot_is_pending(monkeypatch, reset):
    import agent.skill_commands as skills
    import hermes_state_runtime as runtime
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.session_authority import initialize_session_authority
    from gateway.session_results import execution_result

    _skill(monkeypatch)
    rewrite_entered, rewrite_release = threading.Event(), threading.Event()
    snapshot_entered, snapshot_release = threading.Event(), threading.Event()
    stopping = ContextVar('review_stop', default=False)

    def rewrite(key, text='', **kwargs):
        rewrite_entered.set()
        assert rewrite_release.wait(20)
        return '[demo skill] ' + text

    monkeypatch.setattr(skills, 'build_skill_invocation_message', rewrite)
    runner, adapter, sent = _owner(monkeypatch)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='review')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    key = runner.session_store._generate_session_key(source)
    executed = []

    async def turn(event, source, quick_key, generation):
        executed.append(event.message_id)
        captured = execution_result.get()
        if captured is not None:
            captured['result'] = {'final_response': 'ok', 'messages': []}
        return 'ok'

    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    tasks = []
    try:
        await adapter.handle_message(MessageEvent(text='A', source=source, message_id='A'))
        await async_wait_until(lambda: executed == ['A'] and not runner._is_session_running(key), timeout=15)
        command = MessageEvent(text='/demo C', message_type=MessageType.COMMAND, source=source, message_id='C')
        command_task = asyncio.create_task(runner._handle_message(command))
        tasks.append(command_task)
        await async_wait_until(rewrite_entered.is_set, timeout=5)
        real_list = runtime.list_session_admissions

        def delayed_snapshot(*args, **kwargs):
            if stopping.get() and not snapshot_entered.is_set():
                snapshot_entered.set()
                assert snapshot_release.wait(20)
            return real_list(*args, **kwargs)

        monkeypatch.setattr(runtime, 'list_session_admissions', delayed_snapshot)

        async def stop():
            token = stopping.set(True)
            try:
                return await runner._handle_message(MessageEvent(
                    text=reset, message_type=MessageType.COMMAND, source=source, message_id='S'))
            finally:
                stopping.reset(token)

        stop_task = asyncio.create_task(stop())
        tasks.append(stop_task)
        await async_wait_until(snapshot_entered.is_set, timeout=5)
        rewrite_release.set()
        await asyncio.wait_for(asyncio.shield(command_task), 10)
        snapshot_release.set()
        await asyncio.wait_for(stop_task, 10)
        assert 'C' not in executed, f'{reset} let its older slash command execute: {executed}'
    finally:
        rewrite_release.set()
        snapshot_release.set()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        await async_wait_until(lambda: not adapter._background_tasks, timeout=10)
        runner.session_store.close_all_db_handles()
