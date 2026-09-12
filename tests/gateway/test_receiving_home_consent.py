"""Accepted audience flow on real receiving-owner stores, never worker Home."""
from dataclasses import replace
from pathlib import Path
import time
import weakref

import pytest

from gateway import group_home_consent as consent
from gateway.config import HomeChannel, Platform
from gateway.group_home_selection import set_home
from gateway.session_authorities import owner_scope
from gateway.session_group_home_access import home_access_granted
from gateway.slash_commands import GatewaySlashCommandsMixin
from hermes_cli.config import load_config, save_config
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401


@pytest.fixture
def home(view):
    view.event.source.chat_type = 'group'
    view.event.source.thread_id = 'topic'
    view.event.source.scope_id = 'scope'
    view.event.raw_message.chat.type = 'group'
    view.adapter.config.extra['group_allow_admin_from'] = ['42']
    view.adapter.config.home_channel.thread_id = 'topic'
    view.adapter.config.home_channel.scope_id = 'scope'
    with owner_scope(view.receiving):
        save_config({'platforms': {'telegram': view.adapter.config.to_dict()}, 'unrelated': {'keep': 1}})
    return view


async def command(home, value):
    return await home.runner._handle_rooms_command(replace(home.event, text=value))


async def select_home(home):
    return await GatewaySlashCommandsMixin._handle_set_home_command(home.runner, replace(home.event, text='/sethome'))


def saved(home):
    with owner_scope(home.receiving):
        return load_config()['platforms']['telegram']['home_channel']


def confirm_command(home):
    pending = next(iter(home.runner._group_home_confirmations.values()))
    return '!group confirm ' + pending.token


@pytest.mark.asyncio
async def test_warning_precedes_any_room_access_and_does_not_grant_room_permission(home, monkeypatch):
    assert 'now your Home' in await select_home(home)
    with monkeypatch.context() as patch:
        patch.setattr('gateway.group_chat_slash.current_room_backend',
                      lambda *a: pytest.fail('Room access before audience consent'))
        warning = await command(home, '!group 1')
        assert 'Everyone who can read this chat' in warning and '!group confirm ' in warning
        assert 'home secret' not in warning and '/group confirm' not in warning
        assert consent.disclosure_stamp(home.runner, home.event) is None
        assert consent.disclosure_stamp(home.runner, home.event, require_audience=False) is not None
    result = await command(home, confirm_command(home))
    assert 'home secret' not in result
    with owner_scope(home.receiving):
        assert not home_access_granted(home.receiving, 'room')
        assert load_config()['unrelated'] == {'keep': 1}
    assert saved(home)['group_audience_ack'] == consent.acknowledgement(home.adapter.config.home_channel)
    home.adapter.config.home_channel = HomeChannel.from_dict(saved(home))
    home.consent()
    assert 'home secret' in await command(home, '!group list')
    for name in ('default', 'worker'):
        assert Path(home.owners[name].profile_id, 'config.yaml').read_text() == 'model: {}\n'
        assert not Path(home.owners[name].profile_id, '.env').exists()
    assert not home.receiving.sessions
    assert home.receiving.db._read_all('SELECT * FROM session_admissions') == []


@pytest.mark.asyncio
async def test_repeat_sethome_rotates_selection_and_old_typed_token_cannot_confirm(home):
    await select_home(home)
    await command(home, '!group')
    stale = confirm_command(home)
    first = saved(home)
    await select_home(home)
    second = saved(home)
    assert second['selection_id'] != first['selection_id']
    assert not second.get('group_audience_ack')
    await command(home, '!group')
    current = confirm_command(home)
    for value in ('!group confirm', stale, current + ' extra', '!group confirm ' + '\u00e9' * 32):
        assert 'expired' in await command(home, value)
        assert not saved(home).get('group_audience_ack')
    await command(home, current)
    assert saved(home).get('group_audience_ack')
    await select_home(home)
    assert not saved(home).get('group_audience_ack')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['actor', 'topic', 'scope', 'authority', 'epoch', 'store_epoch', 'adapter', 'admin', 'disk_admin', 'disk_home', 'expired'])
