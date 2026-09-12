"""Read-only #98073 Group Chat commands on canonical owner/Home consent."""
import hashlib
import json
import time

from gateway.group_chat_messages import text
from gateway.group_chat_policy import group_command_prefix, receiving_group_context
from gateway.group_chat_work import run_group_read
from gateway.group_home_consent import denial, disclosure_stamp, protect_group_result, require_current
from gateway.group_home_identity import private_event
from gateway.hosted_room_file_contract import FileAccessError
from gateway.hosted_room_messaging import current_room_backend
from gateway.hosted_room_messaging_presentation import (
    RoomControlError, _plain_display_label, format_room_bots, format_room_detail,
    format_room_list, resolve_room,
)

_MUTATIONS = frozenset({'stop', 'retry', 'approve', 'deny', 'discard', 'remember', 'forget', 'confirm'})


def _picker_value(room):
    identity = [room['room_id'], room['authority_gateway_id'], room['authority_epoch'], room.get('_remote_member_id')]
    return 'room-' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]


class GroupChatSlashCommandsMixin:
    @staticmethod
    def _group_chat_command_args(event):
        parts = str(event.text or '').lstrip().split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ''

    @staticmethod
    def _group_chat_help(command):
        return '\n'.join(['**Group Chats**', '', f'`{command}` - Choose a Group Chat.',
            text('group_presentation', 'help_pages', command=f'`{command} list <page>`'),
            f'`{command} 7` - Check recent activity.', f"`{command} 7 bots` - See who's in the group.",
            f'`{command} 7 bot <number>` - View a Bot.',
            text('group_files', 'help_find', command=f'`{command} 7 files [query]`'), '',
            f'`{command} files [query]` - Find files across your Group Chats.', '',
            text('group_files', 'help_get', command=f'`{command} 7 file <file-id>`'), '',
            f'`{command} 7 send <message>` - Send a message to the group.', '',
            "Replace 7 with the Group Chat's number from the list.",
            'Only rooms shared with this Home are visible. Stop and Retry are not enabled here yet.'])

    def _group_chat_rate_limit_denial(self, event):
        stamp = disclosure_stamp(self, event)
        buckets = getattr(self, '_group_chat_command_rate_buckets', None)
        if buckets is None:
            buckets = self._group_chat_command_rate_buckets = {}
        now = time.monotonic()
        recent = [seen for seen in buckets.get(stamp, ()) if now - seen < 60]
        if len(recent) >= 30:
            return 'Too many Group Chat commands. Wait a moment and try again.'
        recent.append(now)
        buckets[stamp] = recent
        while len(buckets) > 2048:
            buckets.pop(next(iter(buckets)))
        return None

    async def _handle_group_command(self, event):
        return await self._handle_rooms_command(event)

    @protect_group_result
    async def _handle_rooms_command(self, event):
        command = group_command_prefix(self, event.source) + 'group'
        query = self._group_chat_command_args(event).strip()
        if query.casefold() in {'help', '?', 'usage'}:
            return self._group_chat_help(command)
        words = query.split(maxsplit=2)
        if (words and words[0].casefold() in _MUTATIONS) or (len(words) > 1 and words[1].casefold() in _MUTATIONS):
            return 'Group Chat changes from messaging are not available yet. Use Hermes Desktop.'
        if query.casefold() == 'cancel':
            getattr(self, '_group_read_choice_tokens', {}).pop(disclosure_stamp(self, event), None)
            return text('group_home', 'cancel', command_prefix=group_command_prefix(self, event.source))
        limited = self._group_chat_rate_limit_denial(event)
        if limited:
            return limited
        stamp = disclosure_stamp(self, event)
        backend = current_room_backend(self, event, stamp)
        try:
            rooms = await run_group_read(backend.list_rooms)
            require_current(self, event, stamp)
            if words and words[0].casefold() == 'files':
                from gateway.group_chat_all_files import browse_all_files
                return await browse_all_files(self, event, backend, rooms, query[len(words[0]):].strip(), command, stamp)
            if not query and await self._group_read_picker(event, backend, rooms, command, stamp):
                return None
            if not words or words[0].casefold() == 'list':
                if len(words) > 2 or (len(words) == 2 and not words[1].isdecimal()):
                    return f'Use `{command} list [page]`.'
                page = int(words[1]) if len(words) == 2 else 1
                return await run_group_read(lambda: format_room_list(backend, rooms, command, page))
            if len(words) > 1 and words[0].isdecimal():
                room = resolve_room(rooms, words[0])
                kind = words[1].casefold()
                argument = words[2] if len(words) == 3 else ''
                if kind == 'send':
                    from gateway.group_chat_send import send_group_message
                    return await send_group_message(self, event, backend, room, argument, stamp)
                if kind in {'files', 'file'}:
                    from gateway.group_chat_files import browse_files, get_file, error_message
                    try:
                        if kind == 'files':
                            return await browse_files(self, event, backend, room, argument, command, stamp)
                        return await get_file(self, event, backend, room, argument, stamp)
                    except Exception as exc:
                        return error_message(exc)
                handlers = {
                    'bots': lambda: format_room_bots(backend, room, command),
                    'bot': lambda: format_room_bots(backend, room, command, selected=argument),
                }
                if kind not in handlers:
                    return self._group_chat_help(command)
                return await run_group_read(handlers[kind])
            room = resolve_room(rooms, query)
            return await run_group_read(lambda: format_room_detail(backend, room, command))
        except RoomControlError as exc:
            return str(exc)
        except FileAccessError as exc:
            return text('group_files', 'unavailable' if exc.code == 'file_access_unsupported' else 'denied' if exc.code == 'file_access_denied' else 'error')
        except PermissionError:
            return denial(self, event)
        except Exception:
            return 'Couldn’t load Group Chats. Try again in a moment.'

    async def _group_read_picker(self, event, backend, rooms, command, stamp):
        context = receiving_group_context(self, event.source)
        if not rooms or not private_event(event) or context is None:
            return False
        picker = getattr(type(context.adapter), 'send_choice_picker', None)
        if picker is None:
            return False
        visible = rooms[:8]
        values = {_picker_value(room) for room in visible}
        tokens = getattr(self, '_group_read_choice_tokens', None)
        if tokens is None:
            tokens = self._group_read_choice_tokens = {}
        token = object()
        tokens[stamp] = token
        while len(tokens) > 2048:
            tokens.pop(next(iter(tokens)))
        deadline = time.monotonic() + 120
        choices = [{'value': _picker_value(room),
                    'label': f"{room['messaging_ref']}. {_plain_display_label(room['name'], limit=42)} ({len(room.get('members', []))})",
                    'full_width': True, 'is_current': False} for room in visible]

        async def selected(chat_id, value):
            try:
                if (str(chat_id) != str(event.source.chat_id) or value not in values
                        or tokens.get(stamp) is not token or time.monotonic() >= deadline):
                    return denial(self, event)
                limited = self._group_chat_rate_limit_denial(event)
                if limited:
                    return limited
                require_current(self, event, stamp)
                current = await run_group_read(backend.list_rooms)
                room = next((item for item in current if _picker_value(item) == value), None)
                if room is None:
                    return 'This Group Chat is no longer available. Run the command again.'
                result = await run_group_read(lambda: format_room_detail(backend, room, command))
                require_current(self, event, stamp)
                if tokens.get(stamp) is not token or time.monotonic() >= deadline:
                    return denial(self, event)
                return result
            except Exception:
                return denial(self, event)
            finally:
                if tokens.get(stamp) is token:
                    tokens.pop(stamp, None)

        from gateway.platforms.base import _thread_metadata_for_event
        require_current(self, event, stamp)
        result = await picker(context.adapter, chat_id=event.source.chat_id, title='👥 Group Chats\nChoose a Group Chat.',
            choices=choices, session_key='group-read:' + hashlib.sha256(repr(stamp).encode()).hexdigest(),
            on_choice_selected=selected, metadata=_thread_metadata_for_event(event))
        require_current(self, event, stamp)
        return getattr(result, 'success', False) is True
