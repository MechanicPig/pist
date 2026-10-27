"""Bounded startup and cleanup for loopback preview integration tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol
from unittest.mock import patch

from aiohttp import ClientResponse, ClientSession


class BrowserPreview(Protocol):
    async def preview(self) -> None: ...


@asynccontextmanager
async def preview_client(preview: BrowserPreview) -> AsyncIterator[tuple[ClientSession, str]]:
    """Run the public preview lifecycle without opening a real browser."""
    urls: list[str] = []
    with patch(
        'berries.map_preview.session.webbrowser.open',
        side_effect=lambda url: urls.append(url) or True,
    ):
        task = asyncio.create_task(preview.preview())
        async with preview_url(task, urls) as url, ClientSession() as client:
            yield client, url


async def preview_request(
    preview: BrowserPreview, method: str, path: str, payload: object = None
) -> ClientResponse:
    """Make one real protocol request; retain its buffered body after cleanup."""
    async with (
        preview_client(preview) as (client, url),
        client.request(method, f'{url}/{path}', json=payload) as response,
    ):
        await response.read()
        return response


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
