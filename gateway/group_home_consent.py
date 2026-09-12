"""Requester-bound Home audience consent; never grants native room permission.

Confirmation state/persistence fencing is forwardported from accepted 5ee4a941.
"""
import asyncio
import contextvars
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from functools import wraps
from threading import RLock

from gateway.group_chat_messages import text
from gateway.group_chat_policy import group_command_prefix, group_policy_for_source, home_config, receiving_group_context
from gateway.group_home_identity import acknowledgement, home_identity, is_home_control_source, private_event, trusted_person
from gateway.session_authorities import owner_scope

PROCEED = object()
PROMPT_SECONDS = 120
MAX_PENDING = 128
_active_confirmation = contextvars.ContextVar('group_home_confirmation', default=None)


class DisclosureChanged(PermissionError):
    pass


def _single_operator(runner, event, context):
    """Retain the source's legacy single-contact census, never allow-all inference."""
    from gateway.authz_mixin import _auth_env, _coerce_allow_set, _platform_authorization_env_names
    source = event.source
    users, allow_all = _platform_authorization_env_names(source.platform)
    extra = getattr(context.config, 'extra', None) or {}
    candidates = set(_coerce_allow_set(extra.get('allow_from')))
    candidates.update(_coerce_allow_set(_auth_env(users)))
    candidates.update(_coerce_allow_set(_auth_env('GATEWAY_ALLOWED_USERS')))
    if any(_auth_env(name).lower() in {'1', 'true', 'yes'} for name in ('GATEWAY_ALLOW_ALL_USERS', allow_all) if name):
        return False
    store = getattr(runner, 'pairing_store', None)
    if store is not None:
        try:
            candidates.update(str(row.get('user_id') or '').strip() for row in store.list_approved(source.platform.value))
        except Exception:
            return False
    candidates.discard('')
    if not candidates or '*' in candidates:
        return False
    matcher = getattr(store, '_user_ids_match', None)
    if callable(matcher):
        return all(matcher(source.platform.value, candidate, str(source.user_id)) for candidate in candidates)
    return candidates == {str(source.user_id)}


def disclosure_stamp(runner, event, *, require_audience=True):
    if not trusted_person(event) or not _confirmation_allows_output(runner):
        return None
    context = receiving_group_context(runner, event.source)
    if context is None:
        return None
    try:
        with owner_scope(context.authority):
            config = home_config(context)
            home = config.get_home_channel(event.source.platform)
            if home is None or not is_home_control_source(config, event.source):
                return None
            home_before = home_identity(home)
            authorize = getattr(runner, '_is_user_authorized_for_source', None)
            if not callable(authorize) or authorize(event.source) is not True:
                return None
            policy = group_policy_for_source(runner, event.source)
            private = private_event(event)
            if policy.enabled:
                if not policy.is_admin(event.source.user_id) or not is_home_control_source(config, event.source, require_owner_identity=True):
                    return None
            elif not private or not _single_operator(runner, event, context):
                return None
            if not private:
                if not getattr(home, 'selection_id', None):
                    return None
                if require_audience and getattr(home, 'group_audience_ack', None) != acknowledgement(home):
                    return None
            current = receiving_group_context(runner, event.source)
            if current is None or current.adapter is not context.adapter or current.authority is not context.authority:
                return None
            if home_before != home_identity(home_config(current).get_home_channel(event.source.platform)):
                return None
            source = event.source
            return (str(context.home), id(context.adapter), id(context.authority), context.authority.epoch,
                    home_before, str(source.user_id), str(source.chat_id), str(source.thread_id or ''),
                    str(source.scope_id or ''), private)
    except Exception:
        return None


def require_current(runner, event, expected):
    from gateway.group_chat_work import require_read_active
    require_read_active()
    if expected is None or disclosure_stamp(runner, event) != expected:
        raise DisclosureChanged('Group Chat access changed. Run the command again.')


def denial(runner, event):
    from gateway.group_chat_provenance import is_machine_authored, is_message_edit
    key = 'people' if is_machine_authored(event) else 'edited' if is_message_edit(event) else 'setup'
    return text('group_home', key, command_prefix=group_command_prefix(runner, event.source))


