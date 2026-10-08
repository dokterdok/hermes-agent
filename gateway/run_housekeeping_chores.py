"""Ordered gateway maintenance chores, retaining each chore's profile and cadence."""
from typing import Any


def housekeeping_chores(*, adapters=None, loop=None, cron_provider=None, runner=None, cron_thread=None):
    from gateway import run
    from gateway.run_delivery_queue_watch import DRAIN_LABEL
    from gateway.run_profile_reconcile import _mcp_config_reconciler, profile_scoped_chore
    chores: list[tuple[int, str, Any]] = [
        # First every tick: re-stamp ``updated_at`` in gateway_state.json so it is a real heartbeat.
        # ``hermes gateway status`` / ``/api/status`` warn when it ages past 2x ``interval`` with the
        # PID alive — the thread (or a chore blocked on the loop) wedged (#113372). Runs first so a
        # wedged chore stops the NEXT stamp instead of a slow one delaying this tick's.
        (1, "Runtime heartbeat", run._write_runtime_status_quiet)]
    if runner is not None:
        from gateway.run_input_reclamation import collect_gateway_input_copies
        # Collector enumerates owned authorities itself; do not run it once per profile.
        chores.append((5, "Working-copy collection", lambda: collect_gateway_input_copies(runner)))
    if adapters is not None or runner is not None:
        # Restart-safe cron workers run outside the gateway cgroup and queue their final send for
        # whichever gateway is live; drained here (not the scheduler tick) so external providers get it too.
        chores.append((1, DRAIN_LABEL, lambda: run._drain_restart_safe_cron_deliveries(adapters, loop, runner)))
    chores += [
        (5, "Channel directory refresh", lambda: adapters and run._housekeeping_channel_directory(adapters, loop)),
        (60, "Media cache cleanup", run._housekeeping_media_caches),
        (60, "Paste sweep", run._housekeeping_paste_sweep)]
    if cron_provider is not None:
        chores.append((5, "Misfire catch-up sweep", lambda: run._housekeeping_misfire_catch_up(cron_provider, adapters, loop)))
    if cron_thread is not None:
        # The ticker's own guards keep its loop alive; this is the outer layer for a thread that has
        # already ended (#111010). Runs every tick so the outage is bounded by one housekeeping interval.
        chores.append((1, "Cron ticker supervisor", cron_thread.restart_if_dead))
    chores += [
        # Per served profile: each profile has its own skills tree, curator state, Nous login
        # and state.db.
        (60, "Curator tick", profile_scoped_chore(runner, run._housekeeping_curator)),
        (60, "state.db maintenance tick", profile_scoped_chore(
            runner,
            # Default-bound now, i.e. OUTSIDE any profile scope: this is the launch home's override.
            lambda _launch=run._launch_sessions_dir(getattr(runner, "config", None)):
                run._housekeeping_state_db_maintenance(_launch))),
        # Due-gated inside: the first tick after startup runs an overdue check, not tick 60.
        # Per served profile: plugins dir, last-run marker and plugins.auto_apply are all the
        # profile's own (get_hermes_home()/load_config_readonly() bind to the scope).
        (1, "Plugin update check", profile_scoped_chore(runner, run._housekeeping_plugin_update_check)),
        (1, "Deferred FTS retry tick", run._housekeeping_deferred_fts_retry),
        (1, "gateway housekeeping memory trim", run._housekeeping_memory_trim),
        (1, "MCP config reconcile", _mcp_config_reconciler(runner)),
        # Last: a real prune can hold this thread for a while; every other chore of the tick runs first.
        (1, "Checkpoint prune tick", run._housekeeping_checkpoint_prune)]

    return chores
