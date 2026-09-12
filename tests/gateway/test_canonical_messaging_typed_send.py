"""Typed Send uses current receiving Home, immutable delivery IDs and the planner."""
from dataclasses import replace
import weakref

import pytest

from gateway import hosted_rooms, hosted_room_driver
from gateway.session_authorities import owner_scope
from tests.gateway.test_canonical_messaging_views import view, Adapter  # noqa: F401


@pytest.mark.asyncio
async def test_typed_send_preserves_text_and_deduplicates_across_adapter_restart(view):
    service = view.receiving.hosted_room_service
    service.local_profiles = lambda: ('pm', 'builder')
    tracked = []
    view.runner._track_deferred_agent_worker = lambda future, _: tracked.append(future)
    view.consent()
    content = '@pm Prepare this report:\nKeep `@builder` literal.\nSee https://example.test/@builder'
    event = replace(view.event, message_id='native-message-1', text='!group 1 send ' + content)
    result = await view.runner._handle_group_command(event)
    assert result == 'Sent to home secret.'
    assert tracked
    # The backend profile still routes elsewhere; that does not confer its owner identity.
    assert event.source.profile == 'worker'
    replacement = Adapter(view.adapter.config)
    view.runner._profile_adapters['home'][event.source.platform] = replacement
    event.source._transport_adapter_ref = weakref.ref(replacement)
    assert await view.runner._handle_group_command(event) == result
    with owner_scope(view.receiving):
        message, = [e for e in service._events('room') if e['kind'] == 'message.user']
        assert message['payload']['text'] == content
        assert message['actor']['display_name'].endswith(' via Telegram')
        task, = hosted_room_driver.list_tasks(service.db_path, room_id='room')
        assert task['payload']['target_member_id'] == 'pm'
    for name in ('worker', 'default'):
        assert not view.owners[name].db._read_all("SELECT * FROM hosted_room_events WHERE kind='message.user'")
    view.consent(False)
    assert await view.runner._handle_group_command(event) != result
    assert all(future.done() for future in tracked)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['consent', 'drain'])
async def test_typed_send_rechecks_permission_before_the_actual_append(view, monkeypatch, change):
    service = view.receiving.hosted_room_service
    service.local_profiles = lambda: ('pm', 'builder')
    view.runner._track_deferred_agent_worker = lambda future, _: None
    view.consent()
    original = hosted_rooms.append_event
    def change_at_write(*args, **kwargs):
        if change == 'consent':
            view.consent(False)
        else:
            view.runner._draining = True
        return original(*args, **kwargs)
    monkeypatch.setattr(hosted_rooms, 'append_event', change_at_write)
    result = await view.runner._handle_group_command(replace(view.event, message_id='send', text='!group 1 send @pm Hello'))
    assert not result.startswith('Sent to ')
    assert not view.receiving.db._read_all("SELECT * FROM hosted_room_events WHERE kind='message.user'")
