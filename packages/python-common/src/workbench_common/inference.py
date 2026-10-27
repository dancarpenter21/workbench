"""Transport-independent failures and serialized local model execution."""

import asyncio


class EngineBusy(RuntimeError):
    pass


class EngineUnavailable(RuntimeError):
    pass


async def run_serialized(lock, function):
    """Cancellation must not release a model while its native worker is still running."""
    if lock.locked():
        raise EngineBusy("Inference engine is busy")
    async with lock:
        task = asyncio.create_task(asyncio.to_thread(function))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # Retrieve a worker failure even when the caller cancelled.
            raise cancelled
