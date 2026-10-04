"""Short-lived loopback hosting and assets shared by browser previews."""

import asyncio
import secrets
import webbrowser
from collections.abc import Iterable
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import cast

from aiohttp import web

from berries.paths import BERRIES_DIR

_SHARED_ASSETS = frozenset(
    {'canvas.js', 'viewport.js', 'geometry.js', 'client.js', 'object_info.js', 'object_info.css'}
)


class MapPreviewError(RuntimeError):
    """The local browser map-preview session could not be started."""


class PreviewAssets:
    """Serve a preview's page with shared rendering modules and sprite fallbacks."""

    def __init__(
        self,
        package: str,
        *,
        sprite_dir: Path = BERRIES_DIR / 'sprites',
        scripts: Iterable[str] = (),
    ) -> None:
        self.package = package
        self.sprite_dir = sprite_dir
        self.asset_names = _SHARED_ASSETS | {'map_preview.css', 'map_preview.js'} | set(scripts)

    def text(self, name: str) -> str:
        package = 'berries.map_preview' if name in _SHARED_ASSETS else self.package
        return files(package).joinpath('static', name).read_text(encoding='utf-8')

    def sprite(self, name: str) -> bytes | None:
        # This HTTP identifier is POSIX-relative, including on Windows hosts.
        if '\\' in name or ':' in name:
            return None
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            return None
        local = self.sprite_dir.joinpath(*path.parts)
        if local.is_file():
            return local.read_bytes()
        # Application-specific sprites take precedence over shared Berries sprites.
        for package in dict.fromkeys((self.package.split('.')[0], 'berries')):
            shared = files(package).joinpath('data', 'sprites', *path.parts)
            if shared.is_file():
                return shared.read_bytes()
        builtin = files('berries.map_preview').joinpath('game_assets', *path.parts)
        return builtin.read_bytes() if builtin.is_file() else None

    async def page(self, req: web.Request) -> web.Response:
        html = self.text('index.html').replace('assets/', f'{req.path}/assets/')
        return web.Response(text=html, content_type='text/html')

    async def asset(self, req: web.Request) -> web.Response:
        name = req.match_info['name']
        if name not in self.asset_names:
            raise web.HTTPNotFound()
        content_type = 'text/css' if name.endswith('.css') else 'text/javascript'
        return web.Response(text=self.text(name), content_type=content_type)

    async def game_asset(self, req: web.Request) -> web.Response:
        data = self.sprite(req.match_info['name'])
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type='image/png')


class PreviewSession:
    """Own the HTTP/browser lifetime, independently of preview or editing state."""

    def __init__(self, assets: PreviewAssets) -> None:
        self.assets = assets
        self.base_path = f'/preview/{secrets.token_urlsafe(24)}'
        self._result: asyncio.Future[None] | None = None

    def close(self) -> None:
        if self._result is not None and not self._result.done():
            self._result.set_result(None)

    async def run(self, routes: Iterable[web.RouteDef]) -> None:
        self._result = asyncio.get_running_loop().create_future()
        app = web.Application()
        app.add_routes(
            (
                web.get(self.base_path, self.assets.page),
                web.get(f'{self.base_path}/assets/{{name}}', self.assets.asset),
                web.get(f'{self.base_path}/game-assets/{{name}}', self.assets.game_asset),
                *routes,
            )
        )
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            sockets = cast(asyncio.Server, site._server).sockets if site._server else ()
            if not sockets:
                raise MapPreviewError('无法启动本地地图预览服务。')
            url = f'http://127.0.0.1:{sockets[0].getsockname()[1]}{self.base_path}'
            if not await asyncio.to_thread(webbrowser.open, url):
                raise MapPreviewError(f'无法打开浏览器：{url}')
            await self._result
        except OSError as error:
            raise MapPreviewError(f'无法启动本地地图预览：{error}') from error
        finally:
            await runner.cleanup()
