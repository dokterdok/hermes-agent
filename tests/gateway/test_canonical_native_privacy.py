"""Native privacy metadata uses the transport's positive facts, not saved labels."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import time

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('count,private', [(2, True), (3, False), (1, False), (0, False), (None, False)])
async def test_matrix_needs_two_known_participants(count, private, monkeypatch):
    from tests.gateway.test_matrix_message_event_metadata import _make_adapter
    adapter = _make_adapter(monkeypatch=monkeypatch)
    identity = SimpleNamespace(display_name='Private-looking name', room_topic=None, server_name='example.org',
                               chat_type='dm', joined_member_count=count)
    adapter._resolve_room_identity = AsyncMock(return_value=identity)
    adapter._is_dm_room = AsyncMock(return_value=True)
    adapter._get_display_name = AsyncMock(return_value='Alice')
    adapter._background_read_receipt = MagicMock()
    context = await adapter._resolve_message_context('!room:example.org', '@alice:example.org', '$event',
        '/group', {'body': '/group'}, {})
    assert context is not None
    assert context[-1].is_one_to_one is private


@pytest.mark.asyncio
@pytest.mark.parametrize('channel,private', [('D123', True), ('G123', False), ('C123', False)])
async def test_slack_native_slash_retains_private_vs_shared_chat(channel, private):
    from gateway.config import PlatformConfig
    from plugins.platforms.slack.adapter import SlackAdapter
    adapter = SlackAdapter(PlatformConfig(enabled=True, token='fixture-token'))
    adapter.handle_message = AsyncMock()
    await adapter._handle_slash_command({'user_id': 'U123', 'channel_id': channel,
        'team_id': 'T123', 'command': '/hermes', 'text': 'group list'})
    event = adapter.handle_message.await_args.args[0]
    assert event.source.is_one_to_one is private
    assert event.source.message_is_edit is False
    assert event.text == '/group list'


@pytest.mark.asyncio
@pytest.mark.parametrize('bot', [False, True])
async def test_slack_resolved_sender_class_survives_message_construction(bot, monkeypatch):
    from gateway.config import PlatformConfig
    from gateway.group_home_identity import private_event, trusted_person
    from plugins.platforms.slack.adapter import SlackAdapter
    monkeypatch.setenv('SLACK_ALLOW_BOTS', 'all')
    adapter = SlackAdapter(PlatformConfig(enabled=True, token='fixture-token', extra={'allow_bots': 'all'}))
    adapter._resolve_user_is_bot = AsyncMock(return_value=bot)
    adapter._resolve_user_name = AsyncMock(return_value='Sender')
    adapter._resolve_channel_name = AsyncMock(return_value='DM')
    adapter._hydrate_thread_context = AsyncMock(return_value=(None, [], []))
    adapter.handle_message = AsyncMock()
    await adapter._handle_slack_message_impl({'channel': 'D123', 'channel_type': 'im', 'user': 'U123',
        'team': 'T123', 'text': '/group list', 'ts': str(time.time())})
    assert adapter.handle_message.await_count == 1
    event = adapter.handle_message.await_args.args[0]
    assert private_event(event)
    assert event.source.is_bot is bot
    assert trusted_person(event) is (not bot)
