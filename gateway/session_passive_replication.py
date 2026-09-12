"""Canonical lifecycle for the existing passive history/work publisher.

The publisher from #104601/cfc9856 remains the copy engine. This adapter only
selects its owning authority/store and binds its workers to that profile.
"""
import asyncio
from pathlib import Path

from gateway.session_authorities import all_authorities, owner_scope, served_profile_name
from hermes_state_runtime import _epoch
from tui_gateway.hosted_room_replication import HostedRoomReplicationPublisher


def passive_source_supported(authority):
    home = Path(authority.profile_id)
    # Wire identity is installation/room, with no source-profile namespace.
    return (home.is_absolute() and home == home.resolve()
            and home.parent.name != 'profiles' and served_profile_name(home) == 'default')


class CanonicalReplicationPublisher(HostedRoomReplicationPublisher):
    def __init__(self, authority):
        from gateway.hosted_rooms import local_authority_gateway_id
        home = Path(authority.profile_id)
        if not passive_source_supported(authority) or home.resolve() != Path(authority.db.db_path).resolve().parent:
            raise ValueError('passive publisher requires the exact authority store')
        self.authority = authority
        with owner_scope(authority):
            super().__init__(authority.db.db_path, local_gateway_id=local_authority_gateway_id())

    def _worker(self):
        with owner_scope(self.authority):
            super()._worker()

    def _current(self, conn, route):
        from gateway.session_hosted_service import _OWNER
        runner = self.authority.runner
        if (getattr(runner, '_draining', False)
                or getattr(runner, 'session_runtime_descriptor', {}).get('state') != 'ready'):
            return False
        _epoch(conn, self.authority.epoch)
        owner = conn.execute('SELECT value FROM state_meta WHERE key=?', (_OWNER + route.key[0],)).fetchone()
        return bool(owner and owner[0]) and super()._current(conn, route)


async def prepare_passive_publishers(runner):
    """Prepare explicit per-authority stores without starting copy or enrolling peers."""
    for authority in all_authorities(runner):
        if passive_source_supported(authority) and getattr(authority, 'passive_publisher', None) is None:
            with owner_scope(authority):
                authority.passive_publisher = await asyncio.to_thread(CanonicalReplicationPublisher, authority)


def start_passive_publishers(runner):
    """Readiness releases copy workers; only explicit replicate grants are eligible."""
    if (getattr(runner, '_draining', False)
            or getattr(runner, 'session_runtime_descriptor', {}).get('state') != 'ready'):
        return
    for authority in all_authorities(runner):
        publisher = getattr(authority, 'passive_publisher', None)
        if publisher is not None:
            publisher.start()


async def stop_passive_publishers(runner, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    stopped = True
    for authority in all_authorities(runner):
        publisher = getattr(authority, 'passive_publisher', None)
        if publisher is not None:
            joined = await asyncio.to_thread(publisher.stop, timeout=max(0.0, deadline - loop.time()))
            stopped = joined and stopped
    return stopped
