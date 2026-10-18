"""G4 probe (lane 5, MSG-27): on the IDLE path a ``/<skill>`` is rewritten off-loop (a cold skill scan)
before it is admitted. A ``/new`` that lands during that rewrite must drop it, as the busy path does
(``_busy_skill_turn`` keeps the epoch it arrived under): the old command must never run in the fresh
conversation.

Self-contained (runs on main and the lane head). Real GatewayRunner, DiscordAdapter, SessionAuthority
and drain over SQLite; the turn body (``_handle_message_with_agent``) is a stand-in, the skill is a
stub (no skills directory), and the rewrite is held on a threading gate.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.waiting import async_wait_until


def _owner(monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.run import GatewayRunner
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    sent, hooks = [], []

    async def send(chat_id, content, reply_to=None, metadata=None):
        sent.append(content)
        for hook in list(hooks):
            await hook(content)
        return SendResult(success=True, message_id=f's{len(sent)}')
    monkeypatch.setattr(adapter, 'send', send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.DISCORD: adapter}
    monkeypatch.setattr(runner, '_read_user_config', lambda: {'approvals': {'destructive_slash_confirm': False}})
    return runner, adapter, sent, hooks


def _slow_skill(monkeypatch):
    """One installed skill, ``/demo``, whose rewrite blocks until ``gate`` opens."""
    import agent.skill_bundles as bundles
    import agent.skill_commands as skills
    from agent import skill_utils
    started, gate = threading.Event(), threading.Event()

    def build(key, text='', **_):
        started.set()
        gate.wait(10)
        return f'[demo skill] {text}'
    monkeypatch.setattr(skills, 'get_skill_commands', lambda: {'/demo': {'name': 'demo'}})
    monkeypatch.setattr(skills, 'resolve_skill_command_key', lambda command, **_: '/demo' if command == 'demo' else None)
    monkeypatch.setattr(skills, 'split_stacked_skill_commands', lambda rest, **_: ([], rest), raising=False)
    monkeypatch.setattr(skills, 'build_skill_invocation_message', build)
    monkeypatch.setattr(bundles, 'resolve_bundle_command_key', lambda command: None, raising=False)
    monkeypatch.setattr(skill_utils, 'get_disabled_skill_names', lambda **_: set())
    return started, gate


async def _idle_chat(monkeypatch):
    """A chat whose first turn A already finished: the next input takes the idle path."""
    from gateway.platforms.event import MessageEvent
    from gateway.session_authority import initialize_session_authority
    from gateway.session_ingress import executing_admission
    from gateway.session_results import execution_result

    runner, adapter, sent, hooks = _owner(monkeypatch)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    rig = SimpleNamespace(runner=runner, adapter=adapter, sent=sent, hooks=hooks, authority=authority,
                          source=source, key=runner.session_store._generate_session_key(source), order=[])

    async def turn(event, source, quick_key, run_generation):
        sid = runner.session_store.peek_session_id(quick_key)
        rig.order.append((event.message_id, sid))
        result = {'final_response': 'reply ' + str(event.message_id), 'messages': []}
        captured = execution_result.get()
        if captured is not None:
            captured['result'] = result
        return result['final_response']
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    await adapter.handle_message(MessageEvent(text='A', source=source, message_id='A'))
    await async_wait_until(lambda: rig.order and not adapter._active_sessions and 'reply A' in sent,
                           timeout=15, what='A to finish')
    return rig


def _ran_or_admitted_after_new(rig, fresh):
    from hermes_state_runtime import list_session_admissions
    rows = [row['request_id'] for row in list_session_admissions(rig.authority.db, session_id=fresh,
                                                                     pending_only=False)]
    return [r for r in rows if r == 'C'], [m for m, sid in rig.order if m == 'C']


@pytest.mark.asyncio
async def test_g4_new_during_idle_skill_rewrite_drops_it_runner_path(monkeypatch):
    """The runner's idle path (as the lane's busy test drives it with ``caller='runner'``)."""
    from gateway.platforms.event import MessageEvent, MessageType
    started, gate = _slow_skill(monkeypatch)
    rig = await _idle_chat(monkeypatch)
    old = rig.runner.session_store.peek_session_id(rig.key)
    command = MessageEvent(text='/demo C', message_type=MessageType.COMMAND, source=rig.source, message_id='C')
    task = asyncio.create_task(rig.runner._handle_message(command))
    try:
        await async_wait_until(lambda: started.is_set() or task.done(), what='the rewrite to start')
        assert not task.done(), task
        await rig.adapter.handle_message(MessageEvent(text='/new', message_type=MessageType.COMMAND,
                                                      source=rig.source, message_id='N'))
        await async_wait_until(lambda: rig.runner.session_store.peek_session_id(rig.key) != old
                               and not rig.adapter._active_sessions, timeout=15, what='/new to finish')
        fresh = rig.runner.session_store.peek_session_id(rig.key)
    finally:
        gate.set()
    await asyncio.wait_for(task, 15)
    await async_wait_until(lambda: not rig.adapter._active_sessions, timeout=15)
    rows, ran = _ran_or_admitted_after_new(rig, fresh)
    assert (rows, ran) == ([], []), f'/demo C (sent before /new) reached the fresh conversation {fresh}: {rig.order}'


@pytest.mark.asyncio
async def test_g4_new_during_idle_skill_rewrite_drops_it_adapter_path(monkeypatch):
    """The adapter's own task: /new answers, then cancels the command's task. The rewrite finishes
    while the reset reply is being sent (a slow platform send), so the admission write has started
    when the cancel arrives."""
    from gateway.platforms.event import MessageEvent, MessageType
    from hermes_state_runtime import list_session_admissions
    started, gate = _slow_skill(monkeypatch)
    rig = await _idle_chat(monkeypatch)
    old = rig.runner.session_store.peek_session_id(rig.key)

    async def slow_reset_reply(content):
        fresh = rig.runner.session_store.peek_session_id(rig.key)
        if fresh == old or gate.is_set():
            return
        gate.set()  # the cold scan returns while the platform send is in flight

        def committed():
            return any(row['request_id'] == 'C' for row in list_session_admissions(
                rig.authority.db, session_id=fresh, pending_only=False)) or 'C' in [m for m, _ in rig.order]
        try:
            await async_wait_until(committed, timeout=3, what='the command to commit')
        except AssertionError:
            pass  # a fixed build never admits it
    rig.hooks.append(slow_reset_reply)
    command = MessageEvent(text='/demo C', message_type=MessageType.COMMAND, source=rig.source, message_id='C')
    await rig.adapter.handle_message(command)
    try:
        await async_wait_until(lambda: started.is_set(), what='the rewrite to start')
        await rig.adapter.handle_message(MessageEvent(text='/new', message_type=MessageType.COMMAND,
                                                      source=rig.source, message_id='N'))
    finally:
        gate.set()
    await async_wait_until(lambda: not rig.adapter._active_sessions, timeout=15, what='the chat to settle')
    fresh = rig.runner.session_store.peek_session_id(rig.key)
    assert fresh != old
    from tests.waiting import async_wait_until as wait
    rows, ran = _ran_or_admitted_after_new(rig, fresh)
    if rows:  # admitted: give the drain its chance to run it
        await wait(lambda: _ran_or_admitted_after_new(rig, fresh)[1], timeout=10, what='C to run')
        rows, ran = _ran_or_admitted_after_new(rig, fresh)
    assert (rows, ran) == ([], []), f'/demo C (sent before /new) reached the fresh conversation {fresh}: {rig.order}'
