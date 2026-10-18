"""A standalone gateway (``multiplex_profiles`` off) keeps resolving the launch profile's credentials
after a native hosted room activated the process-wide secret guard (#112878).

``tui_gateway.launch_profile_policy.activate_multi_profile_hosting`` runs inside the messaging
gateway process when a hosted room serves a second profile; ``get_secret`` then fails closed for
every unscoped read. The standalone gateway's turns and handler entry points must bind the launch
profile's OWN scope (``.env`` over the env frozen at activation) — not skip binding because the
config flag is off, and not rebuild from ``.env`` alone (systemd / ``op run`` injection has no file).

The same holds for the canonical owner-side RPC entry point (``owner_scope``): Desktop's
``session.create`` on a standalone named-profile gateway failed with ``UnscopedSecretError`` once
its embedded dashboard had served another profile (#134889).
"""
import asyncio
from contextlib import nullcontext
from unittest import mock

import pytest

from agent import secret_scope
from agent.secret_scope import UnscopedSecretError, current_secret_scope, get_secret
from gateway import run as gateway_run
from gateway.config import GatewayConfig
from gateway.run import GatewayRunner
from tui_gateway import launch_profile_policy

INJECTED = "HOSTEDROOM_TEST_INJECTED_KEY"


@pytest.fixture
def standalone(tmp_path, monkeypatch):
    launch = tmp_path / "launch"
    launch.mkdir()
    (launch / ".env").write_text("OPENAI_API_KEY=launch-dotenv-key\n", encoding="utf-8")
    secondary = tmp_path / "profiles" / "roomie"
    secondary.mkdir(parents=True)
    (secondary / ".env").write_text("OPENAI_API_KEY=secondary-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv(INJECTED, "launch-env-injected")  # systemd / `op run` style injection
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
    monkeypatch.setattr(launch_profile_policy, "_snapshot", None)
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=False)
    return runner, secondary


def _keys():
    return get_secret("OPENAI_API_KEY"), get_secret(INJECTED)


def test_standalone_turn_binds_launch_profile_scope_after_hosted_activation(standalone):
    runner, secondary = standalone
    from tui_gateway.server import _session_profile_runtime_scope

    # A native hosted room ran a second profile: real activation, real secondary scope entered and left.
    launch_profile_policy.activate_multi_profile_hosting()
    with _session_profile_runtime_scope({"profile_home": str(secondary)}):
        assert get_secret("OPENAI_API_KEY") == "secondary-key"
        assert get_secret(INJECTED) is None  # the launch env never leaks into a secondary
    assert secret_scope.is_multiplex_active()

    source = mock.MagicMock(profile=None)
    with runner._profile_scope_for_source(source):
        assert _keys() == ("launch-dotenv-key", "launch-env-injected")
    runner._handle_message = mock.AsyncMock(side_effect=lambda event: _keys())
    assert asyncio.run(runner._primary_message_handler()(mock.MagicMock(source=source))) == (
        "launch-dotenv-key", "launch-env-injected")
    with pytest.raises(UnscopedSecretError):  # the guard itself is not weakened
        get_secret("OPENAI_API_KEY")


def test_standalone_without_hosted_activation_stays_unscoped(standalone):
    runner, _secondary = standalone
    source = mock.MagicMock(profile=None)
    assert isinstance(runner._profile_scope_for_source(source), nullcontext)
    runner._handle_message = mock.AsyncMock(side_effect=lambda event: current_secret_scope())
    assert asyncio.run(runner._primary_message_handler()(mock.MagicMock(source=source))) is None


class _Transport:
    def write(self, frame):
        return True

    def close(self):
        pass


