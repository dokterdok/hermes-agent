"""Probe (claude, r2 lane5): 432daa45333 stops fencing a command turn on a FIFO paused on a lost turn.
An idle /demo C whose rewrite is slow, then /new on that paused chat: C must not run in the fresh
conversation (92853004762's W3 claim). On 432daa45333^ the runner epoch bumped regardless of pause."""
import asyncio

import pytest

from tests.waiting import async_wait_until
from tests.gateway.test_messaging_slash_command_fifo import _owner, _skill


@pytest.mark.asyncio
@pytest.mark.parametrize('reset', ['/new'])
async def test_reset_on_a_paused_chat_during_an_idle_skill_rewrite_never_runs_it_in_the_fresh_session(monkeypatch, reset):
    import threading
    import agent.skill_commands as skills
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.session_authority import initialize_session_authority
    from gateway.session_ingress import executing_admission
    from gateway.session_results import execution_result
    from hermes_state_runtime import list_session_admissions

    _skill(monkeypatch)
    started, gate = threading.Event(), threading.Event()

    def slow(text):
        started.set()
        gate.wait(10)
        return f'[demo skill] {text}'
    monkeypatch.setattr(skills, 'build_skill_invocation_message', lambda key, text='', **_: slow(text))
    runner, adapter, _sent = _owner(monkeypatch)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    order = []

    async def turn(event, source, quick_key, run_generation):
        order.append((event.message_id, executing_admission.get()))
        captured = execution_result.get()
        if captured is not None:
            captured['result'] = {'final_response': 'ok', 'messages': []}
        return 'ok'
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    key = runner.session_store._generate_session_key(source)
    await adapter.handle_message(MessageEvent(text='A', source=source, message_id='A'))
    await async_wait_until(lambda: order == [('A', True)] and not runner._is_session_running(key),
                           timeout=15, what='A to run and settle')
    old_sid = runner.session_store.peek_session_id(key)
    # The FIFO pauses on a lost turn (as after a crash mid-turn): the operator's to resume.
    authority.db._execute_write(lambda conn: conn.execute(
        "UPDATE session_admissions SET status='unknown' WHERE request_id='A'"))
    command = MessageEvent(text='/demo C', message_type=MessageType.COMMAND, source=source, message_id='C')
    task = asyncio.create_task(runner._handle_message(command))
    await async_wait_until(lambda: started.is_set() or task.done(), what='the idle rewrite to start')
    assert not task.done()
    await runner._handle_message(MessageEvent(text=reset, message_type=MessageType.COMMAND,
                                              source=source, message_id='S'))
    new_sid = runner.session_store.peek_session_id(key)
    assert new_sid != old_sid
    gate.set()
    await asyncio.wait_for(task, 15)
    await asyncio.sleep(2.0)
    new_rows = [row['request_id'] for row in list_session_admissions(
        authority.db, session_id=new_sid, pending_only=False)]
    old_rows = [(row['request_id'], row['status']) for row in list_session_admissions(
        authority.db, session_id=old_sid, pending_only=False)]
    assert 'C' not in new_rows and 'C' not in [m for m, _ in order], (new_rows, old_rows, order)
