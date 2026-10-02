"""Contracts for the preview capabilities composed by Berries and Pist."""

import asyncio
from pathlib import Path

import pytest
from aiohttp import ClientConnectorError, ClientSession

from berries.map_preview.navigation import PreviewNavigation
from berries.map_preview.session import MapPreviewError, PreviewAssets, PreviewSession
from test_support.preview import preview_url


def test_navigation_branches_discard_forward_pages_only_after_successful_append() -> None:
    navigation = PreviewNavigation('source', dialogs={})
    navigation.append('first')
    navigation.append('second')
    assert navigation.back()
    assert navigation.current == 'first'
    assert navigation.can_forward

    navigation.append('branch')
    assert navigation.current == 'branch'
    assert not navigation.forward()
    navigation.home()
    assert navigation.current == 'source'
    assert not navigation.back()
    assert navigation.forward()
    assert navigation.current == 'first'
    assert navigation.forward()
    assert navigation.current == 'branch'


@pytest.mark.parametrize(
    'name', ['../outside.png', '/outside.png', r'..\outside.png', 'C:/outside.png']
)
def test_sprite_identifiers_cannot_escape_the_local_directory(tmp_path: Path, name: str) -> None:
    sprite_dir = tmp_path / 'sprites'
    sprite_dir.mkdir()
    (tmp_path / 'outside.png').write_bytes(b'outside')
    assets = PreviewAssets('berries.map_preview', sprite_dir=sprite_dir)

    assert assets.sprite(name) is None


def test_nested_local_sprite_overrides_builtins(tmp_path: Path) -> None:
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'seed.png').write_bytes(b'seed')
    (tmp_path / 'strawberry.png').write_bytes(b'override')
    assets = PreviewAssets('berries.map_preview', sprite_dir=tmp_path)

    assert assets.sprite('nested/seed.png') == b'seed'
    assert assets.sprite('strawberry.png') == b'override'
    assert assets.sprite('silverberry.png') is not None


def test_session_releases_its_listener_when_the_browser_cannot_open(monkeypatch) -> None:
    urls: list[str] = []
    monkeypatch.setattr(
        'berries.map_preview.session.webbrowser.open', lambda url: urls.append(url) and False
    )
    preview = PreviewSession(PreviewAssets('berries.map_preview'))

    async def check() -> None:
        with pytest.raises(MapPreviewError, match='无法打开浏览器'):
            await preview.run(())
        assert len(urls) == 1
        async with ClientSession() as client:
            with pytest.raises(ClientConnectorError):
                await client.get(urls[0])

    asyncio.run(check())


def test_session_releases_its_listener_on_cancellation(monkeypatch) -> None:
    urls: list[str] = []
    monkeypatch.setattr(
        'berries.map_preview.session.webbrowser.open', lambda url: urls.append(url) or True
    )
    preview = PreviewSession(PreviewAssets('berries.map_preview'))

    async def check() -> None:
        task = asyncio.create_task(preview.run(()))
        try:
            async with asyncio.timeout(5):
                while not urls:
                    await asyncio.sleep(0)
                async with ClientSession() as client:
                    async with client.get(urls[0]) as response:
                        assert response.status == 200
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    with pytest.raises(ClientConnectorError):
                        await client.get(urls[0])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


def test_preview_wait_propagates_early_startup_failure() -> None:
    async def fail() -> None:
        raise ValueError('startup failure')

    async def check() -> None:
        task = asyncio.create_task(fail())
        with pytest.raises(ValueError, match='startup failure'):
            async with preview_url(task, []):
                pytest.fail('Failed startup must not yield a URL')
        assert task.done()

    asyncio.run(check())


def test_preview_wait_times_out_and_stops_pending_startup() -> None:
    async def check() -> None:
        task = asyncio.create_task(asyncio.Event().wait())
        with pytest.raises(TimeoutError):
            async with preview_url(task, [], timeout=0.01):
                pytest.fail('Pending startup must not yield a URL')
        assert task.cancelled()

    asyncio.run(check())


def test_preview_wait_rejects_normal_exit_before_opening_a_url() -> None:
    async def stop() -> None:
        pass

    async def check() -> None:
        task = asyncio.create_task(stop())
        with pytest.raises(AssertionError, match='before opening a browser URL'):
            async with preview_url(task, []):
                pytest.fail('Exited preview must not yield a URL')
        assert task.done()

    asyncio.run(check())


def test_preview_wait_stops_server_after_a_test_assertion_fails() -> None:
    async def check() -> None:
        task = asyncio.create_task(asyncio.Event().wait())
        with pytest.raises(AssertionError, match='HTTP assertion failed'):
            async with preview_url(task, ['http://127.0.0.1/']):
                raise AssertionError('HTTP assertion failed')
        assert task.cancelled()

    asyncio.run(check())
