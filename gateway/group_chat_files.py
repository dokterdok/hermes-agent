"""Canonical Files browsing and native delivery over the existing owner catalog."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import secrets
import time

from gateway.group_chat_messages import text
from gateway.group_chat_policy import receiving_group_context
from gateway.group_chat_work import run_group_read
from gateway.group_home_consent import require_current
from gateway.hosted_room_file_contract import FileAccessError, catalog_options
from gateway.hosted_room_file_lookup import resolve_file, selection_digest
from gateway.hosted_room_messaging_presentation import _plain_display_label, room_reference

PAGE_SIZE = 8
MAX_MENUS = 128
PAGE_SECONDS = 120


def room_key(room):
    return tuple(room.get(key) for key in ('room_id', 'authority_gateway_id', 'authority_epoch', '_room_mode', '_remote_member_id'))


def source_key(backend, event, stamp):
    source = event.source
    values = [backend.authority.profile_id, source.platform.value, source.user_id, source.chat_id,
              source.thread_id, source.scope_id, stamp[4]]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


def delivery_keys(runner, event, backend, stamp, selection):
    from gateway.hosted_room_file_delivery import delivery_identity, FileDeliveryError
    message_id = getattr(event, 'message_id', None)
    if not isinstance(message_id, str) or not message_id:
        raise FileDeliveryError('file_invalid_request')
    rates = getattr(runner, '_canonical_file_send_rates', None)
    if rates is None:
        rates = runner._canonical_file_send_rates = OrderedDict()
    source = source_key(backend, event, stamp)
    now = time.monotonic()
    recent = [sent for sent in rates.pop(source, []) if now - sent < 60]
    rates[source] = recent
    if len(recent) >= 6:
        raise FileDeliveryError('rate')
    recent.append(now)
    while len(rates) > 2048:
        rates.popitem(last=False)
    return delivery_identity(source, message_id, selection)


def error_message(exc):
    key = {
        'file_code_ambiguous': 'ambiguous', 'file_too_large': 'large', 'too_large': 'large',
        'file_integrity_failed': 'integrity', 'file_unavailable': 'removed',
        'file_invalid_request': 'invalid', 'file_access_denied': 'denied',
        'file_access_unsupported': 'unsupported', 'unsupported': 'unsupported',
        'attachment_cursor_reset_required': 'cursor_reset', 'file_lookup_limit': 'lookup_limit',
        'rate': 'rate',
    }.get(getattr(exc, 'code', str(exc)), 'error')
    return text('group_files', key)


def file_lines(room, item, command):
    name = _plain_display_label(item['name'], limit=100)
    producer = _plain_display_label(item['producer']['label'], limit=60)
    size = f"{item['size'] / 1_000_000:.1f} MB" if item['size'] >= 1_000_000 else f"{max(1, round(item['size'] / 1000))} KB"
    date = datetime.fromtimestamp(item['shared_at'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    code = selection_digest(room, item)[:12]
    icons = {'application/pdf': '📕', 'text/csv': '📊', 'application/zip': '📦',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '📊'}
    icon = icons.get(item['mime']) or {'image': '🖼', 'audio': '🎵', 'video': '🎬', 'text': '📄'}.get(item['mime'].split('/')[0], '📎')
    return [f'• {icon} **{name}**', f'{producer} · {date} · {size}',
            f"{text('group_files', 'download')}: `{command} {room_reference(room)} file {code}`", '']


@dataclass
class FilePages:
    stamp: tuple
    room: dict
    query: str
    deadline: float = field(default_factory=lambda: time.monotonic() + PAGE_SECONDS)
    handle: str = field(default_factory=lambda: secrets.token_hex(8))
    pages: list = field(default_factory=list)
    position: int = 0
    first_page: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def menus(runner):
    pages = getattr(runner, '_canonical_file_pages', None)
    if pages is None:
        pages = runner._canonical_file_pages = OrderedDict()
    for handle in list(pages):
        if pages[handle].deadline <= time.monotonic():
            pages.pop(handle)
    return pages


async def browse_files(runner, event, backend, room, query, command, stamp, *, render=None):
    table = menus(runner)
    parts = query.split()
    if parts and parts[0] == '--page':
        if len(parts) != 3 or not parts[2].isascii() or not parts[2].isdecimal():
            return text('group_files', 'expired')
        state = table.get(parts[1])
        if state is None or state.stamp != stamp or room_key(state.room) != room_key(room):
            return text('group_files', 'expired')
        position = int(parts[2]) - 1
    else:
        catalog_options({'query': query})
        state, position = FilePages(stamp, dict(room), query), 0
    async with state.lock:
        require_current(runner, event, stamp)
        if state.deadline <= time.monotonic():
            return text('group_files', 'expired')
        offset = position - state.first_page
        if not 0 <= offset <= len(state.pages):
            return text('group_files', 'expired')
        if offset == len(state.pages):
            previous = state.pages[-1] if state.pages else None
            if previous and not previous['has_more']:
                return text('group_files', 'no_older')
            page = await run_group_read(lambda: backend.list_files(room=room, query=state.query,
                cursor=previous['next_cursor'] if previous else None, limit=PAGE_SIZE))
            state.pages.append(page)
            if len(state.pages) > 8:
                state.pages.pop(0)
                state.first_page += 1
                offset -= 1
        page = state.pages[offset]
        # Cached pages retain order, not authorization to an expired/removed version.
        items = []
        for item in page['items']:
            try:
                checked = await run_group_read(lambda: backend.resolve_file(room=room, code=selection_digest(room, item)))
            except FileAccessError as exc:
                if exc.code in {'file_unavailable', 'file_access_denied'}:
                    continue
                raise
            items.append(checked)
        require_current(runner, event, stamp)
        if state.deadline <= time.monotonic():
            return text('group_files', 'expired')
        state.position = position
        table[state.handle] = state
        table.move_to_end(state.handle)
        while len(table) > MAX_MENUS:
            table.popitem(last=False)
        lines = [text('group_files', 'title', name=_plain_display_label(room['name'])),
                 f'Page {position + 1}', '']
        for item in items:
            lines += file_lines(room, item, command)
        if not items:
            lines += [text('group_files', 'no_match' if state.query else 'empty'), '']
        for index in ([position - 1] if position > state.first_page else []) + ([position + 1] if page['has_more'] else []):
            lines += [f"{text('group_presentation', 'go_to_page', page=index + 1)}: "
                      f'`{command} {room_reference(room)} files --page {state.handle} {index + 1}`']
        if items or state.query or page['has_more']:
            lines += ['', text('group_files', 'search_with', command=f'`{command} {room_reference(room)} files <query>`')]
        lines += ['', f"{text('group_files', 'view_group')}: `{command} {room_reference(room)}`"]
        if render is not None:
            return render(state, page, items, lines)
        return '\n'.join(lines)


async def get_file(runner, event, backend, room, code, stamp):
    from gateway.hosted_room_file_delivery import Document, deliver_document, native_document_limit
    from gateway.platforms.base import _thread_metadata_for_event
    context = receiving_group_context(runner, event.source)
    if context is None:
        return text('group_files', 'denied')
    message_id = getattr(event, 'message_id', None)
    if not isinstance(message_id, str) or not message_id:
        return text('group_files', 'invalid')
    maximum = native_document_limit(context.adapter, event.source)
    parts = code.split()
    if not 1 <= len(parts) <= 2 or (len(parts) == 2 and parts[1] != 'confirm'):
        return text('group_files', 'invalid')
    item = await run_group_read(lambda: backend.resolve_file(room=room, code=parts[0]))
    exact = selection_digest(room, item)
    if item['size'] > maximum:
        return text('group_files', 'large')
    if item['size'] > 10_000_000 and (len(parts) != 2 or parts[0] != exact):
        from gateway.group_chat_policy import group_command_prefix
        command = group_command_prefix(runner, event.source) + 'group'
        return text('group_files', 'confirm_send', name=_plain_display_label(item['name']), size=f"{item['size']/1_000_000:.1f} MB") + '\n\n' + text(
            'group_files', 'confirm_command', command=f'`{command} {room_reference(room)} file {exact} confirm`')
    key, scope = delivery_keys(runner, event, backend, stamp, exact)

    def load(maximum):
        require_current(runner, event, stamp)
        saved = backend.read_file(room=room, event_id=item['event_id'], attachment_id=item['attachment_id'], max_bytes=maximum)
        if any(saved.attachment[field] != item[field] for field in ('attachment_id', 'event_id', 'name', 'mime', 'kind', 'size')):
            raise PermissionError('File changed')
        return Document(saved.attachment['name'], saved.data)

    async def recheck():
        require_current(runner, event, stamp)
        current = await run_group_read(lambda: backend.resolve_file(room=room, code=exact))
        if current != item:
            raise PermissionError('File changed')
        require_current(runner, event, stamp)

    result = await deliver_document(db_path=backend.db_path, key=key, scope=scope, adapter=context.adapter,
        source=event.source, load=load, recheck=recheck, metadata=_thread_metadata_for_event(event) or {}, reply_to=message_id)
    require_current(runner, event, stamp)
    return text('group_files', result if result in {'delivered', 'unknown', 'busy', 'failed'} else 'error')
