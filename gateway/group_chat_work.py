"""Bounded read work preserving the caller's receiving-profile context."""
import asyncio
from contextvars import ContextVar
from contextvars import copy_context
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


def require_command_open(runner):
    require_read_active()
    if getattr(runner, '_external_drain_active', False) is True or getattr(runner, '_draining', False) is True:
        raise GroupChatMaintenanceError('New Group Chat messages are paused for maintenance. Try again shortly.')


async def run_group_command_work(runner, action, operation):
    """Keep #98073 workers visible to drain, including a timed-out caller's work."""
    if action not in {'send', 'approve', 'deny'}:
        raise ValueError('This Group Chat action is not enabled')
    if action == 'send':
        require_command_open(runner)
    track = getattr(runner, '_track_deferred_agent_worker', None)
    if not callable(track):
        raise GroupChatMaintenanceError('Group Chat messaging is unavailable. Try again shortly.')
    loop = asyncio.get_running_loop()
    completion = loop.create_future()
    try:
        track(completion, None)
    except BaseException:
        completion.set_result(None)
        raise
    cancelled = threading.Event()
    token = _cancelled.set(cancelled)
    context = copy_context()
    _cancelled.reset(token)

    def invoke():
        if action == 'send':
            require_command_open(runner)
        else:
            require_read_active()
        return operation()

    def completed(done):
        if not completion.done():
            completion.set_result(None)
        if not done.cancelled():
            done.exception()

    try:
        worker = loop.run_in_executor(None, context.run, invoke)
    except BaseException:
        completion.set_result(None)
        raise
    worker.add_done_callback(completed)
    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout=20)
    finally:
        cancelled.set()
