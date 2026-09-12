"""Let the existing API reservation follow work beyond its HTTP observer."""
import asyncio


async def run_owned_work(adapter, reservation, operation, *args, **kwargs):
    from gateway.platforms.api_server import _release_pending_api_work
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    reservation['detached'] = True
    def completed(done):
        _release_pending_api_work(adapter, reservation)
        if not done.cancelled():
            done.exception()
    worker.add_done_callback(completed)
    return await asyncio.shield(worker)
