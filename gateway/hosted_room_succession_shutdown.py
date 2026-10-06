"""Best-effort Group Chat handover and restart announcements during gateway shutdown.

The shutdown facade retains its constants and patch seams. These helpers keep the
existing per-authority budget, operator identity and nonblocking executor cleanup.
"""
import sqlite3

# How long a planned restart may keep the group's backups waiting before they treat the host as lost.
GROUP_RESTART_WINDOW_SECONDS = 180.0


# How long a stop or quit waits for this gateway's Group Chats to move to their standbys.
GROUP_HANDOVER_BUDGET_SECONDS = 20.0


def _hand_over_groups(runner) -> None:
    """Stopping without a restart: hand each Group Chat this gateway hosts to its best reachable
    standby first, so the group keeps going. Best effort; a handover that fails leaves the normal
    host-loss paths in place."""
    import concurrent.futures
    from gateway import run_shutdown as facade
    from gateway.session_authorities import all_authorities
    for authority in all_authorities(runner):
        service = getattr(authority, "hosted_room_service", None)
        factory = getattr(service, "succession_context", None)
        context = factory() if callable(factory) else None
        if context is None:
            continue
        import dataclasses
        from gateway.hosted_room_succession_handover import handover_all
        context = dataclasses.replace(context, operator=True)  # the gateway's own stop speaks for its operator
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="group-handover")
        try:
            moved = pool.submit(handover_all, context, reason="stop").result(timeout=facade.GROUP_HANDOVER_BUDGET_SECONDS)
            if moved.get("moved"):
                facade.logger.info("Moved %d group(s) to their standbys before stopping", len(moved["moved"]))
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
            facade.logger.debug("Group handover before stop skipped (%s)", type(exc).__name__)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


def _announce_group_restarts(runner) -> None:
    """A planned restart is not loss: each Group Chat this gateway hosts tells its backups when it
    will be back (``succession.state host_restarting``). Best effort; it never delays the restart."""
    from gateway import run_shutdown as facade
    from gateway.session_authorities import all_authorities
    until = facade.time.time() + facade.GROUP_RESTART_WINDOW_SECONDS
    for authority in all_authorities(runner):
        service = getattr(authority, "hosted_room_service", None)
        announce = getattr(service, "announce_restart", None)
        if announce is None:
            continue
        try:
            announce(until)
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
            facade.logger.debug("Group restart announcement skipped (%s)", type(exc).__name__)
