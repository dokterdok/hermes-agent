"""Native reply receipts use actual receiving events, not routed worker identity."""
import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
# Gateway fixtures may install SDK mocks; these cases use real PTB message types.
if not isinstance(sys.modules.get('telegram'), ModuleType):
    for module_name in list(sys.modules):
        if module_name == 'telegram' or module_name.startswith('telegram.'):
            sys.modules.pop(module_name)
from telegram import Chat, ForceReply, Message, User

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.native_reply_input import NativeReplySubmission, handle_native_reply, text
from gateway.platforms.base import MessageType, SendResult
from gateway.run import GatewayRunner
from plugins.platforms.telegram.adapter import TelegramAdapter


def message(body='Reply text', *, number=201, reply=None, user=111, chat=100, thread=None, **kwargs):
    return Message(message_id=number, date=datetime.now(timezone.utc), chat=Chat(chat, 'private'),
        from_user=User(user, 'Fixture', user == 999), text=body, reply_to_message=reply,
        message_thread_id=thread, **kwargs)


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '111,222')
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner._scale_to_zero_note_real_inbound = lambda: None
    runner._hm_pre_gateway_dispatch_hook = lambda event, source: replace(event)
    runner._is_user_authorized_for_source = lambda source, **kwargs: source.user_id in {'111', '222', '999'}
    runner._admit_bot_message_for_source = lambda source: True
    runner._profile_name_for_source = lambda source, **kwargs: 'worker'
    runner._hm_pending_reply_intercepts = AsyncMock(return_value='ordinary')
    runner._session_key_for_source = lambda source: 'worker-session'
    runner._thread_metadata_for_source = lambda source, anchor=None: {'thread_id': source.thread_id}
    runner.session_authority = SimpleNamespace(profile_id=str(tmp_path), epoch=1,
                                                db=SimpleNamespace(db_path=tmp_path / 'state.db'))
    worker = SimpleNamespace(config=PlatformConfig())
    runner._adapter_for_source = lambda source: worker
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token='fixture-token'))
    adapter.gateway_runner = runner
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {'worker': {Platform.TELEGRAM: worker}}
    runner._native_transport_homes = {None: tmp_path}
    adapter.set_session_store(SimpleNamespace(sessions_dir=tmp_path / 'sessions'))
    adapter.set_message_handler(runner._handle_message)
    adapter._ensure_forum_commands = AsyncMock()
    adapter._should_process_message = lambda *args, **kwargs: True
    adapter._is_user_authorized_from_message = lambda msg: True
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id='response'))
    sent, effects, ordinary = [], [], []

    async def send_message(**kwargs):
        prompt = message(kwargs['text'], number=500 + len(sent), user=999, thread=kwargs.get('message_thread_id'))
        sent.append((kwargs, prompt))
        return prompt

    adapter._bot = SimpleNamespace(id=999, send_message=AsyncMock(side_effect=send_message))
    adapter._enqueue_text_event = ordinary.append
    original = adapter._build_message_event(message('/group', number=101), MessageType.COMMAND)

    async def consume(event, request):
        previous = await asyncio.to_thread(request.claim, event.message_id)
        if previous is not None:
            return previous
        effects.append(event)
        await asyncio.to_thread(request.finish, event.message_id, 'accepted')
        return 'accepted'

    return SimpleNamespace(runner=runner, adapter=adapter, original=original,
                           sent=sent, effects=effects, ordinary=ordinary, consume=consume)


async def open_prompt(state):
    request, result = await state.adapter.send_reply_input(state.original, text('title', group='Fixture'), state.consume)
    assert result.success
    params, prompt = state.sent[-1]
    assert isinstance(params['reply_markup'], ForceReply)
    assert params['reply_markup'].selective is True
    assert params['reply_parameters'].message_id == 101
    assert params['reply_parameters'].allow_sending_without_reply is False
    assert params['parse_mode'] is None
    return request, prompt


async def dispatch(state, prompt, body='Reply text', **kwargs):
    msg = message(body, reply=prompt, **kwargs)
    await state.adapter._handle_text_message(SimpleNamespace(message=msg, effective_message=msg, update_id=700), None)
    return msg


