# A gateway on a multi-profile install writes no log files after boot (regression from 82f19f928c, #140967)

## Symptom

On any install with two or more servable profile homes (default plus one named profile), a gateway on
main writes `logs/gateway.log`, `agent.log` and `errors.log` only until it builds its runtime API
listener. After that, every record from that gateway process is dropped, for the root profile and
for every secondary profile alike. In the MSG-19 runs, `home/logs/gateway.log` holds one line
(`Gateway control socket listening …`) and `profiles/beta/logs/gateway.log` holds none. WARNINGs still
reach the process's stderr; INFO lines such as `Bot Chat delivery … settled as not sent` reach no
file at all. This happens with `gateway.multiplex_profiles: true`
(`gateway/run.py::_enable_multiplex_log_routing`) and also with it false, because
`tui_gateway/launch_profile_policy.py` turns the same routing on for any host with ≥2 servable homes.
A single-profile home is unaffected (probe `runs/mux-log-probe-ddabd38378-noprof`: 29 lines).

## Cause

1. `start_gateway` (`gateway/run_bootstrap.py:798`) swaps the queued file handlers for
   `_ProfileRoutingFileHandler` routers (`hermes_logging.enable_profile_log_routing`).
2. Then `start_gateway_runtime_api` (`:850`) → `gateway/run_api.py:156` →
   `hermes_cli/web_server_lifecycle.py:554 build_listener_server` constructs `uvicorn.Config(...)`.
   With the default `log_config`, uvicorn's `configure_logging` runs `logging.config.dictConfig`,
   and `_clearExistingHandlers` calls `logging.shutdown(logging._handlerList[:])`, which closes every
   handler ever created, the routers included. Live stack captured in
   `runs/router-close-probe-ddabd38378/` (the probe's stdout is `logs/probe-router-close.log`).
3. Since 82f19f928c ("fix(logging): a closed profile router never reopens a per-home log", merged
   in #140967 on 2026-10-18), `close()` is final: `_router_closed = True`, `_handler_for_home`
   returns None and `emit` returns without writing (`hermes_logging.py` "the router is closed"),
   and per-home handlers get `_never_reopen_after_close`. Before that commit, a closed router's
   per-home `RotatingFileHandler` reopened on the next emit, so the boot-time `dictConfig` was harmless.
   The commit message names "logging.shutdown (uvicorn's dictConfig)" as the close it guards against,
   but in the gateway that close happens at boot, not at exit.

## Proof

- New test `tests/gateway/test_multiplex_log_routing_survives_api_listener.py` (`scripts/` here;
  copied here as `msg19/test_multiplex_log_routing_survives_api_listener.py`; written on
  `test/multiprofile-gateway-log-router` at upstream main `77b446b6e1`). It runs `setup_logging(mode="gateway")`,
  then the real `_enable_multiplex_log_routing`, then the real `build_listener_server` exactly as
  `start_gateway_api` calls it, then logs for the default and the beta home.
  RED on `77b446b6e1` (Windows) and on `ddabd38378` (Mac): only "before the listener" reaches gateway.log.
  GREEN with 82f19f928c's `hermes_logging.py` hunk reverted. Also GREEN with `log_config=None` on the
  `uvicorn.Config` (an experiment to check the cause, not the proposed fix). Both experiments were reverted.
- Live: the pre-fix MSG-19 tree `f17021fbec` (before 82f19f928c) writes 37 + 6 gateway.log lines in the
  same scenario; main writes 1 + 0.

## Fix options (none pushed: each one is a design choice)

- Keep third-party `dictConfig` from closing Hermes's queued file handlers: they are owned by the
  QueueListener, never attached to a logger. For example, take them out of `logging._handlerList`,
  or have a close that comes from `logging.shutdown` while the process is still serving leave the
  router open. Real exit and profile release keep the final close.
- Or have `build_listener_server` pass `log_config=None` (and set the uvicorn logger levels itself).
  This also changes uvicorn's own stderr output for `hermes serve` / the dashboard, which share this helper.
- Reordering (routing after the listener) is fragile: any later `uvicorn.Config` (hot serve, dashboard
  warm) would close the routers again.

Severity: operator visibility. Nothing is lost from state or delivery, but on a multi-profile install
every gateway log file goes silent from boot onward, including the lines #140297's live checks ask for.
