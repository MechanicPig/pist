"""Read-only loopback browser preview for Celeste maps."""

import asyncio
import secrets
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import PurePosixPath
from typing import cast

from aiohttp import web
from pydantic import Field

from berries import map_layout
from berries.game import dialog
from berries.game.binmap import AttrValue
from berries.game.levels import Level, LevelSide, Map
from berries.game.maps import MapInfo
from berries.models import FrozenModel, StrictModel
from berries.paths import BERRIES_DIR

STATIC_DIR = 'static'
GAME_ASSETS_DIR = 'game_assets'
MAP_PREVIEW_HTML_FILE = 'index.html'
MAP_PREVIEW_ASSETS = frozenset({'map_preview.css', 'map_preview.js'})
LOCAL_SPRITES_DIR = BERRIES_DIR / 'sprites'


def _map_title(map_info: MapInfo, dialogs: Mapping[str, Mapping[str, str]]) -> str:
    base_file, side = dialog.split_map_side_suffix(map_info.file_path)
    key = dialog.dialog_key_for_map_file(base_file)
    names = {
        lang: f'{name} {side}' if side is not None else name
        for lang, entries in dialogs.items()
        if (name := entries.get(key)) is not None
    }
    fallback = dialog.default_map_name(base_file)
    return dialog.localized_name(names, ('zh-cn', 'en')) or (
        f'{fallback} {side}' if side is not None else fallback
    )


class MapPreviewError(RuntimeError):
    """The read-only browser preview could not be started."""


class _OpenMapReq(StrictModel):
    target_sid: str = Field(alias='targetSid')


class _EntityResp(FrozenModel):
    x: int | float
    y: int | float
    kind: str
    key: str | None = None
    sprite: str | None = None
    entity_id: str | None = Field(default=None, serialization_alias='entityId')
    attrs: dict[str, AttrValue] | None = None


class _RespawnResp(FrozenModel):
    x: int | float
    y: int | float


class _RoomResp(FrozenModel):
    name: str
    x: int | None
    y: int | None
    width: int | None
    height: int | None
    background: tuple[str, ...]
    solids: tuple[str, ...]
    entities: tuple[_EntityResp, ...]
    respawns: tuple[_RespawnResp, ...]


class _EntranceResp(FrozenModel):
    room: str
    target_sid: str = Field(serialization_alias='targetSid')
    x: int | float | None
    y: int | float | None
    width: int | float | None
    height: int | float | None
    target_title: str = Field(serialization_alias='targetTitle')
    available: bool
    source: str | None = None
    entity_id: str | None = Field(default=None, serialization_alias='entityId')
    attrs: dict[str, AttrValue] | None = None


class _StateResp(FrozenModel):
    title: str
    rooms: tuple[_RoomResp, ...]
    entrances: tuple[_EntranceResp, ...]
    can_back: bool = Field(serialization_alias='canBack')
    can_forward: bool = Field(serialization_alias='canForward')
    can_home: bool = Field(serialization_alias='canHome')


@dataclass(frozen=True, slots=True)
class _PreviewPage:
    map_info: MapInfo
    title: str
    layout: map_layout.MapLayout


