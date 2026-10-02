"""Bounded startup and cleanup for loopback preview integration tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def preview_url[T](
    task: asyncio.Task[T], urls: list[str], *, timeout: float = 5
) -> AsyncIterator[str]:
    """Yield the opened URL, propagating startup errors and always stopping the task."""
    try:
        async with asyncio.timeout(timeout):
            while not urls:
                if task.done():
                    await task
                    raise AssertionError('Preview stopped before opening a browser URL')
                await asyncio.sleep(0)
            # A browser-open callback can publish a URL before startup fails.
            if task.done():
                await task
                raise AssertionError('Preview stopped before the HTTP checks could run')
            yield urls[0]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
