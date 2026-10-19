"""A multi-profile gateway keeps writing its log files after it builds its API listener.

``start_gateway`` swaps the file handlers for per-profile routers (``_enable_multiplex_log_routing``)
and then builds the runtime API listener (``gateway/run_api.py::start_gateway_api`` ->
``build_listener_server``). Its ``uvicorn.Config`` runs ``logging.config.dictConfig``, which calls
``logging.shutdown`` on every handler. A closed ``_ProfileRoutingFileHandler`` drops every later
record, so on a live multi-profile gateway gateway.log, agent.log and errors.log stopped at the
control-socket line: the Bot Chat refusal and its "settled as not sent" line (#140297 MSG-19) never
reached gateway.log. A single-profile gateway is unaffected (a plain file handler reopens on emit).
"""
import logging
from types import SimpleNamespace

import pytest

import hermes_logging


@pytest.fixture
def isolated_logging():
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    hermes_logging._reset_queued_handlers()
    hermes_logging._logging_initialized = False
    yield
    hermes_logging._reset_queued_handlers()
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    hermes_logging._logging_initialized = False


def test_profile_logs_survive_the_api_listener_build(tmp_path, monkeypatch, isolated_logging):
    from gateway.run import _enable_multiplex_log_routing
    from hermes_cli.web_server_lifecycle import build_listener_server
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home = (tmp_path / "home").resolve()
    beta = home / "profiles" / "beta"
    (beta / "logs").mkdir(parents=True)
    (beta / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    hermes_logging.setup_logging(hermes_home=home, mode="gateway")
    config = SimpleNamespace(multiplex_profiles=True, _runtime_profile_homes=[("default", home), ("beta", beta)])
    assert _enable_multiplex_log_routing(config)
    log = logging.getLogger("gateway.session_bot")
    log.info("before the listener")

    async def app(scope, receive, send):  # never served: only the listener's Config is built
        raise AssertionError("not served")

    build_listener_server(app, "127.0.0.1", 0, auth_required=False, timeout_graceful_shutdown=5, lifespan="off")
    log.info("default profile, after the listener")
    token = set_hermes_home_override(str(beta))
    try:
        log.info("beta profile, after the listener")
        log.warning("beta warning, after the listener")
    finally:
        reset_hermes_home_override(token)
    hermes_logging.drain_log_queue(timeout=5)

    default_log = (home / "logs" / "gateway.log").read_text(encoding="utf-8")
    assert "before the listener" in default_log
    assert "default profile, after the listener" in default_log, default_log
    beta_log = beta / "logs" / "gateway.log"
    assert beta_log.is_file() and "beta profile, after the listener" in beta_log.read_text(encoding="utf-8")
    assert "beta warning, after the listener" in (beta / "logs" / "errors.log").read_text(encoding="utf-8")