async def test_confirmation_cas_rejects_current_authority_admin_or_selection_drift(home, change):
    await select_home(home)
    await command(home, '!group')
    confirmation = confirm_command(home)
    if change == 'actor': home.event.source.user_id = 'other'
    elif change == 'topic': home.event.source.thread_id = 'other'
    elif change == 'scope': home.event.source.scope_id = 'other'
    elif change == 'authority':
        home.runner.session_authorities._by_key[str(home.runner.receiving_home)] = home.owners['worker']
    elif change == 'epoch': home.receiving.epoch += 1
    elif change == 'store_epoch':
        from hermes_state_runtime import begin_runtime_epoch
        begin_runtime_epoch(home.receiving.db, instance_id='replacement')
    elif change == 'adapter':
        home.runner._profile_adapters['home'][Platform.TELEGRAM] = type(home.adapter)(home.adapter.config)
    elif change == 'admin': home.adapter.config.extra['group_allow_admin_from'] = ['other']
    elif change.startswith('disk_'):
        with owner_scope(home.receiving):
            config = load_config()
            platform = config['platforms']['telegram']
            if change == 'disk_admin': platform['extra']['group_allow_admin_from'] = ['other']
            else: platform['home_channel']['selection_id'] = 'other-selection'
            save_config(config)
    else: next(iter(home.runner._group_home_confirmations.values())).deadline = time.monotonic() - 1
    result = await command(home, confirmation)
    assert 'home secret' not in result
    assert not saved(home).get('group_audience_ack')


@pytest.mark.asyncio
async def test_cancel_help_and_untrusted_sources_do_not_fetch_rooms(home, monkeypatch):
    await select_home(home)
    monkeypatch.setattr('gateway.group_chat_slash.current_room_backend',
                        lambda *a: pytest.fail('Unexpected room lookup'))
    assert 'Choose a Group Chat' in await command(home, '!group help')
    await command(home, '!group')
    token = confirm_command(home)
    assert 'cancelled' in await command(home, '!group cancel')
    assert 'expired' in await command(home, token)
    for attribute, value in [('is_bot', True), ('delivered_via_upstream_relay', True)]:
        with monkeypatch.context() as patch:
            patch.setattr(home.event.source, attribute, value)
            assert 'Everyone who can read this chat' not in await command(home, '!group')
            assert 'now your Home' not in await set_home(home.runner, home.event)
    home.event.raw_message.edit_date = 'edited'
    assert 'now your Home' not in await set_home(home.runner, home.event)


@pytest.mark.asyncio
async def test_failed_or_stale_selection_save_leaves_live_home_unchanged(home, monkeypatch):
    before = saved(home)
    with monkeypatch.context() as patch:
        patch.setattr('hermes_cli.config.save_config', lambda *a, **kw: None)
        assert 'could not be saved' in await select_home(home)
    assert saved(home) == before
    with owner_scope(home.receiving):
        config = load_config()
        config['platforms']['telegram']['home_channel']['selection_id'] = 'newer-external'
        save_config(config)
    assert 'could not be saved' in await select_home(home)
    assert saved(home)['selection_id'] == 'newer-external'


@pytest.mark.asyncio
async def test_sethome_user_command_permission_is_delivery_only_not_shared_admin(home):
    home.adapter.config.extra.update(group_allow_admin_from=['different-admin'], group_user_allowed_commands=['sethome'])
    with owner_scope(home.receiving):
        save_config({'platforms': {'telegram': home.adapter.config.to_dict()}})
    assert 'now your Home' in await select_home(home)
    assert saved(home)['selection_id']
    assert consent.disclosure_stamp(home.runner, home.event, require_audience=False) is None
    assert 'Everyone who can read this chat' not in await command(home, '!group')
    with owner_scope(home.receiving):
        assert not home_access_granted(home.receiving, 'room')