@pytest.mark.asyncio
async def test_actual_reply_reaches_consumer_before_fifo_and_busy_guards(receiver, monkeypatch):
    from gateway import session_ingress
    request, prompt = await open_prompt(receiver)
    monkeypatch.setattr(session_ingress, 'admit_message', AsyncMock(side_effect=AssertionError('native reply entered FIFO')))
    receiver.adapter._active_sessions['worker-session'] = guard = object()
    body = 'Keep @mentions, --flags and /paths unchanged.'
    await dispatch(receiver, prompt, body)
    actual, = receiver.effects
    assert actual is not receiver.original
    assert actual.message_id == '201' and actual.text == body
    assert actual.source.user_id == '111' and actual.source.profile == 'worker'
    assert receiver.runner._adapter_for_source(actual.source) is not receiver.adapter
    assert request.receiver[0] is receiver.adapter
    assert receiver.adapter._active_sessions['worker-session'] is guard
    receiver.runner._hm_pending_reply_intercepts.assert_not_awaited()
    assert not receiver.ordinary
    assert receiver.adapter.send.await_args.kwargs['reply_to'] == '201'
    assert request.path.stat().st_mode & 0o777 == 0o600
    await dispatch(receiver, prompt)
    await dispatch(receiver, prompt, number=202)
    assert len(receiver.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', ['user', 'chat', 'topic', 'edit', 'machine', 'expired', 'receiver', 'epoch', 'restart'])
async def test_known_prompt_mismatch_closes_without_stealing_bot_turn(receiver, mismatch):
    request, prompt = await open_prompt(receiver)
    kwargs = {}
    if mismatch == 'user':
        kwargs['user'] = 222
    elif mismatch == 'chat':
        kwargs['chat'] = 200
    elif mismatch == 'topic':
        kwargs['thread'] = 77
        kwargs['is_topic_message'] = True
    elif mismatch == 'edit':
        kwargs['edit_date'] = datetime.now(timezone.utc)
    elif mismatch == 'machine':
        kwargs['user'] = 999
    elif mismatch == 'expired':
        request.deadline = 0
    elif mismatch == 'receiver':
        receiver.runner.adapters[Platform.TELEGRAM] = object()
    elif mismatch == 'epoch':
        receiver.runner.session_authority.epoch += 1
    else:
        receiver.adapter._native_reply_inputs.clear()
        prompt = message(None, number=prompt.message_id, user=999)
    await dispatch(receiver, prompt, **kwargs)
    assert not receiver.effects and not receiver.ordinary
    receiver.runner._hm_pending_reply_intercepts.assert_not_awaited()


@pytest.mark.asyncio
async def test_ordinary_reply_and_real_command_keep_existing_dispatch(receiver):
    _, prompt = await open_prompt(receiver)
    await dispatch(receiver, message('Ordinary answer', number=99, user=999))
    assert receiver.ordinary[-1].text == 'Reply text'
    receiver.adapter.handle_message = AsyncMock()
    msg = message('/help', reply=prompt)
    await receiver.adapter._handle_command(SimpleNamespace(message=msg, effective_message=msg, update_id=701), None)
    receiver.adapter.handle_message.assert_awaited_once()
    assert receiver.adapter.handle_message.await_args.args[0].text == '/help'
    assert not receiver.effects


@pytest.mark.asyncio
async def test_unavailable_prompt_reply_is_closed_but_other_bot_text_is_not_captured(receiver):
    receiver.adapter._bot.send_message.side_effect = TimeoutError()
    request, result = await receiver.adapter.send_reply_input(receiver.original, text('title', group='Fixture'), receiver.consume)
    assert request is None and not result.success
    body = receiver.adapter._bot.send_message.await_args.kwargs['text']
    await dispatch(receiver, message(body, number=9999, user=999))
    assert not receiver.effects and not receiver.ordinary
    await dispatch(receiver, message(body + ' extra', number=9998, user=999))
    assert len(receiver.ordinary) == 1


@pytest.mark.asyncio
async def test_null_consumed_callback_does_not_fall_through(receiver):
    request, prompt = await open_prompt(receiver)
    request.on_reply = AsyncMock(return_value=None)
    event = receiver.adapter._build_message_event(message(reply=prompt), MessageType.TEXT)
    assert await handle_native_reply(receiver.runner, event, NativeReplySubmission(receiver.adapter, request.token, True)) == text('unknown')
    assert await handle_native_reply(receiver.runner, event, {'token': request.token}) is None


@pytest.mark.asyncio
async def test_startup_replay_uses_live_receiving_adapter_not_worker(receiver):
    request, prompt = await open_prompt(receiver)
    event = receiver.adapter._build_message_event(message(reply=prompt), MessageType.TEXT)
    event._native_reply_submission = NativeReplySubmission(receiver.adapter, request.token, True)
    receiver.runner._startup_restore_queue = [event]
    receiver.runner._startup_restore_in_progress = True
    assert await receiver.runner._drain_startup_restore_queue() == 1
    assert len(receiver.effects) == 1
    receiver.runner._hm_pending_reply_intercepts.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_receipt_failure_does_not_capture_unrelated_replies(receiver, monkeypatch):
    from plugins.platforms.telegram import reply_input
    _, prompt = await open_prompt(receiver)
    def unavailable(*args):
        raise OSError('fixture receipt unavailable')
    monkeypatch.setattr(reply_input, 'prompt_token', unavailable)
    await dispatch(receiver, message(None, number=prompt.message_id, user=999))
    assert not receiver.effects and not receiver.ordinary
    await dispatch(receiver, message('Ordinary bot answer', number=9998, user=999))
    assert len(receiver.ordinary) == 1


@pytest.mark.asyncio
async def test_media_reply_carries_attachment_fact_without_downloading(receiver):
    from telegram import Document
    _, prompt = await open_prompt(receiver)
    received = []
    async def inspect(event, request):
        received.append(event)
        return 'text required'
    next(iter(receiver.adapter._native_reply_inputs.values())).on_reply = inspect
    msg = message(None, reply=prompt, caption='caption', document=Document('file', 'unique'))
    await receiver.adapter._handle_media_message(SimpleNamespace(message=msg, update_id=702), None)
    actual, = received
    assert actual.source.message_had_attachments is True
    assert actual.source.user_id == '111' and actual.text == ''
    assert not actual.media_urls
    assert not receiver.ordinary
    receiver.runner._hm_pending_reply_intercepts.assert_not_awaited()


def test_compose_locales_have_real_friendly_prompt_and_bounded_placeholder():
    import yaml
    from agent.i18n import SUPPORTED_LANGUAGES, _locales_dir
    for language in SUPPORTED_LANGUAGES:
        values = yaml.safe_load((Path(_locales_dir()) / (language + '.yaml')).read_text())['gateway']['group_compose']
        assert 0 < len(values['placeholder']) <= 64
        assert '{group}' in values['title'] and values['prompt']
