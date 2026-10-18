"""Probe (claude, r2 lane1): uvicorn.Config's dictConfig closes every live handler
(logging.shutdown). The gateway enables the profile log router (run_bootstrap.py:798) and then
builds the API listener's uvicorn.Config (run_bootstrap.py:850 -> start_gateway_api ->
build_listener_server). After 82f19f928c6 a closed router drops every later record for good."""
import logging
from logging.handlers import RotatingFileHandler

import hermes_logging


def _log(router, home, text):
    record = logging.LogRecord("probe", logging.ERROR, __file__, 1, text, None, None)
    record.hermes_home = str(home)
    router.handle(record)


def test_router_keeps_writing_after_uvicorn_config(tmp_path):
    default = tmp_path / "home"
    (default / "logs").mkdir(parents=True)
    profile = default / "profiles" / "beta"
    (profile / "logs").mkdir(parents=True)
    existing = RotatingFileHandler(str(default / "logs" / "agent.log"), encoding="utf-8")
    router = hermes_logging._ProfileRoutingFileHandler(existing, [profile])
    existing.close()
    try:
        _log(router, default, "before-default")
        _log(router, profile, "before-beta")
        import uvicorn

        async def app(scope, receive, send):  # pragma: no cover - never served
            pass

        # Exactly what build_listener_server does (no log_config -> uvicorn LOGGING_CONFIG).
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        _log(router, default, "after-default")
        _log(router, profile, "after-beta")
        default_text = (default / "logs" / "agent.log").read_text(encoding="utf-8")
        beta_text = (profile / "logs" / "agent.log").read_text(encoding="utf-8")
        assert "before-default" in default_text and "before-beta" in beta_text
        assert "after-default" in default_text, "default-home record dropped after uvicorn.Config"
        assert "after-beta" in beta_text, "profile record dropped after uvicorn.Config"
    finally:
        router.close()
