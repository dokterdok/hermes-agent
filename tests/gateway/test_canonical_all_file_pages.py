"""Global Files merge existing authorized catalogs without choosing an arbitrary room."""
from dataclasses import replace
import re

import pytest

from gateway import hosted_rooms
from gateway.session_authorities import owner_scope
from gateway.session_group_home_access import dispatch_home_access
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401
from tests.gateway.test_canonical_messaging_files import publish


def add_room(view, room_id, name, consent=True):
    authority = view.receiving
    with owner_scope(authority):
        authority.hosted_room_service.authorize_room('native-owner', room_id, create=True)
        hosted_rooms.create_room(authority.db.db_path, room_id=room_id, name=name,
            authority_gateway_id=hosted_rooms.local_authority_gateway_id(), members=[
                dict(member_id='pm', profile='pm', handle='pm'), dict(member_id='builder', profile='builder', handle='builder')])
        if consent:
            dispatch_home_access(authority, view.actor, 'groups.control.home.set', {'room_id': room_id, 'enabled': True})


@pytest.mark.asyncio
async def test_all_files_merges_newest_first_and_keeps_snapshot_cursor(view):
    view.consent()
    add_room(view, 'other', 'Other work')
    add_room(view, 'private', 'Unshared work', consent=False)
    for index in range(1, 14):
        publish(view.receiving, 'room' if index % 2 else 'other', index, ['pm'], name=f'report-{index:02}.txt')
    publish(view.receiving, 'private', 99, ['pm'], name='do-not-disclose.txt')
    event = replace(view.event, text='!group files')
    first = await view.runner._handle_group_command(event)
    assert 'Files across Group Chats' in first
    assert first.index('report-13.txt') < first.index('report-12.txt') < first.index('report-11.txt')
    assert 'report-06.txt' in first and 'report-05.txt' not in first
    assert 'Other work' in first and 'home secret' in first and 'do-not-disclose' not in first
    next_command = re.search(r'`(!group files --page [a-f0-9]+ 2)`', first).group(1)
    publish(view.receiving, 'other', 14, ['pm'], name='newer-after-snapshot.txt')
    second = await view.runner._handle_group_command(replace(event, text=next_command))
    assert 'report-05.txt' in second and 'report-01.txt' in second and 'newer-after-snapshot' not in second
    again = re.search(r'`(!group files --page [a-f0-9]+ 1)`', second).group(1)
    assert await view.runner._handle_group_command(replace(event, text=again)) == first
    assert 'newer-after-snapshot.txt' in await view.runner._handle_group_command(event)


@pytest.mark.asyncio
async def test_all_files_removes_rooms_whose_consent_was_revoked(view):
    view.consent()
    add_room(view, 'other', 'Other work')
    for index in range(1, 10):
        publish(view.receiving, 'other', index, ['pm'], name=f'other-{index}.txt')
    publish(view.receiving, 'room', 11, ['pm'], name='still-visible.txt')
    event = replace(view.event, text='!group files')
    first = await view.runner._handle_group_command(event)
    page2 = re.search(r'`(!group files --page [a-f0-9]+ 2)`', first).group(1)
    with owner_scope(view.receiving):
        dispatch_home_access(view.receiving, view.actor, 'groups.control.home.set', {'room_id': 'other', 'enabled': False})
    second = await view.runner._handle_group_command(replace(event, text=page2))
    assert 'other-' not in second and 'Some Group Chats could not be checked' in second
