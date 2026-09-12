"""Exact receiving-Home selection and config CAS; no room-owner enrollment.

Legacy destination/topic persistence is retained from accepted 5ee4a941.
"""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import secrets

from gateway.config import HomeChannel, PlatformConfig
from gateway.group_chat_policy import group_command_prefix, group_policy_for_source, receiving_group_context
from gateway.group_home_identity import home_identity, logical_home_source, trusted_person
from gateway.group_chat_messages import text
from gateway.session_authorities import owner_scope
from gateway.slash_access import policy_from_extra


@contextmanager
def receiving_config_scope(context):
    from hermes_cli.config import get_config_path
    from hermes_state_runtime import _epoch

    with owner_scope(context.authority):
        if get_config_path().resolve().parent != context.home:
            raise PermissionError('Receiving Home config is unavailable')
        with context.authority.db._read_ctx() as conn:
            _epoch(conn, context.authority.epoch)
        yield


def require_saved_policy(platform, event, *, audience=False):
    scope = 'dm' if str(event.source.chat_type).casefold() in {'dm', 'direct', 'private'} else 'group'
    policy = policy_from_extra(PlatformConfig.from_dict(platform).extra, scope)
    if audience:
        if not policy.enabled or not policy.is_admin(event.source.user_id):
            raise PermissionError('Shared Home admin changed')
    elif not policy.can_run(event.source.user_id, 'sethome'):
        raise PermissionError('Home selection permission changed')


def _selection_stamp(runner, event):
    if not trusted_person(event):
        raise PermissionError('Authenticated native person required')
    context = receiving_group_context(runner, event.source)
    if context is None:
        raise PermissionError('Receiving Home unavailable')
    with receiving_config_scope(context):
        authorize = getattr(runner, '_is_user_authorized_for_source', None)
        if not callable(authorize) or authorize(event.source) is not True:
            raise PermissionError('Home selection is not authorized')
        policy = group_policy_for_source(runner, event.source)
        if not policy.can_run(event.source.user_id, 'sethome'):
            raise PermissionError('Home selection is not authorized')
        home = context.config.home_channel
        source = logical_home_source(event)
        return (str(context.home), id(context.adapter), id(context.config), id(context.authority),
                context.authority.epoch, home_identity(home) if home else None,
                source.platform.value, str(source.chat_id), str(source.thread_id or ''),
                str(source.user_id), str(source.scope_id or ''))


def _save_legacy_home(values):
    """Keep the legacy destination and topic in one atomic file replacement."""
    from hermes_cli import config

    for key, value in values.items():
        if config._env_write_blocked(key, "set"):
            raise RuntimeError("legacy Home delivery setting is managed")
        config.validate_env_var_name_for_write(key)
        if "\n" in value or "\r" in value:
            raise ValueError("Home destination contains a newline")
    config.ensure_hermes_home()
    path = config.get_env_path()
    lines = config._read_env_lines(path) if path.exists() else []
    lines = [line for line in lines if not any(
        config._env_line_defines_key(line, key) for key in values
    )]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.extend(f"{key}={config._quote_env_value(value)}\n" for key, value in values.items())
    config._write_env_lines(path, lines, preserve_mode=path.exists())
    for key, value in values.items():
        config._publish_env_value(key, value)
    config.invalidate_env_cache()


async def set_home(runner, event):
    """Replace delivery selection, not native room permission or audience consent."""
    prefix = group_command_prefix(runner, event.source)
    context = receiving_group_context(runner, event.source)
    if context is None:
        return text('group_home', 'home_connector', command_prefix=prefix)
    try:
        expected = await asyncio.to_thread(_selection_stamp, runner, event)
        home = await asyncio.to_thread(_replace_home, runner, event, context, expected)
        current = await asyncio.to_thread(_selection_stamp, runner, event)
        if current[:5] != expected[:5] or current[5] != home_identity(home) or current[6:] != expected[6:]:
            raise PermissionError('Home selection changed')
    except Exception:
        return text('group_home', 'home_failed', command_prefix=prefix)
    return text('group_home', 'home_saved', command_prefix=prefix)


def _replace_home(runner, event, context, expected):
    from gateway.run import _home_target_env_var, _home_thread_env_var
    from hermes_cli.config import _CONFIG_LOCK, _env_write_blocked, load_config, save_config

    source = logical_home_source(event)
    home = HomeChannel(platform=source.platform, chat_id=str(source.chat_id),
        name=source.chat_name or str(source.chat_id), thread_id=source.thread_id,
        user_id=str(source.user_id), scope_id=str(source.scope_id) if source.scope_id else None,
        selection_id=secrets.token_hex(16))
    with receiving_config_scope(context), _CONFIG_LOCK:
        if _selection_stamp(runner, event) != expected:
            raise PermissionError('Home selection changed')
        config = load_config()
        platform = config.setdefault('platforms', {}).setdefault(home.platform.value, {})
        previous = deepcopy(platform.get('home_channel'))
        previous_identity = home_identity(HomeChannel.from_dict(previous)) if isinstance(previous, dict) else None
        if previous_identity != expected[5]:
            raise PermissionError('Saved Home selection changed')
        require_saved_policy(platform, event)
        target_key = _home_target_env_var(source.platform.value)
        thread_key = _home_thread_env_var(source.platform.value)
        if _env_write_blocked(target_key, 'set') or _env_write_blocked(thread_key, 'set'):
            raise RuntimeError('Legacy Home delivery setting is managed')
        if _selection_stamp(runner, event) != expected:
            raise PermissionError('Home selection changed')
        platform['home_channel'] = home.to_dict()
        try:
            save_config(config)
            stored_platform = load_config().get('platforms', {}).get(home.platform.value, {})
            if stored_platform.get('home_channel') != home.to_dict():
                raise RuntimeError('Home persistence was not confirmed')
            require_saved_policy(stored_platform, event)
            if _selection_stamp(runner, event) != expected:
                raise PermissionError('Home selection changed')
            _save_legacy_home({target_key: home.chat_id, thread_key: home.thread_id or ''})
        except Exception:
            # Roll back only our own still-current selection, never a newer edit
            # or unrelated platform settings.
            restored = load_config()
            current = restored.get('platforms', {}).get(home.platform.value, {})
            if current.get('home_channel') == home.to_dict():
                if previous is None:
                    current.pop('home_channel', None)
                else:
                    current['home_channel'] = previous
                save_config(restored)
            raise
        context.config.home_channel = home
        return home
