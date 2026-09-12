"""Canonical Group Chat pages on existing, requester-bound native pickers."""
import time

from gateway.choice_picker import ChoicePage, ChoiceProgress, PAGE_TIMEOUT_SECONDS
from gateway.group_chat_files import browse_files, file_lines, get_file, room_key, error_message
from gateway.group_chat_messages import text
from gateway.group_chat_policy import receiving_group_context
from gateway.group_chat_work import run_group_read
from gateway.group_home_consent import require_current
from gateway.hosted_room_file_lookup import selection_digest
from gateway.hosted_room_file_contract import FileAccessError
from gateway.hosted_room_messaging_presentation import (
    _plain_display_label, format_room_detail, format_room_bots, room_reference,
)


class GroupMenu:
    def __init__(self, runner, event, backend, command, stamp):
        context = receiving_group_context(runner, event.source)
        self.runner, self.event, self.backend, self.command, self.stamp = runner, event, backend, command, stamp
        self.adapter = context.adapter
        self.deadline = time.monotonic() + PAGE_TIMEOUT_SECONDS
        self.room = None
        self.actions = {}
        self.revision = 0
        self.files_query = ''
        active = getattr(runner, '_canonical_group_menus', None)
        if active is None:
            active = runner._canonical_group_menus = {}
        for key in list(active):
            if active[key].deadline <= time.monotonic():
                active.pop(key)
        previous = active.pop(stamp, None)
        if previous is not None:
            previous.deadline = 0
        active[stamp] = self
        while len(active) > 128:
            active.pop(next(iter(active))).deadline = 0

    def check(self):
        require_current(self.runner, self.event, self.stamp)
        if self.deadline <= time.monotonic():
            raise TimeoutError('Menu expired')

    def page(self, title, actions):
        self.check()
        self.revision += 1
        self.actions = {f'{self.revision}:{index}': action for index, (_, action) in enumerate(actions)}
        return ChoicePage(title[:2048], [{'value': value, 'label': actions[index][0], 'full_width': True}
            for index, value in enumerate(self.actions)])

    async def current_room(self):
        self.check()
        if self.room is None:
            rooms = await run_group_read(self.backend.list_rooms)
            self.check()
            return rooms
        current = await run_group_read(lambda: self.backend.check(self.room))
        self.check()
        if isinstance(current, dict):
            self.room = {**self.room, **current}
        return self.room

    async def groups(self, index=0, rooms=None):
        self.room = None
        if rooms is None:
            rooms = await self.current_room()
        if not rooms:
            return text('group_files', 'all_empty')
        count = (len(rooms) + 7) // 8
        if not 0 <= index < count:
            return text('group_files', 'expired')
        actions = [(f"{room_reference(room)}. {_plain_display_label(room['name'], limit=60)} · {len(room.get('members', []))} Bots",
                    ('room', room_key(room))) for room in rooms[index * 8:(index + 1) * 8]]
        if index:
            actions.append((text('group_presentation', 'go_to_page', page=index), ('groups', index - 1)))
        if index + 1 < count:
            actions.append((text('group_presentation', 'go_to_page', page=index + 2), ('groups', index + 1)))
        return self.page('👥 **Group Chats**\n\n' + text('group_presentation', 'page', current=index + 1, total=count), actions)

    async def room_page(self, kind='room', selected=None):
        room = await self.current_room()
        actions = []
        summary = await run_group_read(lambda: self.backend.summary(room))
        if (kind == 'room' and 'send' in summary.get('control_actions', [])
                and getattr(type(self.adapter), 'supports_reply_input', False) is True):
            actions.append((text('group_presentation', 'send_message'), ('compose', None)))
        try:
            pending = await run_group_read(lambda: self.backend.approvals(room))
        except Exception:
            pending = []
        if pending:
            actions.append((text('group_files', 'approvals'), ('approvals', 0)))
        if kind == 'room':
            title = format_room_detail(self.backend, room, self.command, native=True, snapshot=summary)
        else:
            title = format_room_bots(self.backend, room, self.command, selected=selected, native=True, snapshot=summary)
        if kind == 'bots':
            for member in summary['room']['members']:
                actions.append((text('group_presentation', 'view_bot') + ' · ' + _plain_display_label(
                    member.get('display_name') or member['handle'], limit=64), ('bot', '@' + member['handle'])))
        try:
            files = await run_group_read(lambda: self.backend.list_files(room=room, limit=1))
        except FileAccessError:
            files = {'items': [], 'has_more': False}
        if files['items'] or files['has_more']:
            actions.append((text('group_files', 'files'), ('files', '')))
        if kind == 'room':
            try:
                reply = await run_group_read(lambda: self.backend.latest_reply(room=room))
            except FileAccessError:
                reply = None
            if reply and len(reply['text']) > 180:
                actions.append((text('group_files', 'full_reply'), ('reply', reply)))
        if kind != 'bots':
            actions.append((text('group_presentation', 'view_bots'), ('bots', None)))
        if kind != 'room':
            actions.append((text('group_files', 'view_group'), ('room', room_key(room))))
        compose = getattr(self, 'compose_request', None)
        if compose is not None and compose.pending and compose.deadline > time.monotonic():
            actions.append(('Cancel message', ('cancel_compose', None)))
        actions.append(('‹ ' + text('group_files', 'groups'), ('groups', 0)))
        await self.current_room()
        return self.page(title, actions)

    async def files(self, query=''):
        room = await self.current_room()
        self.files_query = query
        def render(state, page, items, lines):
            actions = [(text('group_files', 'download') + ' ' + _plain_display_label(item['name'], limit=64),
                        ('file', dict(item))) for item in items]
            if state.position > state.first_page:
                actions.append((text('group_presentation', 'go_to_page', page=state.position),
                                ('files', f'--page {state.handle} {state.position}')))
            if page['has_more']:
                actions.append((text('group_presentation', 'go_to_page', page=state.position + 2),
                                ('files', f'--page {state.handle} {state.position + 2}')))
            actions += [(text('group_files', 'show_latest'), ('files', state.query)),
                        (text('group_files', 'view_group'), ('room', room_key(room)))]
            title = [text('group_files', 'title', name=_plain_display_label(room['name'])), '']
            for item in items:
                title += file_lines(room, item, self.command)[:2] + ['']
            if not items:
                title += [text('group_files', 'no_match' if state.query else 'empty')]
            return self.page('\n'.join(title), actions)
        return await browse_files(self.runner, self.event, self.backend, room, query, self.command, self.stamp, render=render)

    async def download(self, item, *, confirmed=False):
        from gateway.hosted_room_file_delivery import native_document_limit
        room = await self.current_room()
        maximum = native_document_limit(self.adapter, self.event.source)
        if item['size'] > maximum:
            return text('group_files', 'large')
        if item['size'] > 10_000_000 and not confirmed:
            return self.page(text('group_files', 'confirm_send', name=_plain_display_label(item['name']),
                size=f"{item['size']/1_000_000:.1f} MB"), [(text('group_files', 'send'), ('file_confirm', item)),
                                                       (text('group_files', 'cancel'), ('files', self.files_query))])
        async def complete():
            try:
                current = await self.current_room()
                code = selection_digest(current, item) + (' confirm' if confirmed else '')
                outcome = await get_file(self.runner, self.event, self.backend, current, code, self.stamp)
                return self.page(outcome, [(text('group_files', 'back_files'), ('files', self.files_query)),
                                           (text('group_files', 'view_group'), ('room', room_key(current)))])
            except Exception as exc:
                return error_message(exc)
        return ChoiceProgress(text('group_files', 'getting'), complete)

    async def select_room(self, key):
        self.room = None
        rooms = await self.current_room()
        self.room = next((room for room in rooms if room_key(room) == key), None)
        if self.room is None:
            raise PermissionError('Group Chat unavailable')
        return await self.room_page()

    async def reply(self, selected):
        from gateway.group_chat_reply_file import get_reply
        async def complete():
            try:
                room = await self.current_room()
                result = await get_reply(self.runner, self.event, self.backend, room, self.stamp, selected=selected)
                return self.page(result, [(text('group_files', 'view_group'), ('room', room_key(room)))])
            except Exception as exc:
                return error_message(exc)
        return ChoiceProgress(text('group_files', 'getting'), complete)

    async def approvals(self, index=0):
        from gateway.group_chat_decisions import decision_code
        from gateway.hosted_room_messaging_presentation import _plain_preview_text
        room = await self.current_room()
        pending = await run_group_read(lambda: self.backend.approvals(room))
        if not pending:
            return self.page(text('group_files', 'no_approvals'), [(text('group_files', 'view_group'), ('room', room_key(room)))])
        index = min(max(0, index), len(pending) - 1)
        item = pending[index]
        code = decision_code(item)
        title = '\n\n'.join([text('group_files', 'approval_position', current=index + 1, total=len(pending)),
            '**' + _plain_display_label(item['member_id']) + '**',
            _plain_preview_text(item['description'], limit=512), _plain_preview_text(item['command'], limit=512)])
        actions = [('Allow once', ('decide', (code, 'once'))), ('Deny', ('decide', (code, 'deny')))]
        if index:
            actions.append((text('group_files', 'approval_previous'), ('approvals', index - 1)))
        if index + 1 < len(pending):
            actions.append((text('group_files', 'approval_next'), ('approvals', index + 1)))
        actions.append((text('group_files', 'view_group'), ('room', room_key(room))))
        return self.page(title, actions)

    async def decide(self, code, choice):
        from gateway.group_chat_decisions import decide_from_chat
        room = await self.current_room()
        result = await decide_from_chat(self.runner, self.event, self.backend, room, code, choice, self.stamp)
        return self.page(result, [(text('group_files', 'approvals'), ('approvals', 0)),
                                  (text('group_files', 'view_group'), ('room', room_key(room)))])

    async def choose(self, chat_id, value):
        try:
            self.check()
            if str(chat_id) != str(self.event.source.chat_id) or value not in self.actions:
                return text('group_files', 'expired')
            kind, payload = self.actions[value]
            self.actions = {}
            limited = self.runner._group_chat_rate_limit_denial(self.event)
            if limited:
                return limited
            from gateway.group_chat_compose import begin_compose
            async def cancel_compose():
                import asyncio
                await asyncio.to_thread(self.compose_request.cancel)
                return await self.room_page()
            handlers = {'groups': lambda: self.groups(payload), 'room': lambda: self.select_room(payload),
                'bots': lambda: self.room_page('bots'), 'bot': lambda: self.room_page('bot', payload),
                'files': lambda: self.files(payload), 'file': lambda: self.download(payload),
                'file_confirm': lambda: self.download(payload, confirmed=True),
                'reply': lambda: self.reply(payload),
                'approvals': lambda: self.approvals(payload), 'decide': lambda: self.decide(*payload),
                'compose': lambda: begin_compose(self), 'cancel_compose': cancel_compose}
            return await handlers[kind]()
        except (PermissionError, TimeoutError):
            return text('group_files', 'expired')
        except Exception as exc:
            return error_message(exc)


