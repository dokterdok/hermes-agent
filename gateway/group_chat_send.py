"""Send one explicitly addressed message through its receiving Home authority."""
import asyncio
import hashlib
import json

from gateway.group_chat_work import GroupChatMaintenanceError, require_command_open, run_group_command_work
from gateway.group_home_consent import require_current, DisclosureChanged
from gateway.hosted_room_messaging_presentation import _plain_display_label


def message_command_id(event, room, profile_id):
    message_id = getattr(event, 'message_id', None)
    if not isinstance(message_id, str) or not message_id:
        raise ValueError('The message has no delivery identity. Send a new message to try again.')
    source = event.source
    parts = [profile_id, source.platform.value, source.chat_id, source.thread_id,
             source.scope_id, source.user_id, message_id, room['room_id'],
             room['authority_gateway_id'], room['authority_epoch']]
    return 'message-' + hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


async def send_group_message(runner, event, backend, room, content, stamp):
    if not content or not content.strip():
        return 'Type a message after send.'
    if len(content) > 64 * 1024:
        return 'This message is too long. Shorten it or send it in smaller parts.'
    if getattr(event.source, 'message_had_attachments', None) is True or getattr(event, 'media_urls', None):
        return 'This command sends text only. Share files from Hermes Desktop for now.'
    try:
        command_id = message_command_id(event, room, backend.authority.profile_id)
    except ValueError as exc:
        return str(exc)
    try:
        name = _plain_display_label(event.source.user_name or 'You', limit=80)
        actor = {'kind': 'user', 'id': 'messaging:' + hashlib.sha256(json.dumps([
            event.source.platform.value, event.source.user_id]).encode()).hexdigest(),
            'display_name': name + ' via ' + event.source.platform.value.title()}
        def guard():
            require_command_open(runner)
            require_current(runner, event, stamp)
        await run_group_command_work(runner, 'send', lambda: backend.send(room=room, command_id=command_id,
            text=content, actor=actor, write_guard=guard))
        return 'Sent to ' + _plain_display_label(room['name'], limit=80) + '.'
    except GroupChatMaintenanceError as exc:
        return str(exc)
    except DisclosureChanged:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        return "I couldn't confirm whether that message was sent. Check the Group Chat before sending it again."
