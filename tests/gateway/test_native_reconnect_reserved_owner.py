"""One unavailable profile cannot block reconnect recovery of a later served owner."""

import logging

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


@pytest.mark.asyncio
async def test_connector_failure_is_visible_without_blocking_sibling_or_exposing_payload(state, monkeypatch, caplog):
    state.runner.allowed.update({"role-first", "role-second"})
    first, _ = await reconnect_fixture.queued(state, role=True, user="role-first")
    second, _ = await reconnect_fixture.queued(state, "beta", transport="default", chat="routed", role=True, user="role-second")
    runner = state.runner
    replacement = reconnect_fixture.Adapter("primary")
    runner.adapters[Platform.TELEGRAM] = replacement
    before = reconnect_fixture.ledger(state)

    class ConnectorFailure(Exception):
        pass

    async def role_check(adapter, source):
        if source.user_id == "role-first":
            raise ConnectorFailure("private-request-secret")
        return True

    monkeypatch.setattr(reconnect_fixture.Adapter, "reauthorize_native_roles", role_check)
    from gateway.session_native_reconnect import recover_adapter_native_inputs

    with caplog.at_level(logging.WARNING, logger="gateway.session_native_reconnect"):
        await recover_adapter_native_inputs(runner, Platform.TELEGRAM, replacement)

    assert state.scheduled == [(str(state.homes["beta"]), second.ref.session_id, str(state.homes["beta"]))]
    assert first.admission_id != second.admission_id
    assert reconnect_fixture.ledger(state) == before
    assert any("ConnectorFailure" in record.getMessage() for record in caplog.records)
    assert "private-request-secret" not in caplog.text