class MapPreview:
    """Serve one short-lived, read-only map preview on loopback."""

    def __init__(
        self,
        map_source: MapInfo | Map,
        layout: map_layout.MapLayout,
        *,
        title: str | None = None,
        dialogs: Mapping[str, Mapping[str, str]] | None = None,
        routable_maps: Mapping[str, tuple[Level, LevelSide]] | None = None,
        extra_entities: Mapping[str, tuple[map_layout.MapPreviewEntity, ...]] | None = None,
    ) -> None:
        map_info = map_source.map_info if isinstance(map_source, Map) else map_source
        self._dialogs = dialogs or {}
        self._pages = [_PreviewPage(map_info, title or _map_title(map_info, self._dialogs), layout)]
        self._page_index = 0
        self._maps_by_sid = {} if routable_maps is None else dict(routable_maps)
        self._extra_entities = {} if extra_entities is None else dict(extra_entities)
        self._token = secrets.token_urlsafe(24)
        self._result: asyncio.Future[None] | None = None

    async def preview(self) -> None:
        """Open the browser preview and wait until its page is closed."""
        self._result = asyncio.get_running_loop().create_future()
        app = web.Application()
        app.add_routes(
            (
                web.get(f'/preview/{self._token}', self._page),
                web.get(f'/preview/{self._token}/assets/{{name}}', self._asset),
                web.get(f'/preview/{self._token}/game-assets/{{name}}', self._game_asset),
                web.get(f'/preview/{self._token}/state', self._state),
                web.post(f'/preview/{self._token}/open', self._open),
                web.post(f'/preview/{self._token}/back', self._back),
                web.post(f'/preview/{self._token}/forward', self._forward),
                web.post(f'/preview/{self._token}/home', self._home),
                web.post(f'/preview/{self._token}/close', self._close),
            )
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        sockets = cast(asyncio.Server, site._server).sockets if site._server is not None else ()
        if not sockets:
            await runner.cleanup()
            raise MapPreviewError('无法启动本地地图预览服务。')
        url = f'http://127.0.0.1:{sockets[0].getsockname()[1]}/preview/{self._token}'
        try:
            if not await asyncio.to_thread(webbrowser.open, url):
                raise MapPreviewError(f'无法打开浏览器：{url}')
            return await self._result
        finally:
            await runner.cleanup()

    async def _page(self, req: web.Request) -> web.Response:
        html = _web_file(MAP_PREVIEW_HTML_FILE).replace('assets/', f'{req.path}/assets/')
        return web.Response(text=html, content_type='text/html')

    async def _asset(self, req: web.Request) -> web.Response:
        name = req.match_info['name']
        if name not in MAP_PREVIEW_ASSETS:
            raise web.HTTPNotFound()
        content_type = 'text/css' if name.endswith('.css') else 'text/javascript'
        return web.Response(text=_web_file(name), content_type=content_type)

    async def _game_asset(self, req: web.Request) -> web.StreamResponse:
        data = _sprite_data(req.match_info['name'])
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type='image/png')

    async def _state(self, _: web.Request) -> web.Response:
        return self._state_response()

    def _state_response(self) -> web.Response:
        return web.json_response(self._state_data().model_dump(by_alias=True, exclude_none=True))

    def _state_data(self) -> _StateResp:
        page = self._pages[self._page_index]
        entrances: list[_EntranceResp] = []
        for entrance in page.layout.entrances:
            target_level_side = self._maps_by_sid.get(entrance.target_sid)
            entrances.append(
                _EntranceResp(
                    room=entrance.room,
                    target_sid=entrance.target_sid,
                    x=entrance.x,
                    y=entrance.y,
                    width=entrance.width,
                    height=entrance.height,
                    target_title=(
                        target_level_side[0].display_name(
                            target_level_side[1], self._dialogs, ('zh-cn', 'en')
                        )
                        if target_level_side is not None
                        else entrance.target_sid
                    ),
                    available=target_level_side is not None,
                    source=None if entrance.source is None else str(entrance.source),
                    entity_id=entrance.element_name,
                    attrs=None if entrance.attrs is None else dict(entrance.attrs),
                )
            )
        return _StateResp(
            title=page.title,
            rooms=tuple(self._room_resp(room) for room in page.layout.rooms),
            entrances=tuple(entrances),
            can_back=self._page_index > 0,
            can_forward=self._page_index < len(self._pages) - 1,
            can_home=self._page_index > 0,
        )

    def _room_resp(self, room: map_layout.MapRoom) -> _RoomResp:
        entities = (*room.entities, *self._extra_entities.get(room.name, ()))
        return _RoomResp(
            name=room.name,
            x=room.x,
            y=room.y,
            width=room.width,
            height=room.height,
            background=room.background,
            solids=room.solids,
            entities=tuple(
                _EntityResp(
                    x=entity.x,
                    y=entity.y,
                    kind=entity.kind,
                    key=entity.key,
                    sprite=entity.sprite,
                    entity_id=entity.entity_name,
                    attrs=None if entity.attrs is None else dict(entity.attrs),
                )
                for entity in entities
            ),
            respawns=tuple(_RespawnResp(x=item.x, y=item.y) for item in room.respawns),
        )

    async def _open(self, req: web.Request) -> web.Response:
        try:
            target_sid = _OpenMapReq.model_validate(await req.json()).target_sid
        except ValueError, web.HTTPException:
            return web.json_response({'error': '无效的目标地图。'}, status=400)
        page = self._pages[self._page_index]
        if target_sid not in {item.target_sid for item in page.layout.entrances}:
            return web.json_response({'error': '此地图入口目标无法打开。'}, status=400)
        target_level_side = self._maps_by_sid.get(target_sid)
        if target_level_side is None:
            return web.json_response({'error': '此地图入口目标无法打开。'}, status=400)
        target_level, target_side = target_level_side
        target = target_level[target_side]
        try:
            layout = await asyncio.to_thread(map_layout.load_map_layout, target)
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        del self._pages[self._page_index + 1 :]
        self._pages.append(
            _PreviewPage(
                target.map_info,
                target_level.display_name(target_side, self._dialogs, ('zh-cn', 'en')),
                layout,
            )
        )
        self._page_index += 1
        return self._state_response()

    async def _back(self, _: web.Request) -> web.Response:
        if self._page_index == 0:
            return web.json_response({'error': '没有可后退的地图。'}, status=400)
        self._page_index -= 1
        return self._state_response()

    async def _forward(self, _: web.Request) -> web.Response:
        if self._page_index >= len(self._pages) - 1:
            return web.json_response({'error': '没有可前进的地图。'}, status=400)
        self._page_index += 1
        return self._state_response()

    async def _home(self, _: web.Request) -> web.Response:
        self._page_index = 0
        return self._state_response()

    async def _close(self, _: web.Request) -> web.Response:
        if self._result is not None and not self._result.done():
            self._result.set_result(None)
        return web.json_response({'ok': True})


def _web_file(name: str) -> str:
    return files(__package__).joinpath(STATIC_DIR, name).read_text(encoding='utf-8')


def _sprite_data(name: str) -> bytes | None:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        return None
    local = LOCAL_SPRITES_DIR.joinpath(*path.parts)
    if local.is_file():
        return local.read_bytes()
    shared = files('berries').joinpath('data', 'sprites', *path.parts)
    if shared.is_file():
        return shared.read_bytes()
    builtin = files(__package__).joinpath(GAME_ASSETS_DIR, *path.parts)
    return builtin.read_bytes() if builtin.is_file() else None