def protect_group_result(function):
    @wraps(function)
    async def guarded(runner, event, *args, **kwargs):
        prepared = await prepare_group_access(runner, event)
        if prepared is not PROCEED:
            return prepared
        stamp = disclosure_stamp(runner, event)
        if stamp is None:
            return denial(runner, event)
        try:
            result = await function(runner, event, *args, **kwargs)
            require_current(runner, event, stamp)
            return result
        except DisclosureChanged:
            return denial(runner, event)
    return guarded


def _pending(runner):
    pending = getattr(runner, "_group_home_confirmations", None)
    if not isinstance(pending, OrderedDict):
        pending = runner._group_home_confirmations = OrderedDict()
    for key, value in list(pending.items()):
        if value.deadline <= time.monotonic():
            _retire(runner, value)
    return pending


@dataclass
class Confirmation:
    key: tuple
    home: tuple
    stamp: tuple
    token: str
    deadline: float
    adapter: object
    context: contextvars.Context
    state: str = "pending"
    disclose: bool = True
    commit_started: bool = False
    lock: object = field(default_factory=RLock, repr=False)


def _current(runner, pending):
    return getattr(runner, "_group_home_confirmations", {}).get(pending.key) is pending


def _confirmation_allows_output(runner):
    active = _active_confirmation.get()
    if active is None or active[0] is not runner:
        return True
    pending = active[1]
    with pending.lock:
        return (
            _current(runner, pending)
            and pending.disclose
            and pending.deadline > time.monotonic()
        )


def _discard(runner, pending):
    if _current(runner, pending):
        runner._group_home_confirmations.pop(pending.key)


def _retire(runner, pending):
    # Never hold this short state lock across config reads/writes or an await.
    with pending.lock:
        pending.disclose = False
        if pending.state == "committing":
            return True
        if pending.state in {"pending", "claimed"}:
            pending.state = "cancelled"
        _discard(runner, pending)
        return pending.commit_started


def _text(runner, event, key):
    return text('group_home', key, command_prefix=group_command_prefix(runner, event.source))


def _key(runner, event):
    from gateway.group_home_identity import home_thread_from_source

    context = receiving_group_context(runner, event.source)
    if context is None:
        return None
    source = event.source
    return (str(context.home), source.platform.value, str(source.chat_id),
            str(home_thread_from_source(source) or ''), str(source.user_id or ''),
            str(source.scope_id or ''))


def _cancel(runner, event, pending=None):
    if pending is None:
        pending = _pending(runner).get(_key(runner, event))
    if pending is not None:
        return _text(runner, event, 'cancel_late' if _retire(runner, pending) else 'cancel')
    context = receiving_group_context(runner, event.source)
    home = getattr(context.config, 'home_channel', None) if context else None
    accepted = (home is not None and not private_event(event)
                and is_home_control_source(home_config(context), event.source, require_owner_identity=True)
                and home.group_audience_ack == acknowledgement(home))
    return _text(runner, event, 'cancel_late' if accepted else 'cancel')


def _check(runner, event, pending):
    with pending.lock:
        if not _current(runner, pending) or pending.state in {'cancelled', 'failed'}:
            raise PermissionError
    if (pending.deadline <= time.monotonic() or _key(runner, event) != pending.key
            or disclosure_stamp(runner, event, require_audience=False) != pending.stamp):
        raise PermissionError
    context = receiving_group_context(runner, event.source)
    if context is None or context.adapter is not pending.adapter:
        raise PermissionError
    from hermes_state_runtime import _epoch
    with context.authority.db._read_ctx() as conn:
        _epoch(conn, context.authority.epoch)
    home = context.config.home_channel
    if home is None or home_identity(home) != pending.home:
        raise PermissionError
    return home


def _persist(runner, event, pending):
    from gateway.group_home_selection import receiving_config_scope

    try:
        context = receiving_group_context(runner, event.source)
        if context is None:
            raise PermissionError
        with receiving_config_scope(context):
            return _persist_locked(runner, event, pending)
    except BaseException:
        with pending.lock:
            if pending.state in {'claimed', 'committing'}:
                pending.state = 'failed'
        raise
    finally:
        with pending.lock:
            if not pending.disclose and pending.state != 'committing':
                _discard(runner, pending)


