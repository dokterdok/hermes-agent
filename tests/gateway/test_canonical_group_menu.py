"""Native pages and reply compose use the same canonical owner consumers."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest

from gateway.choice_picker import ChoicePage, ChoiceProgress
from gateway.native_reply_input import ReplyInput, NativeReplySubmission, handle_native_reply
from gateway.native_document_guard import mark_native_document_guard
from tests.gateway.test_canonical_messaging_views import view, Adapter  # noqa: F401
from tests.gateway.test_canonical_messaging_files import publish


class NativeAdapter(Adapter):
    supports_choice_pages = True
    supports_reply_input = True

    async def send_choice_picker(self, **kwargs):
        self.picker = kwargs
        return SimpleNamespace(success=True)

    async def send_reply_input(self, event, title, on_reply):
        self.prompt = title
        self.request = ReplyInput(self, on_reply)
        self.request.bind_source(event)
        self.request.chat_id, self.request.bot_id = str(event.source.chat_id), 'fixture-bot'
        self.request.register()
        self.request.bind_prompt('prompt-123')
        return self.request, SimpleNamespace(success=True)

    @mark_native_document_guard
    async def send_document(self, *, chat_id, file_path, **kwargs):
        self.document = chat_id, Path(file_path).read_bytes()
        return SimpleNamespace(success=True)


def native(view):
    adapter = NativeAdapter(view.adapter.config)
    adapter.gateway_runner = view.runner
    adapter._session_store = SimpleNamespace(sessions_dir=Path(view.receiving.profile_id) / 'sessions')
    view.runner._profile_adapters['home'][view.event.source.platform] = adapter
    view.event.source._transport_adapter_ref = weakref.ref(adapter)
    view.runner._track_deferred_agent_worker = lambda future, _: None
    view.receiving.hosted_room_service.local_profiles = lambda: ('pm', 'builder')
    view.consent()
    return adapter


def choice(page, label):
    return next(item['value'] for item in page.choices if item['label'] == label)


@pytest.mark.asyncio
async def test_menu_back_navigation_and_native_reply_need_no_reentered_command(view):
    adapter = native(view)
    event = replace(view.event, text='/group', message_id='open')
    assert await view.runner._handle_group_command(event) is None
    picker = adapter.picker
    assert picker['metadata']['requester_user_id'] == '42' and picker['metadata']['choice_pages'] is True
    selected = picker['on_choice_selected']
    room = await selected('42', picker['choices'][0]['value'])
    assert isinstance(room, ChoicePage)
    assert room.choices[0]['label'] == 'Send message'
    assert not any(item['label'] == 'View files' for item in room.choices)
    bots = await selected('42', choice(room, 'View Bots'))
    assert isinstance(bots, ChoicePage)
    groups = await selected('42', choice(bots, '‹ Group Chats'))
    assert isinstance(groups, ChoicePage) and 'Group Chats' in groups.title
    assert groups.choices[0]['label'].startswith('1. home secret')
    room = await selected('42', groups.choices[0]['value'])
    composed = await selected('42', choice(room, 'Send message'))
    assert isinstance(composed, ChoicePage) and 'Cancel message' in [item['label'] for item in composed.choices]
    reply = replace(event, text='@pm Prepare the report\nKeep this second line.', message_id='typed-reply', reply_to_message_id='prompt-123')
    submission = NativeReplySubmission(adapter, adapter.request.token, True)
    result = await handle_native_reply(view.runner, reply, submission)
    assert result == 'Sent to home secret.'
    assert await handle_native_reply(view.runner, reply, submission) == result
    messages = view.receiving.db._read_all("SELECT payload_json FROM hosted_room_events WHERE kind='message.user'")
    assert len(messages) == 1
    assert 'Prepare the report' in messages[0]['payload_json']
    assert not view.owners['worker'].db._read_all("SELECT * FROM hosted_room_events WHERE kind='message.user'")


@pytest.mark.asyncio
async def test_file_action_delivers_and_stale_or_revoked_menu_does_not_disclose(view):
    adapter = native(view)
    publish(view.receiving, 'room', 1, ['pm'], name='report.txt', data=b'real report bytes')
    event = replace(view.event, text='/group 1', message_id='open-file')
    assert await view.runner._handle_group_command(event) is None
    callback = adapter.picker['on_choice_selected']
    initial = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    old = choice(initial, 'View files')
    files = await callback('42', old)
    assert isinstance(files, ChoicePage)
    progress = await callback('42', choice(files, 'Download report.txt'))
    assert isinstance(progress, ChoiceProgress)
    assert not hasattr(adapter, 'document')
    outcome = await progress.complete()
    assert isinstance(outcome, ChoicePage) and outcome.title == 'File sent.'
    assert adapter.document == ('42', b'real report bytes')
    assert isinstance(await callback('42', old), str)
    view.consent(False)
    result = await callback('42', choice(outcome, 'View Group Chat'))
    assert isinstance(result, str) and 'home secret' not in result


@pytest.mark.asyncio
async def test_full_reply_action_keeps_the_selected_reply_when_a_new_one_arrives(view):
    from gateway import hosted_rooms
    from gateway.session_authorities import owner_scope
    adapter = native(view)
    full = 'Detailed original reply.\n' * 25
    def publish_reply(event_id, body):
        with owner_scope(view.receiving):
            hosted_rooms.append_event(view.receiving.db.db_path, room_id='room', event_id=event_id,
                kind='message.member', actor={'kind': 'member', 'id': 'pm'},
                payload={'member_id': 'pm', 'text': body},
                authority_gateway_id=hosted_rooms.local_authority_gateway_id(), authority_epoch=1)
    publish_reply('long-reply', full)
    assert await view.runner._handle_group_command(replace(view.event, text='/group 1', message_id='open-reply')) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    assert 'View files' not in [item['label'] for item in page.choices]
    publish_reply('new-reply', 'Newer short answer.')
    progress = await adapter.picker['on_choice_selected']('42', choice(page, 'Get full reply'))
    assert isinstance(progress, ChoiceProgress)
    completed = await progress.complete()
    assert isinstance(completed, ChoicePage) and completed.title == 'File sent.'
    assert adapter.document == ('42', full.encode())


@pytest.mark.asyncio
async def test_cancel_closes_native_navigation_and_the_reply_specific_compose(view):
    adapter = native(view)
    event = replace(view.event, text='/group 1', message_id='open')
    assert await view.runner._handle_group_command(event) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    current = await adapter.picker['on_choice_selected']('42', choice(page, 'Send message'))
    result = await view.runner._handle_group_command(replace(event, text='/group cancel', message_id='cancel'))
    assert 'cancelled' in result
    assert not adapter.request.pending and adapter.request.deadline == 0
    reply = replace(event, text='@pm This must not be sent', message_id='late', reply_to_message_id='prompt-123')
    response = await handle_native_reply(view.runner, reply, NativeReplySubmission(adapter, adapter.request.token, True))
    assert response != 'Sent to home secret.'
    assert not view.receiving.db._read_all("SELECT * FROM hosted_room_events WHERE kind='message.user'")
    stale = await adapter.picker['on_choice_selected']('42', choice(current, 'View Bots'))
    assert isinstance(stale, str) and 'expired' in stale.lower()


@pytest.mark.asyncio
async def test_discord_thread_choices_use_the_receiving_thread_not_parent_channel(view):
    from gateway.config import Platform
    from gateway.group_home_identity import acknowledgement
    from gateway.group_home_consent import disclosure_stamp
    adapter = native(view)
    view.runner._profile_adapters['home'] = {Platform.DISCORD: adapter}
    home = adapter.config.home_channel
    home.platform, home.chat_id, home.thread_id = Platform.DISCORD, 'parent', 'thread'
    home.selection_id = 'explicit-selection'
    home.group_audience_ack = acknowledgement(home)
    adapter.config.extra['group_allow_admin_from'] = ['42']
    source = replace(view.event.source, platform=Platform.DISCORD, chat_id='parent', thread_id='thread', chat_type='group')
    source._transport_adapter_ref = weakref.ref(adapter)
    event = replace(view.event, source=source, text='/group', message_id='open-thread')
    assert disclosure_stamp(view.runner, event) is not None
    assert await view.runner._handle_group_command(event) is None
    assert adapter.picker['metadata']['thread_id'] == 'thread'
    assert adapter.picker['metadata']['hermes_profile'] == 'home'
    callback = adapter.picker['on_choice_selected']
    value = adapter.picker['choices'][0]['value']
    assert isinstance(await callback('parent', value), str)
    room = await callback('thread', value)
    assert isinstance(room, ChoicePage) and 'home secret' in room.title
