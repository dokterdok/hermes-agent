"""Real canonical stores and Home consent through a receiving-adapter command surface."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest

from gateway import hosted_rooms
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.group_chat_slash import GroupChatSlashCommandsMixin
from gateway.group_home_consent import disclosure_stamp
from gateway.hosted_room_messaging import current_room_backend
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from gateway.session_authorities import SessionAuthorities, owner_scope
from gateway.session_authority import SessionAuthority
from gateway.session_contract import Principal
from gateway.session_group_home_access import dispatch_home_access, home_access_granted
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_constants import get_hermes_home
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch


class Adapter:
    typed_command_prefix = '!'
    def __init__(self, config):
        self.config = config


class PickerAdapter(Adapter):
    async def send_choice_picker(self, **kwargs):
        self.picker = kwargs
        return SimpleNamespace(success=True)


class Runner(GroupChatSlashCommandsMixin):
    def _is_user_authorized_for_source(self, source):
        return str(get_hermes_home()) == str(self.receiving_home) and source.user_id == '42'


@pytest.fixture
def view(tmp_path, monkeypatch):
    root = tmp_path / '.hermes'
    root.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    runner = Runner()
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.session_authorities = SessionAuthorities(root)
    runner._draining = False
    runner._native_transport_homes = {}
    runner.adapters = {}
    runner._profile_adapters = {}
    owners = {}
    with ExitStack() as stack:
        for name, home in [('default', root), ('home', root / 'profiles' / 'home'), ('worker', root / 'profiles' / 'worker')]:
            home.mkdir(parents=True, exist_ok=True)
            (home / 'config.yaml').write_text('model: {}\n')
            db = stack.enter_context(SessionDB(home / 'state.db'))
            authority = SessionAuthority(runner, profile_id=str(home), instance_id=name, db=db,
                epoch=begin_runtime_epoch(db, instance_id=name))
            runner.session_authorities.add(home, authority, name=name)
            with owner_scope(authority):
                service = CanonicalHostedRoomService(authority, None)
                authority.hosted_room_service = service
                service.authorize_room('native-owner', 'room', create=True)
                hosted_rooms.create_room(db.db_path, room_id='room', name=name + ' secret',
                    authority_gateway_id=hosted_rooms.local_authority_gateway_id(), members=[
                        {'member_id': 'pm', 'profile': 'pm', 'handle': 'pm'},
                        {'member_id': 'builder', 'profile': 'builder', 'handle': 'builder'}])
            owners[name] = authority
        runner.session_authority = owners['default']
        receiving = owners['home']
        runner.receiving_home = Path(receiving.profile_id)
        config = PlatformConfig(enabled=True, home_channel=HomeChannel(Platform.TELEGRAM, '42', 'Home', user_id='42'),
                                extra={'allow_from': ['42'], 'allow_admin_from': ['42']})
        adapter = Adapter(config)
        runner._profile_adapters['home'] = {Platform.TELEGRAM: adapter}
        runner._native_transport_homes['home'] = runner.receiving_home
        source = SessionSource(Platform.TELEGRAM, '42', user_id='42', profile='worker')
        source._transport_adapter_ref = weakref.ref(adapter)
        source._authorization_profile_home = runner.receiving_home
        raw = SimpleNamespace(chat=SimpleNamespace(type='private', id=42), from_user=SimpleNamespace(id=42, is_bot=False))
        event = MessageEvent(text='/group list', source=source, raw_message=raw)
        actor = Principal('native-owner', receiving.profile_id, frozenset({'session:control'}), 'native')
        def consent(enabled=True):
            with owner_scope(receiving):
                return dispatch_home_access(receiving, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': enabled})
        yield SimpleNamespace(runner=runner, receiving=receiving, owners=owners, adapter=adapter,
                              event=event, consent=consent, actor=actor)


@pytest.mark.asyncio
async def test_actual_receiving_home_not_execution_profile_and_no_implicit_consent(view):
    assert disclosure_stamp(view.runner, view.event) is not None
    result = await view.runner._handle_group_command(view.event)
    assert 'home secret' not in result and 'worker secret' not in result
    with owner_scope(view.receiving):
        assert not home_access_granted(view.receiving, 'room')
    view.consent()
    result = await view.runner._handle_group_command(view.event)
    assert 'home secret' in result and 'worker secret' not in result and 'default secret' not in result
    assert '!group' in result
    for command in ('/group 1', '/group 1 bots', '/group 1 bot 2', '/group 1 files'):
        result = await view.runner._handle_group_command(replace(view.event, text=command))
        assert 'couldn’t' not in result.lower() and 'could not' not in result.lower()
        assert 'home secret' in result
        if command == '/group 1':
            assert '!group 1 send <message>' in result
    assert not view.receiving.sessions
    assert view.receiving.db._read_all('SELECT * FROM session_admissions') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['bot', 'edited', 'sender_chat', 'anonymous', 'relay', 'foreign_home', 'admin', 'receiver', 'home', 'audience'])
async def test_disclosure_gates_fail_closed(view, change):
    view.consent()
    if change == 'bot': view.event.source.is_bot = True
    elif change == 'edited': view.event.raw_message.edit_date = 'edited'
    elif change == 'sender_chat': view.event.raw_message.sender_chat = object()
    elif change == 'anonymous': view.event.source.user_id = 'anonymous'
    elif change == 'relay': view.event.source.delivered_via_upstream_relay = True
    elif change == 'foreign_home': view.event.source._authorization_profile_home = Path(view.owners['worker'].profile_id)
    elif change == 'admin': view.adapter.config.extra['allow_admin_from'] = ['another-user']
    elif change == 'receiver': view.runner._profile_adapters['home'][Platform.TELEGRAM] = Adapter(view.adapter.config)
    elif change == 'home': view.adapter.config.home_channel.thread_id = 'another-topic'
    else:
        view.event.source.chat_type = 'group'
        view.event.raw_message.chat.type = 'group'
    result = await view.runner._handle_group_command(view.event)
    assert 'home secret' not in result and disclosure_stamp(view.runner, view.event) is None


@pytest.mark.asyncio
async def test_revocation_during_read_and_picker_selection_does_not_disclose_or_retarget(view, monkeypatch):
    view.consent()
    original = view.receiving.hosted_room_service.status
    def status(room_id):
        result = original(room_id)
        view.consent(False)
        return result
    monkeypatch.setattr(view.receiving.hosted_room_service, 'status', status)
    result = await view.runner._handle_group_command(replace(view.event, text='/group 1'))
    assert 'home secret' not in result
    monkeypatch.setattr(view.receiving.hosted_room_service, 'status', original)
    view.consent()
    picker = PickerAdapter(view.adapter.config)
    view.runner._profile_adapters['home'][Platform.TELEGRAM] = picker
    view.event.source._transport_adapter_ref = weakref.ref(picker)
    assert await view.runner._handle_group_command(replace(view.event, text='/group')) is None
    callback = picker.picker['on_choice_selected']
    value = picker.picker['choices'][0]['value']
    view.consent(False)
    result = await callback('42', value)
    assert 'home secret' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['stop', 'retry', 'approve', 'deny', 'discard'])
async def test_other_mutations_are_not_activated(view, action):
    view.consent()
    before = view.receiving.db._read_all('SELECT * FROM hosted_room_events')
    result = await view.runner._handle_group_command(replace(view.event, text='/group 1 ' + action))
    assert 'not available yet' in result
    assert view.receiving.db._read_all('SELECT * FROM hosted_room_events') == before
    assert view.receiving.db._read_all('SELECT * FROM session_admissions') == []