async def _standalone_connection(tmp_path, monkeypatch):
    """A named profile's standalone gateway (``multiplex_profiles`` off) on a host with other profile
    homes, and Desktop's ticketed connection to it — run_api.py's redeem → identity → connection
    binding, the whole socket under ``owner_scope`` as ``_NativeSessionWebSocket`` enters it."""
    from gateway.run_runtime import initialize_gateway_runtime
    from gateway.runtime_ownership import process_ownership
    from gateway.session_authorities import owner_scope
    from gateway.session_controls import AuthorityConnection
    from tests.gateway.test_session_authorities_multiplex import _reserve_homes
    from tui_gateway import server

    _root, homes = _reserve_homes(tmp_path, monkeypatch, names=("alpha", "beta"))
    launch = dict(homes)["alpha"]
    (launch / "config.yaml").write_text("model:\n  default: fixture\n", encoding="utf-8")
    (launch / ".env").write_text("XAI_API_KEY=launch-xai-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv(INJECTED, "launch-env-injected")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(server, "_hermes_home", launch)
    monkeypatch.setattr(gateway_run, "_hermes_home", launch)  # the import-time home of this process
    config = GatewayConfig()
    config.multiplex_profiles = False
    runner = GatewayRunner(config)
    runner._primary_profile_name = "alpha"
    process_ownership.reserve([launch])
    await initialize_gateway_runtime(runner)
    authority = runner.session_authority
    ticket = runner.session_ticket_store.mint(profile_id=str(launch.resolve()), subject="uid:desk",
                                              purpose="interactive")
    grant = runner.session_ticket_store.redeem(ticket, profile_id=None, purpose="interactive")
    identity = {"user_id": grant["subject"], "provider": "local", "profile_id": grant["profile_id"],
                "instance_id": grant["instance_id"], "capabilities": grant["capabilities"],
                "native_bootstrap": True, "profile_scope": grant.get("scope", "profile")}
    with owner_scope(authority):
        connection = AuthorityConnection(authority, _Transport(), identity, operator=True)
    return runner, launch, connection


async def _close(runner, launch, connection):
    from gateway.runtime_ownership import process_ownership
    from gateway.session_authorities import owner_scope
    with owner_scope(connection.authority):
        await connection.close()
    runner.session_authority.db.close()
    process_ownership.release(launch)


@pytest.mark.asyncio
async def test_standalone_session_create_after_the_dashboard_served_another_profile(tmp_path, monkeypatch):
    from gateway.session_authorities import owner_scope
    from hermes_cli.web_server_profiles import _config_profile_scope
    from tui_gateway.ws import _dispatch_request
    runner, launch, connection = await _standalone_connection(tmp_path, monkeypatch)
    try:
        assert not secret_scope.is_multiplex_active()
        # The gateway's embedded dashboard (gateway/run_api.py) entered another profile's scope (a
        # ``?profile=beta`` route, or POST /api/profiles writing a new profile's model): that arms the
        # process-wide guard inside this gateway, although the gateway itself serves only alpha.
        with _config_profile_scope("beta"):
            pass
        assert secret_scope.is_multiplex_active()

        create = {"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}}
        reply = await _dispatch_request(connection, create, create["method"], _Transport())
        assert "result" in reply, reply
        # Owner-side handlers read the launch profile's own scope: its .env over the frozen launch env.
        with owner_scope(connection.authority):
            assert (get_secret("XAI_API_KEY"), get_secret(INJECTED)) == ("launch-xai-key", "launch-env-injected")
        with pytest.raises(UnscopedSecretError):  # outside the scope the guard still fails closed
            get_secret("XAI_API_KEY")
    finally:
        await _close(runner, launch, connection)


@pytest.mark.asyncio
async def test_standalone_owner_scope_stays_a_nullcontext_until_the_guard_is_armed(tmp_path, monkeypatch):
    from gateway.session_authorities import owner_scope
    runner, launch, connection = await _standalone_connection(tmp_path, monkeypatch)
    try:
        assert type(owner_scope(connection.authority)) is nullcontext
        assert type(owner_scope(connection.authority, hydrate_secrets=True)) is nullcontext
        reply = await connection.dispatch({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
        assert "result" in reply, reply
        assert not secret_scope.is_multiplex_active() and current_secret_scope() is None
    finally:
        await _close(runner, launch, connection)
