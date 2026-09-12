"""Ordinary native Slack confirmation events; no Relay or live transport."""
from copy import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session_authorities import owner_scope
from hermes_cli.config import load_config, save_config
from plugins.platforms.slack.adapter import SlackAdapter
from tests.gateway.test_receiving_home_consent import (
    home, view, command, confirm_command, select_home,  # noqa: F401
)


@pytest.mark.asyncio
@pytest.mark.parametrize('source_has_message_id', [False, True])
async def test_native_slack_typed_confirmation_keeps_the_logical_home(home, source_has_message_id):
    home.event.source.platform = Platform.SLACK
    home.event.source.thread_id = None
    home.event.source.message_id = None
    home.event.raw_message = {'type': 'message', 'channel': '42', 'user': '42'}
    home.adapter.config.home_channel.platform = Platform.SLACK
    home.adapter.config.home_channel.thread_id = None
    home.runner._profile_adapters['home'] = {Platform.SLACK: home.adapter}
    with owner_scope(home.receiving):
        save_config({'platforms': {'slack': home.adapter.config.to_dict()}})
    assert 'now your Home' in await select_home(home)
    assert 'Everyone who can read this chat' in await command(home, '!group')
    pending_command = confirm_command(home)

    # Native Slack's normal top-level message builder uses this synthetic
    # thread for session keying; MessageEvent, not build_source, receives ts.
    ts = '1757680000.000002'
    incoming = copy(home.event.source)
    incoming.thread_id = SlackAdapter._session_thread_ts(
        SimpleNamespace(config=home.adapter.config), {'ts': ts}, ts, False, {})
    assert incoming.thread_id == ts and incoming.message_id is None
    if source_has_message_id:
        incoming.message_id = ts
    event = replace(home.event, text=pending_command, source=incoming,
        message_id=ts, raw_message={'type': 'message', 'channel': '42', 'user': '42', 'ts': ts})
    result = await home.runner._handle_rooms_command(event)
    with owner_scope(home.receiving):
        stored = load_config()['platforms']['slack']['home_channel']
    assert stored.get('group_audience_ack'), result
