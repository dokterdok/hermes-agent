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


@pytest.mark.asyncio
async def test_remember_requires_second_exact_selection_and_shows_scope(view, monkeypatch):
    adapter = native(view)
    current = dict(room_id='room', authority_gateway_id='gateway', authority_epoch=1, member_id='pm',
        task_id='task', execution_generation=1, request_id='first', command='Fixture action', description='',
        choices=['once', 'deny'], profile='default', remember_key='a' * 64, remember_context='Local, folder /fixture')
    decisions = []
    monkeypatch.setattr(MessagingRoomBackend, 'approvals', lambda backend, room: [dict(current)])
    def apply(backend, **kwargs):
        backend.check(kwargs['room'])
        decisions.append(kwargs['decision'])
        return {'status': 'resolved', 'remembered': True}
    monkeypatch.setattr(MessagingRoomBackend, 'decide', apply)
    event = replace(view.event, text='/group 1 approvals', message_id='open-remember')
    assert await view.runner._handle_group_command(event) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    warning = await adapter.picker['on_choice_selected']('42', choice(page, 'Always allow in this chat'))
    assert isinstance(warning, ChoicePage) and '/fixture' in warning.title and 'Other commands' in warning.title
    assert decisions == []
    # A metadata change invalidates the confirmation, even if the request ID is unchanged.
    current.update(remember_key='b' * 64, remember_context='Local, folder /different')
    refused = await adapter.picker['on_choice_selected']('42', choice(warning, 'Always allow in this chat'))
    assert 'no longer waiting' in refused.title and decisions == []
    await view.runner._handle_group_command(replace(event, message_id='fresh-remember'))
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    warning = await adapter.picker['on_choice_selected']('42', choice(page, 'Always allow in this chat'))
    result = await adapter.picker['on_choice_selected']('42', choice(warning, 'Always allow in this chat'))
    assert result.title.startswith('Allowed.')
    assert decisions == [dict(member_id='pm', task_id='task', execution_generation=1,
                              request_id='first', choice='remember', remember_key='b' * 64)]


@pytest.mark.asyncio
async def test_permissions_are_paged_and_show_exact_detail_before_removal(view, monkeypatch):
    adapter = native(view)
    values = [dict(rule_id=f'{index:064x}', member_id='pm', command_text=f'Fixture {index}',
                   context_text=f'Local, folder /fixture/{index}', generation=1, state='active') for index in range(10)]
    removed = []
    monkeypatch.setattr(MessagingRoomBackend, 'permissions', lambda backend, room: values)
    monkeypatch.setattr(MessagingRoomBackend, 'forget_permission', lambda backend, room, rule_id, generation: removed.append((rule_id, generation)) or 1)
    assert await view.runner._handle_group_command(replace(view.event, text='/group 1 bots', message_id='permissions')) is None
    page = ChoicePage(adapter.picker['title'], adapter.picker['choices'])
    page = await adapter.picker['on_choice_selected']('42', choice(page, 'Manage permissions'))
    page = await adapter.picker['on_choice_selected']('42', choice(page, 'Go to page 2'))
    page = await adapter.picker['on_choice_selected']('42', choice(page, 'View permission · pm: Fixture 8'))
    assert '/fixture/8' in page.title and 'already approved' in page.title and removed == []
    result = await adapter.picker['on_choice_selected']('42', choice(page, 'Remove permission'))
    assert removed == [(values[8]['rule_id'], 1)] and 'ask again' in result.title
