"""Read-only Home/audience disclosure fences; never enroll a room or persist consent."""
from functools import wraps

from gateway.group_chat_messages import text
from gateway.group_chat_policy import group_command_prefix, group_policy_for_source, home_config, receiving_group_context
from gateway.group_home_identity import acknowledgement, home_identity, is_home_control_source, private_event, trusted_person
from gateway.session_authorities import owner_scope


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


def disclosure_stamp(runner, event):
    if not trusted_person(event):
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
            if not private and getattr(home, 'group_audience_ack', None) != acknowledgement(home):
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
