"""Reply-specific group compose using the canonical typed Send boundary."""
import asyncio
import time

from gateway.group_chat_files import room_key
from gateway.group_chat_send import message_command_id, send_group_message
from gateway.group_chat_work import run_group_read
from gateway.group_home_consent import require_current
from gateway.native_reply_input import text


async def begin_compose(menu):
    room = await menu.current_room()
    expected = room_key(room)
    previous = getattr(menu, 'compose_request', None)
    if previous is not None:
        await asyncio.to_thread(previous.cancel)

    async def submitted(event, request):
        try:
            require_current(menu.runner, event, menu.stamp)
            rooms = await run_group_read(menu.backend.list_rooms)
            current = next((room for room in rooms if room_key(room) == expected), None)
            if current is None or request.deadline <= time.monotonic():
                return text('closed')
            if not event.text.strip() or event.media_urls or event.media_types or event.source.message_had_attachments:
                return text('text_only')
            command_id = message_command_id(event, current, menu.backend.authority.profile_id)
            prior = await asyncio.to_thread(request.claim, command_id)
            if prior is not None:
                return prior
            def guard():
                if request.deadline <= time.monotonic():
                    raise PermissionError('Compose request expired')
            result = await send_group_message(menu.runner, event, menu.backend, current, event.text, menu.stamp, extra_guard=guard)
            await asyncio.to_thread(request.finish, command_id, result)
            return result
        except Exception:
            return text('unknown')

    from gateway.hosted_room_messaging_presentation import _plain_display_label
    request, sent = await menu.adapter.send_reply_input(menu.event,
        text('title', group=_plain_display_label(room['name'])), submitted)
    if request is None or getattr(sent, 'success', False) is not True:
        return text('unavailable')
    try:
        await menu.current_room()
    except Exception:
        await asyncio.to_thread(request.cancel)
        raise
    menu.compose_request = request
    return await menu.room_page()