def _persist_locked(runner, event, pending):
    from gateway.config import HomeChannel
    from gateway.group_home_selection import require_saved_policy
    from hermes_cli.config import _CONFIG_LOCK, load_config, save_config

    with _CONFIG_LOCK:
        live = _check(runner, event, pending)
        config = load_config()
        platform = config.get('platforms', {}).get(event.source.platform.value, {})
        raw = platform.get('home_channel')
        if not isinstance(raw, dict) or home_identity(HomeChannel.from_dict(raw)) != pending.home:
            raise PermissionError
        require_saved_policy(platform, event, audience=True)
        ack = acknowledgement(live)
        _check(runner, event, pending)
        with pending.lock:
            if not _current(runner, pending) or pending.state != 'claimed':
                raise PermissionError
            # Cancellation cannot undo I/O once it starts. Keep the lock free
            # during disk work so cancellation can suppress the continuation.
            pending.state = 'committing'
            pending.commit_started = True
        platform['home_channel'] = {**raw, 'group_audience_ack': ack}
        save_config(config)
        saved_platform = load_config().get('platforms', {}).get(event.source.platform.value, {})
        saved = saved_platform.get('home_channel', {})
        if (saved.get('group_audience_ack') != ack
                or home_identity(HomeChannel.from_dict(saved)) != pending.home):
            raise RuntimeError('save not confirmed')
        require_saved_policy(saved_platform, event, audience=True)
        current = _check(runner, event, pending)
        current.group_audience_ack = ack
        with pending.lock:
            pending.state = 'committed'


async def _confirm(runner, event, pending, *, native=False):
    with pending.lock:
        if not _current(runner, pending) or pending.state != 'pending':
            return _text(runner, event, 'expired')
        pending.state = 'claimed'
    try:
        await asyncio.wait_for(asyncio.to_thread(_persist, runner, event, pending),
                               timeout=max(0, pending.deadline - time.monotonic()))
        with pending.lock:
            if not _current(runner, pending) or not pending.disclose:
                return _text(runner, event, 'cancel_late' if pending.commit_started else 'expired')
        _check(runner, event, pending)
        if disclosure_stamp(runner, event) is None:
            return _text(runner, event, 'expired')
        followup = replace(event, text=group_command_prefix(runner, event.source) + 'group list')
        token = _active_confirmation.set((runner, pending))
        menu = None
        try:
            if native:
                from gateway.hosted_room_messaging import current_room_backend
                from gateway.group_chat_menu import GroupMenu
                stamp = disclosure_stamp(runner, event)
                backend = current_room_backend(runner, event, stamp)
                menu = GroupMenu(runner, event, backend, group_command_prefix(runner, event.source) + 'group', stamp)
                result = await menu.groups()
            else:
                result = await runner._handle_rooms_command(followup)
        finally:
            _active_confirmation.reset(token)
        with pending.lock:
            if (not _current(runner, pending) or not pending.disclose
                    or pending.deadline <= time.monotonic()):
                return _text(runner, event, 'cancel_late')
            if menu is not None:
                pending.menu = menu
        return result or _text(runner, event, 'chooser')
    except asyncio.CancelledError:
        _retire(runner, pending)
        raise
    except (PermissionError, asyncio.TimeoutError):
        _retire(runner, pending)
        return _text(runner, event, 'expired')
    except Exception:
        return _text(runner, event, 'failed')
    finally:
        with pending.lock:
            if pending.state != 'committing':
                _discard(runner, pending)


