"""Bounded cross-room merge of existing snapshot catalogs, adapted from #104199."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import secrets
import time

from gateway.group_chat_files import PAGE_SIZE, PAGE_SECONDS, file_lines, room_key
from gateway.group_chat_messages import text
from gateway.group_home_consent import require_current
from gateway.hosted_room_file_contract import FileAccessError, catalog_options
from gateway.hosted_room_file_lookup import selection_digest
from gateway.hosted_room_messaging_presentation import _plain_display_label


@dataclass
class AllFilePages:
    stamp: tuple
    query: str
    handle: str = field(default_factory=lambda: secrets.token_hex(8))
    deadline: float = field(default_factory=lambda: time.monotonic() + PAGE_SECONDS)
    streams: dict = field(default_factory=dict)
    pages: list = field(default_factory=list)
    initialized: bool = False
    incomplete: bool = False
    first_page: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def _read(runner, operation):
    semaphore = getattr(runner, '_canonical_all_file_slots', None)
    if semaphore is None:
        semaphore = runner._canonical_all_file_slots = asyncio.Semaphore(4)
    await semaphore.acquire()
    job = asyncio.create_task(asyncio.to_thread(operation))
    def finished(task):
        semaphore.release()
        if not task.cancelled():
            task.exception()
    job.add_done_callback(finished)
    return await asyncio.shield(job)


async def _fill(runner, backend, state):
    selected = [stream for stream in state.streams.values()
                if not stream['items'] and (not stream['loaded'] or stream['cursor'])]
    results = await asyncio.gather(*[
        _read(runner, lambda stream=stream: backend.list_files(room=stream['room'],
            query=state.query, cursor=stream['cursor'], limit=PAGE_SIZE)) for stream in selected], return_exceptions=True)
    for stream, result in zip(selected, results):
        if isinstance(result, BaseException):
            if stream['loaded'] and stream['cursor'] and getattr(result, 'code', None) not in {
                    'file_access_denied', 'file_unavailable', 'attachment_cursor_reset_required'}:
                raise FileAccessError('file_host_unavailable', retryable=True)
            stream['loaded'], stream['cursor'] = True, None
            state.incomplete = True
        else:
            stream.update(loaded=True, items=list(result['items']), cursor=result['next_cursor'] if result['has_more'] else None)


async def _construct(runner, event, backend, rooms, state, position, command, stamp):
    current = {room_key(room): room for room in rooms}
    if not state.initialized:
        state.streams = {key: dict(room=room, items=[], cursor=None, loaded=False) for key, room in current.items()}
        state.initialized = True
    for key in list(state.streams):
        if key not in current:
            del state.streams[key]
            state.incomplete = True
        else:
            state.streams[key]['room'] = current[key]
    offset = position - state.first_page
    if not 0 <= offset <= len(state.pages):
        raise TimeoutError('expired')
    if offset == len(state.pages):
        if state.pages and not state.pages[-1]['has_more']:
            raise TimeoutError('expired')
        rows = []
        for _ in range(PAGE_SIZE):
            await _fill(runner, backend, state)
            require_current(runner, event, stamp)
            row = min(((stream['room'], item) for stream in state.streams.values() for item in stream['items']),
                key=lambda row: (-row[1]['shared_at'], row[0]['room_id'], -row[1]['seq'], row[1]['attachment_id']), default=None)
            if row is None:
                break
            room, item = row
            state.streams[room_key(room)]['items'].remove(item)
            rows.append(row)
        state.pages.append(dict(rows=rows, has_more=any(stream['items'] or stream['cursor'] for stream in state.streams.values())))
        if len(state.pages) > 8:
            state.pages.pop(0)
            state.first_page += 1
            offset -= 1
    page = state.pages[offset]
    lines = ['**' + text('group_files', 'all_title') + '**', f'Page {position + 1}', '']
    shown = 0
    for room, item in page['rows']:
        if room_key(room) not in current:
            state.incomplete = True
            continue
        try:
            checked = await _read(runner, lambda: backend.resolve_file(room=room, code=selection_digest(room, item)))
        except (FileAccessError, PermissionError):
            state.incomplete = True
            continue
        lines += [text('group_files', 'group_from', name=_plain_display_label(room['name']))]
        lines += file_lines(room, checked, command)
        shown += 1
    require_current(runner, event, stamp)
    if not shown:
        lines += [text('group_files', 'all_empty'), '']
    if state.incomplete:
        lines += [text('group_files', 'some_unavailable'), '']
    for index in ([position - 1] if position > state.first_page else []) + ([position + 1] if page['has_more'] else []):
        lines += [f"{text('group_presentation', 'go_to_page', page=index + 1)}: `"
                  f'{command} files --page {state.handle} {index + 1}`']
    lines += ['', text('group_files', 'all_search', command=f'`{command} files <query>`')]
    return '\n'.join(lines)


async def browse_all_files(runner, event, backend, rooms, query, command, stamp):
    table = getattr(runner, '_canonical_all_file_pages', None)
    if table is None:
        table = runner._canonical_all_file_pages = OrderedDict()
    for key in list(table):
        if table[key].deadline <= time.monotonic():
            table.pop(key)
    parts = query.split()
    if parts and parts[0] == '--page':
        if len(parts) != 3 or not parts[2].isascii() or not parts[2].isdecimal():
            return text('group_files', 'expired')
        state = table.get(parts[1])
        if state is None or state.stamp != stamp:
            return text('group_files', 'expired')
        position = int(parts[2]) - 1
    else:
        catalog_options({'query': query})
        state, position = AllFilePages(stamp, query), 0
    async with state.lock:
        if state.deadline <= time.monotonic():
            return text('group_files', 'expired')
        # Failed partial refills never consume a cursor or cached page position.
        previous = state.streams, state.pages, state.initialized, state.incomplete, state.first_page
        state.streams = {key: {**stream, 'items': list(stream['items'])} for key, stream in state.streams.items()}
        state.pages = list(state.pages)
        try:
            result = await asyncio.wait_for(_construct(runner, event, backend, rooms, state, position, command, stamp),
                                             min(20, state.deadline - time.monotonic()))
            require_current(runner, event, stamp)
        except BaseException:
            state.streams, state.pages, state.initialized, state.incomplete, state.first_page = previous
            raise
        table[state.handle] = state
        table.move_to_end(state.handle)
        while len(table) > 8:
            table.popitem(last=False)
        return result