async def show_group_menu(runner, event, backend, command, stamp, *, room=None, view='room', selected=None, rooms=None):
    context = receiving_group_context(runner, event.source)
    if context is None or getattr(type(context.adapter), 'supports_choice_pages', False) is not True:
        return False
    menu = GroupMenu(runner, event, backend, command, stamp)
    if room is None:
        page = await menu.groups(rooms=rooms)
    else:
        menu.room = room
        if view == 'files':
            page = await menu.files(selected or '')
        elif view == 'approvals':
            page = await menu.approvals()
        else:
            page = await menu.room_page(view, selected)
    if not isinstance(page, ChoicePage):
        return False
    from gateway.platforms.base import _thread_metadata_for_event
    menu.check()
    result = await context.adapter.send_choice_picker(chat_id=event.source.chat_id, title=page.title,
        choices=list(page.choices), session_key='group-menu', on_choice_selected=menu.choose,
        metadata={**(_thread_metadata_for_event(event) or {}), 'choice_pages': True,
                  'requester_user_id': str(event.source.user_id)})
    menu.check()
    return getattr(result, 'success', False) is True


async def cancel_navigation(runner, event):
    import asyncio
    context = receiving_group_context(runner, event.source)
    if context is None:
        return
    location = (str(event.source.user_id), str(event.source.chat_id), str(event.source.thread_id or ''), str(event.source.scope_id or ''))
    active = getattr(runner, '_canonical_group_menus', {})
    for key, menu in list(active.items()):
        if key[0] != str(context.home) or key[5:9] != location:
            continue
        menu.deadline = 0
        menu.actions.clear()
        active.pop(key, None)
        request = getattr(menu, 'compose_request', None)
        if request is not None:
            request.deadline = 0
            request.pending = False
            await asyncio.to_thread(request.cancel)
