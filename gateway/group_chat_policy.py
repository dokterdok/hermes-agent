"""Group reads use the live receiving adapter's authority, never its execution route."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from gateway.config import Platform
from gateway.session_authorities import authority_for_home, served_profile_name
from gateway.slash_access import SlashAccessPolicy, policy_from_extra


@dataclass(frozen=True)
class ReceivingGroupContext:
    adapter: object
    authority: object
    home: Path
    profile: str
    config: object


def receiving_group_context(runner, source):
    """Resolve only server-retained provenance against live adapter/home registries."""
    reference = getattr(source, '_transport_adapter_ref', None)
    if not callable(reference):
        return None
    adapter = reference()
    platform = Platform.RELAY if source.delivered_via_upstream_relay else source.platform
    mappings = [(None, getattr(runner, 'adapters', {})), *getattr(runner, '_profile_adapters', {}).items()]
    owners = [profile for profile, mapping in mappings if adapter is not None and mapping.get(platform) is adapter]
    if len(owners) != 1:
        return None
    home = getattr(runner, '_native_transport_homes', {}).get(owners[0])
    if home is None:
        return None
    home = Path(home).resolve()
    stamped = getattr(source, '_authorization_profile_home', None)
    if not home.is_dir() or (stamped is not None and Path(stamped).resolve() != home):
        return None
    authority = authority_for_home(runner, home)
    config = getattr(adapter, 'config', None)
    if authority is None or config is None:
        return None
    # The relay's own config is not the underlying platform's Home/admin policy.
    # Do not silently borrow an ambient or routed-profile policy for it.
    if platform == Platform.RELAY:
        return None
    return ReceivingGroupContext(adapter, authority, home, served_profile_name(home), config)


def receiving_group_transport(runner, source):
    context = receiving_group_context(runner, source)
    return (context.adapter, context.config) if context is not None else None


def group_policy_for_source(runner, source):
    context = receiving_group_context(runner, source)
    if context is None:
        return SlashAccessPolicy(True, frozenset(), frozenset())
    extra = getattr(context.config, 'extra', None) or {}
    scope = 'dm' if str(source.chat_type).casefold() in {'dm', 'direct', 'private'} else 'group'
    return policy_from_extra(extra, scope)


def home_config(context):
    """Source helpers expect get_home_channel; wrap the actual receiver, not runner.config."""
    return SimpleNamespace(get_home_channel=lambda platform: getattr(context.config, 'home_channel', None))


def group_command_prefix(runner, source):
    context = receiving_group_context(runner, source)
    return str(getattr(context.adapter, 'typed_command_prefix', '/') or '/') if context else '/'
