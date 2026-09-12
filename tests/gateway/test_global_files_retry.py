"""Immediate catalog response checks; no live transport or held worker."""
from dataclasses import replace
import re
import time

import pytest

from gateway import hosted_rooms, hosted_room_controls as controls
from gateway.hosted_room_control_client import RoomControlHTTPClient, RoomControlClientError
from gateway.hosted_room_file_lookup import selection_digest
from gateway.session_authorities import owner_scope
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401


@pytest.mark.asyncio
async def test_temporary_peer_summary_failure_preserves_global_cursor(view, monkeypatch):
    remote = {'room_id': 'remote-room', 'name': 'Remote files', 'authority_gateway_id': 'remote-home',
              'authority_epoch': 1, 'members': [{'member_id': 'peer', 'profile': 'home', 'handle': 'peer'}]}
    with owner_scope(view.receiving):
        hosted_rooms.reserve_peer_room(view.receiving.db.db_path, claims={
            'room_id': 'remote-room', 'member_id': 'peer', 'target_profile': 'home',
            'authority_gateway_id': 'remote-home', 'authority_epoch': 1}, expires_at=time.time() + 3600)
        controls.save_peer_control_link(view.receiving.db.db_path, room_id='remote-room', member_id='peer',
            home_url='https://files.invalid', authority_gateway_id='remote-home', authority_epoch=1,
            room_name='Remote files', member_count=1, control_token='r' * 64, expires_at=time.time() + 3600)
    items = [{'attachment_id': f'att_{n:032x}', 'event_id': f'event-{n}', 'name': f'file-{n:02}.txt',
              'kind': 'file', 'mime': 'text/plain', 'size': 1, 'seq': n, 'shared_at': float(n),
              'producer': {'kind': 'member', 'id': 'peer', 'label': 'Peer'}} for n in range(12, 0, -1)]
    unavailable = [False]

    def summary(client):
        if unavailable[0]:
            raise RoomControlClientError('temporary summary failure', status_code=503, retryable=True)
        return {'room': remote, 'status': {}, 'events': [], 'control_actions': []}

    def files(client, *, target_profile, cursor=None, limit=8, **kwargs):
        offset = int(cursor or 0)
        end = min(offset + limit, len(items))
        return {'items': items[offset:end], 'next_cursor': str(end) if end < len(items) else None,
                'has_more': end < len(items)}

    monkeypatch.setattr(RoomControlHTTPClient, 'summary', summary)
    monkeypatch.setattr(RoomControlHTTPClient, 'list_files', files)
    monkeypatch.setattr(RoomControlHTTPClient, 'resolve_file', lambda client, *, code, **kwargs:
        next(item for item in items if selection_digest(remote, item).startswith(code)))
    event = replace(view.event, text='!group files')
    first = await view.runner._handle_group_command(event)
    assert 'file-12.txt' in first and 'file-05.txt' in first and 'file-04.txt' not in first
    command = re.search(r'`(!group files --page [a-f0-9]+ 2)`', first).group(1)
    unavailable[0] = True
    failed = await view.runner._handle_group_command(replace(event, text=command))
    assert 'file-04.txt' not in failed
    unavailable[0] = False
    retried = await view.runner._handle_group_command(replace(event, text=command))
    assert 'file-04.txt' in retried and 'file-01.txt' in retried, retried
