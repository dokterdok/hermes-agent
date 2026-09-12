"""The existing registry and both busy guards dispatch Group reads without inference."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from tests.gateway.test_canonical_messaging_views import view  # noqa: F401


@pytest.mark.asyncio
async def test_group_registry_and_gateway_handler_reach_current_home_views(view):
    from gateway.run import GatewayRunner
    from hermes_cli.commands import resolve_command, should_bypass_active_session, is_interrupt_then_dispatch
    definition = resolve_command('rooms')
    assert definition.name == 'group' and definition.gateway_only
    assert definition.busy_policy == 'dispatch'
    assert should_bypass_active_session('group') and should_bypass_active_session('rooms')
    assert not is_interrupt_then_dispatch('group')
    live = GatewayRunner.__new__(GatewayRunner)
    live.__dict__.update(view.runner.__dict__)
    live._is_user_authorized_for_source = view.runner._is_user_authorized_for_source
    view.consent()
    handler = live._gateway_plain_command_handlers()['group']
    result = await handler(view.event)
    assert 'home secret' in result and 'worker secret' not in result and 'default secret' not in result


@pytest.mark.asyncio
async def test_active_adapter_bypasses_pending_queue_without_interrupt(view):
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.run import GatewayRunner
    live = GatewayRunner.__new__(GatewayRunner)
    live.__dict__.update(view.runner.__dict__)
    live._is_user_authorized_for_source = view.runner._is_user_authorized_for_source
    view.consent()
    replies = []
    async def inline(event):
        replies.append(await live._gateway_plain_command_handlers()['group'](event))
    async def cancel(*args):
        pytest.fail('Group read must not interrupt the ordinary chat')
    adapter = SimpleNamespace(_message_handler=live._handle_message, name='fixture',
        _dispatch_inline_reply=inline, _dispatch_active_session_command=cancel, _pending_messages={})
    await BasePlatformAdapter._handle_message_while_active(adapter, view.event, 'ordinary-busy-chat')
    assert len(replies) == 1 and 'home secret' in replies[0]
    assert not adapter._pending_messages


@pytest.mark.asyncio
async def test_read_surfaces_contain_real_paragraphs_not_escape_sequences(view):
    view.consent()
    for command in ('/group list', '/group 1', '/group 1 bots', '/group 1 files'):
        result = await view.runner._handle_group_command(replace(view.event, text=command))
        assert len(result.splitlines()) > 3
        assert '\n\n' in result
        assert '\\n' not in result