@pytest.mark.asyncio
@pytest.mark.parametrize('platform, prefix', [(Platform.TELEGRAM, '/'), (Platform.MATRIX, '!')])
async def test_typed_fallback_uses_receiving_platform_prefix(home, platform, prefix):
    home.adapter.typed_command_prefix = prefix
    home.event.source.platform = platform
    home.adapter.config.home_channel.platform = platform
    home.runner._profile_adapters['home'] = {platform: home.adapter}
    with owner_scope(home.receiving):
        save_config({'platforms': {platform.value: home.adapter.config.to_dict()}})
    await select_home(home)
    result = await command(home, prefix + 'group')
    assert f'{prefix}group confirm ' in result and f'{prefix}group cancel' in result
    assert 'home secret' not in result


@pytest.mark.asyncio
async def test_live_admin_change_while_selection_waits_cannot_save(home, monkeypatch):
    from gateway import group_home_selection as selection
    before = saved(home)
    original = selection._replace_home
    def changed(*args):
        home.adapter.config.extra['group_allow_admin_from'] = ['other']
        return original(*args)
    monkeypatch.setattr(selection, '_replace_home', changed)
    assert 'could not be saved' in await select_home(home)
    assert saved(home) == before


@pytest.mark.asyncio
async def test_launch_receiver_updates_its_existing_live_delivery_config(view):
    view.receiving = view.owners['default']
    view.runner.receiving_home = Path(view.receiving.profile_id)
    view.runner._profile_adapters = {}
    view.runner.adapters = {Platform.TELEGRAM: view.adapter}
    view.runner._native_transport_homes = {None: view.runner.receiving_home}
    view.runner.config.platforms[Platform.TELEGRAM] = view.adapter.config
    view.event.source._authorization_profile_home = view.runner.receiving_home
    with owner_scope(view.receiving):
        save_config({'platforms': {'telegram': view.adapter.config.to_dict()}})
    assert 'now your Home' in await select_home(view)
    selected = view.runner.config.get_home_channel(Platform.TELEGRAM)
    assert selected is view.adapter.config.home_channel
    assert selected.selection_id == saved(view)['selection_id']
    assert not selected.group_audience_ack


@pytest.mark.asyncio
async def test_legacy_env_failure_rolls_back_only_own_home_slot(home, monkeypatch):
    before = saved(home)
    def fail(values):
        config = load_config()
        config['platforms']['telegram']['operator_note'] = 'keep concurrent setting'
        save_config(config)
        raise OSError('fixture write refusal')
    monkeypatch.setattr('gateway.group_home_selection._save_legacy_home', fail)
    assert 'could not be saved' in await select_home(home)
    assert saved(home) == before
    with owner_scope(home.receiving):
        assert load_config()['platforms']['telegram']['operator_note'] == 'keep concurrent setting'


@pytest.mark.asyncio
async def test_native_confirmation_supplies_requester_topic_and_receiving_profile(home, adapter):
    # Real Telegram callback handler and fake Bot API, no network connection.
    from tests.gateway.test_telegram_choice_picker import _query
    adapter.config = home.adapter.config
    home.runner._profile_adapters['home'][Platform.TELEGRAM] = adapter
    home.event.source._transport_adapter_ref = weakref.ref(adapter)
    home.adapter = adapter
    await select_home(home)
    home.consent()
    assert await command(home, '/group') is None
    state = adapter._choice_picker_state['42']
    assert state['requester_user_id'] == '42' and state['thread_id'] == 'topic'
    request = adapter._send_message_with_thread_fallback.await_args.kwargs
    assert 'Everyone who can read this chat' in request['text']
    action = request['reply_markup'].inline_keyboard[0][0].callback_data
    wrong = _query()
    wrong.message.message_id = state['msg_id']
    wrong.message.chat_id = '42'
    wrong.message.message_thread_id = 'topic'
    await adapter._handle_choice_picker_callback(wrong, action, '42')
    assert not saved(home).get('group_audience_ack')
    wrong.from_user.id = '42'
    await adapter._handle_choice_picker_callback(wrong, action, '42')
    assert saved(home).get('group_audience_ack')
    current = adapter._choice_picker_state['42']
    assert any('home secret' in item['label'] for item in current['choices'])
    action = wrong.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    await adapter._handle_choice_picker_callback(wrong, action, '42')
    assert 'home secret' in wrong.edit_message_text.await_args.kwargs['text']


from tests.gateway.test_telegram_choice_pages import adapter  # noqa: E402,F401