async def prepare_group_access(runner, event):
    if not _confirmation_allows_output(runner):
        return _text(runner, event, 'expired')
    if not trusted_person(event):
        return denial(runner, event)
    query = runner._group_chat_command_args(event).strip().casefold()
    if query in {'help', 'usage', '?'}:
        return runner._group_chat_help(group_command_prefix(runner, event.source) + 'group')
    if query == 'cancel':
        from gateway.group_chat_menu import cancel_navigation
        await cancel_navigation(runner, event)
        # Retire only this requester's existing read chooser, without a config
        # read that could block cancellation behind the in-flight writer.
        context = receiving_group_context(runner, event.source)
        tokens = getattr(runner, '_group_read_choice_tokens', {})
        source = event.source
        location = (str(source.user_id), str(source.chat_id), str(source.thread_id or ''), str(source.scope_id or ''))
        for stamp in list(tokens):
            if isinstance(stamp, tuple) and len(stamp) == 10 and context is not None:
                if stamp[0] == str(context.home) and stamp[5:9] == location:
                    tokens.pop(stamp, None)
        return _cancel(runner, event)
    key = _key(runner, event)
    if key is None:
        return denial(runner, event)
    words = query.split()
    if words and words[0] == 'confirm':
        pending = _pending(runner).get(key)
        if (pending is None or len(words) != 2
                or len(words[1]) != 32 or not words[1].isascii()
                or not secrets.compare_digest(words[1], pending.token)):
            return _text(runner, event, 'expired')
        return await _confirm(runner, event, pending)
    previous = _pending(runner).get(key)
    active = _active_confirmation.get()
    same_confirmation = active is not None and active[0] is runner and active[1] is previous
    if previous is not None and not same_confirmation:
        with previous.lock:
            committing = previous.state == 'committing'
            _retire(runner, previous)
            if committing:
                return _text(runner, event, 'saving')
    stamp = await asyncio.to_thread(disclosure_stamp, runner, event, require_audience=False)
    if stamp is None:
        return denial(runner, event)
    if disclosure_stamp(runner, event) is not None:
        return PROCEED
    context = receiving_group_context(runner, event.source)
    if context is None:
        return denial(runner, event)
    pending = Confirmation(key, home_identity(context.config.home_channel), stamp,
        secrets.token_hex(16), time.monotonic() + PROMPT_SECONDS, context.adapter, contextvars.copy_context())
    prompts = _pending(runner)
    prompts[key] = pending
    while len(prompts) > MAX_PENDING:
        oldest = next(iter(prompts.values()))
        _retire(runner, oldest)
        if _current(runner, oldest):
            _retire(runner, pending)
            return _text(runner, event, 'saving')
    command = group_command_prefix(runner, event.source) + 'group'
    fallback = (_text(runner, event, 'warning')
                + f"\n{_text(runner, event, 'continue')}: {command} confirm {pending.token}"
                + f"\n{_text(runner, event, 'private')}: {command} cancel")
    picker = getattr(type(context.adapter), 'send_choice_picker', None)
    if not callable(picker) or getattr(type(context.adapter), 'supports_choice_pages', False) is not True:
        return fallback

    async def selected(chat_id, value):
        async def apply():
            destination = event.source.chat_id
            if event.source.platform.value == 'discord' and event.source.thread_id:
                destination = event.source.thread_id
            menu = getattr(pending, 'menu', None)
            if menu is not None and str(chat_id) == str(destination):
                return await menu.choose(chat_id, value)
            if (str(chat_id) != str(destination) or _pending(runner).get(key) is not pending
                    or value not in {pending.token + ':yes', pending.token + ':no'}):
                return _text(runner, event, 'expired')
            if value.endswith(':no'):
                return _cancel(runner, event, pending)
            return await _confirm(runner, event, pending, native=True)
        return await asyncio.create_task(apply(), context=pending.context.copy())

    from gateway.platforms.base import _thread_metadata_for_event
    metadata = {**(_thread_metadata_for_event(event) or {}),
                'hermes_profile': context.profile, 'requester_user_id': str(event.source.user_id),
                'choice_pages': True}
    try:
        _check(runner, event, pending)
        sent = await picker(context.adapter, chat_id=event.source.chat_id,
            title=_text(runner, event, 'warning'),
            choices=[{'label': _text(runner, event, 'continue'), 'value': pending.token + ':yes'},
                     {'label': _text(runner, event, 'private'), 'value': pending.token + ':no'}],
            session_key='group-home:' + pending.token, on_choice_selected=selected, metadata=metadata)
        _check(runner, event, pending)
        return None if getattr(sent, 'success', False) is True else fallback
    except asyncio.CancelledError:
        _retire(runner, pending)
        raise
    except PermissionError:
        _retire(runner, pending)
        return _text(runner, event, 'expired')
    except Exception:
        return fallback
