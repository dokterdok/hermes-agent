"""Bounded read work preserving the caller's receiving-profile context."""
import asyncio
from contextvars import ContextVar
import threading

_cancelled = ContextVar('group_read_cancelled', default=None)


def require_read_active():
    cancelled = _cancelled.get()
    if cancelled is not None and cancelled.is_set():
        raise PermissionError('Group Chat read was cancelled')


async def run_group_read(operation):
    # Timeout/cancel retires disclosure, not the underlying read's storage ownership.
    cancelled = threading.Event()
    token = _cancelled.set(cancelled)
    try:
        return await asyncio.wait_for(asyncio.to_thread(operation), timeout=20)
    finally:
        cancelled.set()
        _cancelled.reset(token)


class GroupChatMaintenanceError(RuntimeError):
    pass


async def run_group_command_work(runner, action, operation):
    """No mutation callable is invoked before the parent's final-write contract lands."""
    raise GroupChatMaintenanceError('Group Chat changes from messaging are not available yet. Use Hermes Desktop.')
