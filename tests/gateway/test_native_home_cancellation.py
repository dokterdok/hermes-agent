"""Review only parent native continuation/menu cancellation; no live Send."""
import asyncio
from dataclasses import replace
from threading import Event
import time

import pytest

from gateway.choice_picker import ChoicePage
from gateway.hosted_room_messaging import MessagingRoomBackend
from gateway.native_reply_input import handle_native_reply, NativeReplySubmission
from tests.gateway.test_canonical_group_menu import native, choice
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401
from tests.gateway.test_receiving_home_consent import home, command, select_home  # noqa: F401


async def confirm_native(home):
    adapter = native(home)
    await select_home(home)
    assert await command(home, '!group') is None
    callback = adapter.picker['on_choice_selected']
    yes = adapter.picker['choices'][0]['value']
    page = await callback('42', yes)
    return adapter, callback, page


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['cancel', 'owner', 'scope', 'expired'])
async def test_forwarded_choices_keep_scope_after_confirmation_record_retires(home, change):
    adapter, callback, page = await confirm_native(home)
    assert isinstance(page, ChoicePage)
    assert not home.runner._group_home_confirmations
    menu = next(iter(home.runner._canonical_group_menus.values()))
    selected = page.choices[0]['value']
    if change == 'cancel':
        await command(home, '!group cancel')
    elif change == 'owner':
        home.consent(False)
    elif change == 'scope':
        home.event.source.scope_id = 'different-scope'
    else:
        menu.deadline = time.monotonic() - 1
    result = await callback('42', selected)
    assert not isinstance(result, ChoicePage)
    assert 'home secret' not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['cancel', 'owner', 'expired'])
async def test_native_first_page_withheld_when_pending_read_crosses_cancel_or_expiry(home, monkeypatch, change):
    adapter = native(home)
    await select_home(home)
    assert await command(home, '!group') is None
    pending = next(iter(home.runner._group_home_confirmations.values()))
    entered, release = Event(), Event()
    original = MessagingRoomBackend.list_rooms
    def delayed(*args):
        result = original(*args)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(MessagingRoomBackend, 'list_rooms', delayed)
    callback = adapter.picker['on_choice_selected']
    task = asyncio.create_task(callback('42', adapter.picker['choices'][0]['value']))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        if change == 'cancel':
            await command(home, '!group cancel')
        elif change == 'owner':
            home.consent(False)
        else:
            pending.deadline = time.monotonic() - 1
    finally:
        release.set()
    result = await task
    assert not isinstance(result, ChoicePage) and 'home secret' not in str(result)
    assert getattr(pending, 'menu', None) is None


@pytest.mark.asyncio
async def test_group_cancel_retires_compose_from_replaced_menu(view, monkeypatch):
    adapter = native(view)
    event = replace(view.event, text='/group 1', message_id='open')
    assert await view.runner._handle_group_command(event) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    await adapter.picker['on_choice_selected']('42', choice(page, 'Send message'))
    request = adapter.request
    assert request.pending
    assert await view.runner._handle_group_command(replace(event, message_id='reopen')) is None
    await view.runner._handle_group_command(replace(event, text='/group cancel', message_id='cancel'))
    attempts = []
    async def would_send(*args, **kwargs):
        attempts.append(True)
        return 'REVIEW_SEND_SENTINEL'
    monkeypatch.setattr('gateway.group_chat_compose.send_group_message', would_send)
    reply = replace(event, text='fixture draft', reply_to_message_id='prompt-123', message_id='late-reply')
    result = await handle_native_reply(view.runner, reply, NativeReplySubmission(adapter, request.token, True))
    assert not attempts, {'cancelled_draft_reached_send': bool(attempts), 'result': result}


@pytest.mark.asyncio
async def test_cancel_retires_new_confirmation_before_waiting_for_older_compose_io(home, monkeypatch):
    adapter, callback, page = await confirm_native(home)
    room_page = await callback('42', page.choices[0]['value'])
    await callback('42', choice(room_page, 'Send message'))
    old_request = adapter.request
    await select_home(home)
    assert await command(home, '!group') is None
    callback = adapter.picker['on_choice_selected']
    yes = adapter.picker['choices'][0]['value']
    read_entered, read_release = Event(), Event()
    cancel_entered, cancel_release = Event(), Event()
    original_read = MessagingRoomBackend.list_rooms
    def delayed_read(*args):
        result = original_read(*args)
        read_entered.set()
        assert read_release.wait(10)
        return result
    monkeypatch.setattr(MessagingRoomBackend, 'list_rooms', delayed_read)
    original_cancel = old_request.cancel
    def delayed_cancel():
        cancel_entered.set()
        assert cancel_release.wait(10)
        return original_cancel()
    monkeypatch.setattr(old_request, 'cancel', delayed_cancel)
    confirming = asyncio.create_task(callback('42', yes))
    assert await asyncio.to_thread(read_entered.wait, 5)
    cancelling = asyncio.create_task(command(home, '!group cancel'))
    try:
        assert await asyncio.to_thread(cancel_entered.wait, 5)
        read_release.set()
        result = await asyncio.wait_for(confirming, timeout=3)
        cancellation_was_waiting = not cancelling.done()
    finally:
        read_release.set()
        cancel_release.set()
        await asyncio.gather(confirming, cancelling, return_exceptions=True)
    labels = [item['label'] for item in result.choices] if isinstance(result, ChoicePage) else []
    assert not isinstance(result, ChoicePage), {'cancel_waiting': cancellation_was_waiting, 'disclosed_labels': labels}
