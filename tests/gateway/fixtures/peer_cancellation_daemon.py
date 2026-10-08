"""Ordinary canonical daemon with short room recovery clocks for cancellation UAT."""
import asyncio
import runpy
from pathlib import Path

from gateway.session_hosted_service import CanonicalHostedRoomService
from gateway.platforms.api_server import APIServerAdapter
from gateway.session_authority import SessionAuthority
from gateway import session_api_turn
from hermes_state_runtime import list_session_admissions
from hermes_constants import get_hermes_home

initialize = CanonicalHostedRoomService.__init__


def short_recovery(self, *args, **kwargs):
    initialize(self, *args, **kwargs)
    self.runtime.lease_ttl_seconds = 1.0
    self.runtime.indeterminate_defer_seconds = 2.0
    self.runtime.poll_interval_seconds = 0.05
    self.runtime.active_poll_interval_seconds = 0.05


CanonicalHostedRoomService.__init__ = short_recovery
admit_live_chat = APIServerAdapter._admit_to_live_bot_chat


async def before_canonical_write(self, session_id, user_message, *args, **kwargs):
    if 'CANCEL_BEFORE_CANONICAL_WRITE' in user_message:
        home = Path(get_hermes_home())
        (home / 'pre-admission-entered').touch()
        while not (home / 'pre-admission-release').exists():
            await asyncio.sleep(.02)
    return await admit_live_chat(self, session_id, user_message, *args, **kwargs)


APIServerAdapter._admit_to_live_bot_chat = before_canonical_write
schedule = SessionAuthority._schedule
observe = session_api_turn.observe_api_turn


def hold_queued_observer(self, ref):
    rows = list_session_admissions(self.db, session_id=ref.session_id)
    if rows and 'CANCEL_QUEUED_OBSERVER' in rows[-1]['payload'].get('text', ''):
        return
    return schedule(self, ref)


async def before_observation(admitted, **kwargs):
    held = 'CANCEL_QUEUED_OBSERVER' in admitted[2]['payload'].get('text', '')
    home = Path(get_hermes_home())
    if held:
        (home / 'observer-entered').touch()
        while not (home / 'observer-release').exists():
            await asyncio.sleep(.02)
    try:
        return await observe(admitted, **kwargs)
    finally:
        if held:
            (home / 'observer-finished').touch()


SessionAuthority._schedule = hold_queued_observer
session_api_turn.observe_api_turn = before_observation
runpy.run_module('gateway.run', run_name='__main__')
