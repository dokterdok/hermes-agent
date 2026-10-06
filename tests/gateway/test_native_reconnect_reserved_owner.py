"""A parked profile must not block reconnect recovery of a later served owner."""

import pytest

from gateway.config import Platform
from gateway.session_authorities import SessionAuthorities
from tests.gateway import test_native_reconnect_recovery as reconnect_fixture

state = reconnect_fixture.state


@pytest.mark.asyncio
async def test_parked_profile_does_not_block_later_owner_reconnect(state):
    runner = state.runner
    registry = SessionAuthorities(state.homes["default"])
    registry.add(state.homes["default"], state.authorities["default"])
    registry.add(state.homes["alpha"], None, name="alpha")
    registry.add(state.homes["beta"], state.authorities["beta"], name="beta")
    runner.session_authorities = registry
    receipt, _ = await reconnect_fixture.queued(state, "beta")
    before = reconnect_fixture.ledger(state)
    replacement = reconnect_fixture.Adapter("beta")
    runner._profile_adapters["beta"][Platform.TELEGRAM] = replacement
    from gateway.session_native_reconnect import recover_adapter_native_inputs

    await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement, profile="beta")

    assert state.scheduled == [(str(state.homes["beta"]), receipt.ref.session_id, str(state.homes["beta"]))]
    assert reconnect_fixture.ledger(state) == before
