"""Plan bounded caller-side retry waits independently of the delivery itself."""


def retry_delay(adapter, server_retry_after, spent, attempt, base_delay, error_str):
    """Return delay and updated server-wait usage, or refuse another inline retry."""
    from gateway.platforms import base as facade

    backoff = server_retry_after
    if backoff is None:
        backoff = base_delay * (2 ** (attempt - 1))
    elif backoff > facade._SEND_RETRY_INLINE_WAIT_CAP_SECS:
        # Never hold this coroutine open for a long server penalty: a 97-minute
        # FloodWait slept verbatim once froze inbound on every platform (#91969).
        # Return the typed failure; the delivery ledger redelivers after the cooldown.
        facade.logger.error(
            "[%s] Server asked to retry after %.0fs (> %.0fs inline cap); returning "
            "typed failure for redelivery instead of sleeping: %s",
            adapter.name, backoff, facade._SEND_RETRY_INLINE_WAIT_CAP_SECS, error_str,
        )
        return None
    if server_retry_after is not None:
        budget = adapter.retry_after_sleep_budget_secs
        if budget is not None:
            remaining = max(0.0, budget - spent)
            if backoff > remaining:
                facade.logger.warning(
                    "[%s] Server retry after %.1fs exceeds remaining %.1fs of %.1fs "
                    "delivery sleep budget; returning failure for redelivery: %s",
                    adapter.name, backoff, remaining, budget, error_str,
                )
                return None
    delay = backoff + facade.random.uniform(0, 1)
    if server_retry_after is not None and adapter.retry_after_sleep_budget_secs is not None:
        delay = min(delay, max(0.0, adapter.retry_after_sleep_budget_secs - spent))
        spent += delay
    return delay, spent
