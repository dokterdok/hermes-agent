"""Reconnect publication with real temporary ledgers/preflight, never a drain."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import weakref

import pytest

from gateway.authz_mixin import GatewayAuthorizationMixin
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.profile_routing import ProfileRoute
from gateway.run_adapters import GatewayAdapterLifecycleMixin
from gateway.session import SessionSource, SessionStore
from gateway.session_identity import restore_identity
from gateway.session_authorities import SessionAuthorities, owner_scope
from gateway.session_authority import SessionAuthority
from gateway.session_ingress_context import native_callback, register_transport_home
from hermes_constants import get_hermes_home
from hermes_state_runtime import begin_runtime_epoch


class Adapter:
    send_path_degraded = False
    DEGRADED_STATUS_MESSAGE = 'fixture'

    def __init__(self, token):
        self.config = PlatformConfig(enabled=True, token=token)
        self.role_check = None

    async def reauthorize_native_roles(self, source):
        if self.role_check:
            await self.role_check()
        return True


class Runner(GatewayAuthorizationMixin, GatewayAdapterLifecycleMixin):
    def _resolve_profile_home_for_source(self, source):
        return self.homes[source.profile or 'default']

    def _is_user_authorized_for_source(self, source, allow_adapter_delegation=False):
        self.auth_checks.append((source.user_id, source.profile, str(get_hermes_home())))
        return source.user_id in self.allowed and (not source.role_authorized or allow_adapter_delegation)


@pytest.fixture
def state(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    import hermes_state
    root = tmp_path / '.hermes'
    root.mkdir()
    homes = {'default': root, **{name: root / 'profiles' / name for name in ('alpha', 'beta')}}
    for home in homes.values():
        home.mkdir(exist_ok=True, parents=True)
        (home / 'config.yaml').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setattr(hermes_state, 'DEFAULT_DB_PATH', hermes_state._IMPORT_DEFAULT_DB_PATH)
    runner = Runner()
    runner.config = GatewayConfig()
    runner.config.multiplex_profiles = True
    runner.config._runtime_profile_homes = tuple(homes.items())
    runner.config.profile_routes = []
    runner._primary_profile_name = 'default'
    runner.homes = homes
    runner._running, runner._draining = True, False
    runner.adapters = {Platform.TELEGRAM: Adapter('primary'), Platform.DISCORD: Adapter('discord')}
    runner._profile_adapters = {name: {Platform.TELEGRAM: Adapter(name)} for name in ('alpha', 'beta')}
    runner._failed_platforms = {Platform.TELEGRAM: {}}
    runner._profile_failed_platforms = {}
    runner.allowed, runner.auth_checks = {'allowed'}, []
    runner.session_store = SessionStore(root / 'sessions', runner.config)
    runner.session_authorities = SessionAuthorities(root)
    scheduled = []
    authorities = {}
    for name, home in homes.items():
        db = SessionDB(home / 'state.db')
        authority = SessionAuthority(runner, profile_id=str(home), instance_id='fixture', db=db,
                                     epoch=begin_runtime_epoch(db, instance_id='fixture'))
        authority._schedule = lambda ref, a=authority: scheduled.append((a.profile_id, ref.session_id, str(get_hermes_home())))
        authorities[name] = authority
        runner.session_authorities.add(home, authority, name=name)
        register_transport_home(runner, None if name == 'default' else name, home)
    runner.session_authority = authorities['default']
    runner.delivery_router = SimpleNamespace(adapters=runner.adapters)
    runner._sync_voice_mode_state_to_adapter = lambda adapter: None
    runner._bind_voice_input_callback = lambda adapter: None
    runner._update_platform_runtime_status = lambda *args, **kwargs: None
    runner._redeliver_failed_obligations_for_platform = AsyncMock()
    runner._schedule_resume_pending_sessions = lambda **kwargs: None
    runner._safe_adapter_disconnect = AsyncMock()
    monkeypatch.setattr('gateway.channel_directory.build_channel_directory', AsyncMock())
    value = SimpleNamespace(runner=runner, homes=homes, authorities=authorities, scheduled=scheduled)
    yield value
    for authority in authorities.values():
        authority.db.close()
    for db in runner.session_store._db_handles.values():
        db.close()


async def queued(state, name='default', *, transport=None, chat='same-chat', user='allowed', role=False, platform=Platform.TELEGRAM):
    runner = state.runner
    if transport is None:
        transport = name
    profile = None if transport == 'default' else transport
    adapter = (runner.adapters if profile is None else runner._profile_adapters[profile])[platform]
    if name != transport:
        runner.config.profile_routes.append(ProfileRoute('fixture', platform.value, name, chat_id=chat, bot_profile=profile))
    source = SessionSource(platform=platform, chat_id=chat, chat_type='dm', user_id=user,
                           profile=None if name == 'default' else name, role_authorized=role)
    source._transport_adapter_ref = weakref.ref(adapter)
    restore_identity(source, runner=runner, transport_profile=transport)
    event = MessageEvent(text='original queued text', message_id=f'{transport}-{chat}-{user}', source=source)
    authority = state.authorities[name]
    with owner_scope(authority), native_callback(runner, event, state.homes[transport], profile):
        receipt = await authority.admit_native(event)
    state.scheduled.clear()
    return receipt, source


def ledger(state):
    return {name: authority.db._read_all('SELECT * FROM session_admissions ORDER BY seq')
            for name, authority in state.authorities.items()}


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', [None, 'alpha'])
async def test_actual_install_recovers_only_new_adapters_original_queue(state, profile):
    first, _ = await queued(state)
    alpha, _ = await queued(state, 'alpha')
    await queued(state, 'beta')
    await queued(state, platform=Platform.DISCORD, chat='other-platform')
    before = ledger(state)
    runner = state.runner
    name = profile or 'default'
    replacement = Adapter(name if profile else 'primary')
    redelivery_observations = []
    async def redelivery(*args, **kwargs):
        redelivery_observations.append(list(state.scheduled))
    runner._redeliver_failed_obligations_for_platform.side_effect = redelivery
    if profile:
        runner._profile_adapters[profile].pop(Platform.TELEGRAM)
        runner._secondary_reconnect_attempt = AsyncMock(return_value=(replacement, True))
        await runner._run_secondary_profile_reconnect(profile, Platform.TELEGRAM)
    else:
        await runner._install_reconnected_adapter(Platform.TELEGRAM, replacement)
    selected = alpha if profile else first
    assert state.scheduled == [(str(state.homes[name]), selected.ref.session_id, str(state.homes[name]))]
    assert redelivery_observations == [state.scheduled]
    assert ledger(state) == before
    assert all(live.task is None for a in state.authorities.values() for live in a.sessions.values())


@pytest.mark.asyncio
async def test_routed_runtime_recovers_from_receiving_adapter_not_runtime_adapter(state):
    receipt, _ = await queued(state, 'beta', transport='alpha', chat='routed')
    await queued(state, 'beta', chat='own-bot')
    before = ledger(state)
    runner = state.runner
    authority = state.authorities['beta']
    live = authority.sessions[receipt.ref.session_id]
    controls, events, subscribers = live.controls, live.event_stream, live.subscribers
    replacement = Adapter('alpha')
    runner._profile_adapters['alpha'].pop(Platform.TELEGRAM)
    runner._secondary_reconnect_attempt = AsyncMock(return_value=(replacement, True))
    await runner._run_secondary_profile_reconnect('alpha', Platform.TELEGRAM)
    assert state.scheduled == [(str(state.homes['beta']), receipt.ref.session_id, str(state.homes['beta']))]
    assert ledger(state) == before
    assert authority.sessions[receipt.ref.session_id] is live
    assert (live.controls, live.event_stream, live.subscribers) == (controls, events, subscribers)
    # The next existing drain preflight resolves its adapter from live.source.
    # A stale transport ref would fall back to beta's unrelated bot after alpha
    # is replaced, so recording a schedule alone is not sufficient evidence.
    assert runner._delivery_adapter_for(live.source) is replacement
    from gateway.session_envelope import check_native_route
    from hermes_state_runtime import get_session_admission
    payload = get_session_admission(authority.db, admission_id=receipt.admission_id)['payload']
    with owner_scope(authority):
        # Reproduce only the next read-only preflight, not execution or claiming.
        await check_native_route(runner, payload, receipt.ref.session_id, live.source,
                                 runner._delivery_adapter_for(live.source))


@pytest.mark.asyncio
async def test_reconnect_claims_original_once_on_receiving_bot(state, monkeypatch):
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    from hermes_state_runtime import get_session_admission

    receipt, _ = await queued(state, 'beta', transport='alpha', chat='claim-on-alpha')
    runner = state.runner
    authority = state.authorities['beta']
    original = get_session_admission(authority.db, admission_id=receipt.admission_id)
    replacement = Adapter('alpha')
    runner._profile_adapters['alpha'][Platform.TELEGRAM] = replacement
    executed = AsyncMock(return_value='inert answer')
    monkeypatch.setattr('gateway.session_finite.execute_finite_admission', executed)
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement, profile='alpha')
    assert state.scheduled == [(str(state.homes['beta']), receipt.ref.session_id, str(state.homes['beta']))]
    live = authority.sessions[receipt.ref.session_id]
    assert runner._delivery_adapter_for(live.source) is replacement
    with owner_scope(authority):
        await authority._drain(receipt.ref)
        await authority._drain(receipt.ref)
    executed.assert_awaited_once()
    settled = get_session_admission(authority.db, admission_id=receipt.admission_id)
    assert settled['status'] == 'terminal' and settled['outcome'] == 'completed'
    assert settled['payload'] == original['payload']


@pytest.mark.asyncio
async def test_pinned_receiving_bot_offline_never_borrows_runtime_bot(state):
    from gateway.session_native_reconnect import recover_adapter_native_inputs

    receipt, _ = await queued(state, 'beta', transport='alpha', chat='offline-owner')
    runner = state.runner
    entry = runner.session_store.lookup_by_session_key(
        state.authorities['beta'].sessions[receipt.ref.session_id].route)
    assert entry.transport_profile == 'alpha'
    before = ledger(state)
    runner._profile_adapters['alpha'].pop(Platform.TELEGRAM)
    restored = runner._restored_source(entry)
    assert runner._delivery_adapter_for(restored) is None
    # beta's bot is up but cannot claim the queue admitted on alpha's credential.
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM,
                                        runner._profile_adapters['beta'][Platform.TELEGRAM], profile='beta')
    assert state.scheduled == [] and ledger(state) == before
    replacement = Adapter('alpha')
    runner._profile_adapters['alpha'][Platform.TELEGRAM] = replacement
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement, profile='alpha')
    assert [item[1] for item in state.scheduled] == [receipt.ref.session_id]
    assert ledger(state) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal', ['sender', 'credential', 'route', 'unknown', 'unpublished'])
async def test_reconnect_refusals_leave_original_admissions_untouched(state, refusal):
    receipt, _ = await queued(state)
    runner = state.runner
    replacement = Adapter('primary')
    runner.adapters[Platform.TELEGRAM] = replacement
    if refusal == 'sender':
        runner.allowed.clear()
    elif refusal == 'credential':
        replacement.config.token = 'different'
    elif refusal == 'route':
        runner.session_store._entries.clear()
    elif refusal == 'unknown':
        db = state.authorities['default'].db
        db._execute_write(lambda conn: conn.execute("UPDATE session_admissions SET status='unknown' WHERE admission_id=?", (receipt.admission_id,)))
        # A different queued row behind an unresolved fixture row must not wake it.
        from hermes_state_runtime import admit_session_input, get_session_admission
        row = get_session_admission(db, admission_id=receipt.admission_id)
        admit_session_input(db, epoch=state.authorities['default'].epoch, principal_id='fixture',
                            session_id=receipt.ref.session_id, request_id='following', payload=row['payload'])
    else:
        runner.adapters[Platform.TELEGRAM] = Adapter('primary')
    before = ledger(state)
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement)
    assert state.scheduled == []
    assert ledger(state) == before


@pytest.mark.asyncio
async def test_late_old_adapter_role_reply_cannot_schedule_or_replace_winner(state):
    receipt, _ = await queued(state, role=True)
    runner = state.runner
    old, winner = Adapter('primary'), Adapter('primary')
    runner.adapters[Platform.TELEGRAM] = old
    async def replace_during_check():
        runner.adapters[Platform.TELEGRAM] = winner
    old.role_check = replace_during_check
    before = ledger(state)
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, old)
    assert state.scheduled == [] and runner.adapters[Platform.TELEGRAM] is winner
    assert ledger(state) == before
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, winner)
    assert [item[1] for item in state.scheduled] == [receipt.ref.session_id]


@pytest.mark.asyncio
async def test_actual_sender_policy_is_rechecked_in_receiving_home_not_routed_home(state, monkeypatch):
    from gateway.run import GatewayRunner
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    # The lightweight runner omits __init__, which sets this process-mode flag.
    monkeypatch.setattr('agent.secret_scope._MULTIPLEX_ACTIVE', True)
    runner = state.runner
    runner._under_authorization_profile = GatewayRunner._under_authorization_profile.__get__(runner)
    runner._is_user_authorized_for_source = GatewayRunner._is_user_authorized_for_source.__get__(runner)
    receiving_env = state.homes['alpha'] / '.env'
    receiving_env.write_text('TELEGRAM_ALLOWED_USERS=allowed\n', encoding='utf-8')
    (state.homes['beta'] / '.env').write_text('TELEGRAM_ALLOWED_USERS=runtime-only\n', encoding='utf-8')
    receipt, _ = await queued(state, 'beta', transport='alpha', chat='real-policy')
    replacement = Adapter('alpha')
    runner._profile_adapters['alpha'][Platform.TELEGRAM] = replacement
    before = ledger(state)
    receiving_env.write_text('TELEGRAM_ALLOWED_USERS=now-revoked\n', encoding='utf-8')
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement, profile='alpha')
    assert state.scheduled == [] and ledger(state) == before
    receiving_env.write_text('TELEGRAM_ALLOWED_USERS=allowed\n', encoding='utf-8')
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement, profile='alpha')
    assert state.scheduled == [(str(state.homes['beta']), receipt.ref.session_id, str(state.homes['beta']))]
    assert ledger(state) == before


@pytest.mark.asyncio
async def test_secondary_loser_does_not_recover_the_winning_adapters_queue(state):
    await queued(state, 'alpha')
    runner = state.runner
    winner = runner._profile_adapters['alpha'][Platform.TELEGRAM]
    loser = Adapter('alpha')
    runner._secondary_reconnect_attempt = AsyncMock(return_value=(loser, True))
    before = ledger(state)
    await runner._run_secondary_profile_reconnect('alpha', Platform.TELEGRAM)
    assert state.scheduled == [] and ledger(state) == before
    assert runner._profile_adapters['alpha'][Platform.TELEGRAM] is winner
    runner._redeliver_failed_obligations_for_platform.assert_not_called()


@pytest.mark.asyncio
async def test_missing_authorities_and_admission_withdrawal_are_quiet(state):
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    await queued(state)
    runner = state.runner
    before = ledger(state)
    runner._draining = True  # Static refusal flag only, no lifecycle operation.
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, runner.adapters[Platform.TELEGRAM])
    assert state.scheduled == [] and ledger(state) == before
    runner._draining = False
    runner.session_authorities = SessionAuthorities(state.homes['default'])
    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, runner.adapters[Platform.TELEGRAM])
    assert state.scheduled == [] and ledger(state) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('active', ['task', 'started', 'unknown'])
async def test_reconnect_never_rebinds_or_rearms_active_or_unresolved_work(state, active):
    import asyncio
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    receipt, _ = await queued(state, 'beta', transport='alpha', chat='retained-binding')
    authority = state.authorities['beta']
    live = authority.sessions[receipt.ref.session_id]
    original_source, original_route = live.source, live.route
    replacement = Adapter('alpha')
    state.runner._profile_adapters['alpha'][Platform.TELEGRAM] = replacement
    if active == 'task':
        live.task = asyncio.get_running_loop().create_future()  # No running coroutine.
    else:
        from hermes_state_runtime import admit_session_input, get_session_admission
        authority.db._execute_write(lambda conn: conn.execute(
            'UPDATE session_admissions SET status=?,generation=1 WHERE admission_id=?', (active, receipt.admission_id)))
        row = get_session_admission(authority.db, admission_id=receipt.admission_id)
        admit_session_input(authority.db, epoch=authority.epoch, principal_id='fixture',
            session_id=receipt.ref.session_id, request_id='queued-follower', payload=row['payload'])
    before = ledger(state)
    await recover_adapter_native_inputs(state.runner, Platform.TELEGRAM, replacement, profile='alpha')
    assert state.scheduled == [] and ledger(state) == before
    assert live.source is original_source and live.route == original_route


@pytest.mark.asyncio
@pytest.mark.parametrize('withdraw', ['sender', 'route', 'adapter', 'authority', 'policy_config'])
async def test_earlier_sender_withdrawn_while_later_role_waits_preserves_paused_batch(state, withdraw):
    from gateway.session_native_reconnect import recover_adapter_native_inputs
    state.runner.allowed.add('other')
    first, _ = await queued(state, 'alpha', chat='shared-dm', user='allowed')
    second, _ = await queued(state, 'alpha', chat='shared-dm', user='other', role=True)
    assert first.ref == second.ref
    authority = state.authorities['alpha']
    live = authority.sessions[first.ref.session_id]
    source, route = live.source, live.route
    controls, stream, subscribers = live.controls, live.event_stream, live.subscribers
    live.subscribers['retained'] = object()
    previous_entries = dict(state.runner.session_store._entries)
    replacement = Adapter('alpha')
    state.runner._profile_adapters['alpha'][Platform.TELEGRAM] = replacement
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def held_last_role_check():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await asyncio.wait_for(release.wait(), 3)

    replacement.role_check = held_last_role_check
    before = ledger(state)
    pending = asyncio.create_task(recover_adapter_native_inputs(
        state.runner, Platform.TELEGRAM, replacement, profile='alpha'))
    await asyncio.wait_for(entered.wait(), 3)
    if withdraw == 'sender':
        state.runner.allowed.remove('allowed')
    elif withdraw == 'route':
        state.runner.session_store._entries.clear()
    elif withdraw == 'adapter':
        state.runner._profile_adapters['alpha'][Platform.TELEGRAM] = Adapter('alpha')
    elif withdraw == 'authority':
        registry = state.runner.session_authorities
        key = next(key for key, owned in registry._by_key.items() if owned is authority)
        registry._by_key.pop(key)
        registry._names.pop(key)
    else:
        replacement.config.extra['dm_policy'] = 'disabled'
    release.set()
    await asyncio.wait_for(pending, 3)
    assert calls == 2 and state.scheduled == []
    assert live.source is source and live.route == route
    assert (live.controls, live.event_stream, live.subscribers) == (controls, stream, subscribers)
    assert 'retained' in live.subscribers and ledger(state) == before

    if withdraw == 'sender':
        state.runner.allowed.add('allowed')
    elif withdraw == 'route':
        state.runner.session_store._entries.update(previous_entries)
    elif withdraw == 'adapter':
        state.runner._profile_adapters['alpha'][Platform.TELEGRAM] = replacement
    elif withdraw == 'authority':
        state.runner.session_authorities.add(state.homes['alpha'], authority, name='alpha')
    else:
        replacement.config.extra.pop('dm_policy')
    replacement.role_check = None
    await recover_adapter_native_inputs(state.runner, Platform.TELEGRAM, replacement, profile='alpha')
    assert state.scheduled == [(str(state.homes['alpha']), first.ref.session_id, str(state.homes['alpha']))]
    assert live.source is not source and live.route == route
    assert (live.controls, live.event_stream, live.subscribers) == (controls, stream, subscribers)
    assert ledger(state) == before
