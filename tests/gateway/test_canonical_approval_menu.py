"""Approval UI selections retain the exact request shown to the owner."""
from dataclasses import replace

import pytest

from gateway.choice_picker import ChoicePage
from gateway.hosted_room_messaging import MessagingRoomBackend
from tests.gateway.test_canonical_group_menu import native, choice
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401


@pytest.mark.asyncio
async def test_native_menu_approves_only_the_selected_request(view, monkeypatch):
    from gateway import hosted_rooms
    from gateway.session_authorities import owner_scope
    adapter = native(view)
    with owner_scope(view.receiving):
        gateway = hosted_rooms.local_authority_gateway_id()
    item = dict(room_id='room', authority_gateway_id=gateway, authority_epoch=1, member_id='pm',
        task_id='task', execution_generation=1, request_id='request', command='Fixture action',
        description='A test decision', choices=['once', 'deny'])
    decisions = []
    def approvals(backend, room):
        backend.check(room)
        return [dict(item)]
    def decide(backend, *, room, command_id, decision):
        backend.check(room)
        decisions.append((command_id, decision))
        return {'status': 'resolved', 'prompt_id': decision['request_id']}
    monkeypatch.setattr(MessagingRoomBackend, 'approvals', approvals)
    monkeypatch.setattr(MessagingRoomBackend, 'decide', decide)
    event = replace(view.event, text='/group 1 approvals', message_id='open-approvals')
    assert await view.runner._handle_group_command(event) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    assert 'Fixture action' in page.title
    assert [item['label'] for item in page.choices][:2] == ['Allow once', 'Deny']
    result = await adapter.picker['on_choice_selected']('42', choice(page, 'Allow once'))
    assert isinstance(result, ChoicePage) and result.title == 'Allowed once.'
    assert decisions[0][1] == dict(member_id='pm', task_id='task', execution_generation=1, request_id='request', choice='once')
    assert await view.runner._handle_group_command(replace(event, text='/group 1 approvals not-a-command')) is not None
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_changed_approval_is_not_silently_reselected(view, monkeypatch):
    from gateway import hosted_rooms
    from gateway.session_authorities import owner_scope
    adapter = native(view)
    with owner_scope(view.receiving):
        gateway = hosted_rooms.local_authority_gateway_id()
    current = dict(room_id='room', authority_gateway_id=gateway, authority_epoch=1, member_id='pm',
        task_id='task', execution_generation=1, request_id='first', command='First action', description='', choices=['once', 'deny'])
    monkeypatch.setattr(MessagingRoomBackend, 'approvals', lambda backend, room: [dict(current)])
    monkeypatch.setattr(MessagingRoomBackend, 'decide', lambda *args, **kwargs: pytest.fail('Changed request was applied'))
    assert await view.runner._handle_group_command(replace(view.event, text='/group 1 approvals', message_id='open')) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    current.update(request_id='second', command='Second action')
    result = await adapter.picker['on_choice_selected']('42', choice(page, 'Allow once'))
    assert isinstance(result, ChoicePage) and 'no longer waiting' in result.title
