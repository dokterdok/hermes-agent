"""Newest-first snapshot pages keep room identity and current file access."""
from dataclasses import replace
import re

import pytest

from tests.gateway.test_canonical_messaging_views import view  # noqa: F401
from tests.gateway.test_canonical_messaging_files import publish
from gateway.hosted_room_file_lookup import selection_digest


@pytest.mark.asyncio
async def test_pages_keep_snapshot_order_and_download_commands(view):
    view.consent()
    for index in range(1, 12):
        publish(view.receiving, 'room', index, ['pm'], name=f'report-{index:02}.txt')
    event = replace(view.event, text='!group 1 files')
    first = await view.runner._handle_group_command(event)
    assert first.index('report-11.txt') < first.index('report-10.txt')
    assert 'report-04.txt' in first and 'report-03.txt' not in first
    assert 'Download: `!group 1 file ' in first and 'Get full reply' not in first
    command = re.search(r'`(!group 1 files --page [a-f0-9]+ 2)`', first).group(1)
    publish(view.receiving, 'room', 12, ['pm'], name='new-after-snapshot.txt')
    second = await view.runner._handle_group_command(replace(event, text=command))
    assert 'report-03.txt' in second and 'report-01.txt' in second
    assert 'new-after-snapshot' not in second and 'report-04.txt' not in second
    back = re.search(r'`(!group 1 files --page [a-f0-9]+ 1)`', second).group(1)
    assert await view.runner._handle_group_command(replace(event, text=back)) == first
    refreshed = await view.runner._handle_group_command(event)
    assert 'new-after-snapshot.txt' in refreshed


@pytest.mark.asyncio
async def test_cached_page_rechecks_versions_and_home_authorization(view):
    view.consent()
    values = [publish(view.receiving, 'room', index, ['pm'], name=f'file-{index}.txt') for index in range(1, 10)]
    event = replace(view.event, text='!group 1 files')
    first = await view.runner._handle_group_command(event)
    page2 = re.search(r'`(!group 1 files --page [a-f0-9]+ 2)`', first).group(1)
    second = await view.runner._handle_group_command(replace(event, text=page2))
    page1 = re.search(r'`(!group 1 files --page [a-f0-9]+ 1)`', second).group(1)
    view.receiving.db._execute_write(lambda conn: conn.execute(
        'UPDATE hosted_room_attachments SET expires_at=1 WHERE attachment_id=?', (values[-1]['attachment_id'],)))
    refreshed = await view.runner._handle_group_command(replace(event, text=page1))
    assert 'file-9.txt' not in refreshed and 'file-8.txt' in refreshed
    view.consent(False)
    denied = await view.runner._handle_group_command(replace(event, text=page1))
    assert 'file-8.txt' not in denied
