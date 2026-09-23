"""Local browser GUI for previewing one map."""

import asyncio
import secrets
import webbrowser
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import PurePosixPath
from typing import cast

from aiohttp import web
from pydantic import Field, PositiveInt

from pist.game import dialog, routes
from pist.game.binmap import AttrValue
from pist.game.levels import Level, LevelSide, LoadedMap, map_display_name
from pist.game.maps import MapInfo
from pist.local_data import LocalDataStore
from pist.models import FrozenModel, StrictModel
from pist.paths import PIST_DIR

STATIC_DIR = 'static'
GAME_ASSETS_DIR = 'game_assets'
MAP_PREVIEW_HTML_FILE = 'index.html'
MAP_PREVIEW_ASSETS = frozenset({'map_preview.css', 'map_preview.js'})
LOCAL_SPRITES_DIR = PIST_DIR / 'sprites'


def _map_title(map_info: MapInfo, dialogs: Mapping[str, Mapping[str, str]]) -> str:
    """Resolve a raw map asset title for preview links without freezing Dialog text."""
    base_file, side = dialog.split_map_side_suffix(map_info.file_path)
    key = dialog.dialog_key_for_map_file(base_file)
    names = {
        language: f'{name} {side}' if side is not None else name
        for language, entries in dialogs.items()
        if (name := entries.get(key)) is not None
    }
    fallback = dialog.default_map_name(base_file)
    return dialog.localized_name(names, ('zh-cn', 'en')) or (
        f'{fallback} {side}' if side is not None else fallback
    )


class MapPreviewError(RuntimeError):
    """The local browser map-preview session could not be started."""


class MapPreviewMode(StrEnum):
    """One interaction mode exposed by the browser map preview."""

    REVIEW = 'review'
    PREVIEW = 'preview'


class _OpenMapReq(StrictModel):
    """One browser request to open an allowed linked map."""

    target_sid: str = Field(alias='targetSid')


class _SaveRouteReq(StrictModel):
    """One browser request to persist route and entity-exclusion edits."""

    rooms: list[str]
    room_counts: dict[str, PositiveInt] = Field(default_factory=dict, alias='roomCounts')
    excluded_entities: list[str] = Field(default_factory=list, alias='excludedEntities')


class _PreviewEntitySummary(FrozenModel):
    """One configured statistic represented by a preview entity."""

    kind: str
    stat: str
    label: str
    value: str | None = None


class _PreviewEntityResp(FrozenModel):
    """One entity projected into the browser preview protocol."""

    x: int | float
    y: int | float
    kind: str
    key: str | None = None
    excluded: bool | None = None
    sprite: str | None = None
    entity_id: str | None = Field(default=None, serialization_alias='entityId')
    attrs: dict[str, AttrValue] | None = None
    summary: _PreviewEntitySummary | None = None


class _PreviewRespawnResp(FrozenModel):
    """One respawn position in the browser preview protocol."""

    x: int | float
    y: int | float


class _PreviewRoomResp(FrozenModel):
    """One room and its rendered content in the browser preview protocol."""

    name: str
    x: int | None
    y: int | None
    width: int | None
    height: int | None
    background: tuple[str, ...]
    solids: tuple[str, ...]
    entities: tuple[_PreviewEntityResp, ...]
    respawns: tuple[_PreviewRespawnResp, ...]
    respawn_count: int = Field(serialization_alias='respawnCount')
    room_count: int = Field(serialization_alias='roomCount')
    first_clear_death: int | None = Field(default=None, serialization_alias='firstClearDeath')
    first_clear_time: int | None = Field(default=None, serialization_alias='firstClearTime')


class _PreviewEntranceResp(FrozenModel):
    """One available map transition in the browser preview protocol."""

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


class _MapPreviewStateResp(FrozenModel):
    """The complete browser state returned by the local map-preview server."""

    title: str
    rooms: tuple[_PreviewRoomResp, ...]
    selected: tuple[str, ...]
    first_clear_rooms: tuple[str, ...] = Field(serialization_alias='firstClearRooms')
    entrances: tuple[_PreviewEntranceResp, ...]
    can_back: bool = Field(serialization_alias='canBack')
    can_forward: bool = Field(serialization_alias='canForward')
    can_home: bool = Field(serialization_alias='canHome')
    read_only: bool = Field(serialization_alias='readOnly')
    initial_mode: MapPreviewMode = Field(serialization_alias='initialMode')


@dataclass(slots=True)
class _MapPreviewPage:
    """One map opened during a browser map-preview session."""

    map_info: MapInfo
    title: str
    layout: routes.MapLayout
    selected: frozenset[str]
    room_counts: dict[str, int]
    excluded_entities: frozenset[str]
    first_clear_rooms: tuple[str, ...]
    first_clear_room_deaths: dict[str, int]
    first_clear_room_times: dict[str, int]


class MapPreview:
    """Serve one short-lived, loopback-only browser map-preview session."""

    def __init__(
        self,
        map_source: MapInfo | LoadedMap,
        layout: routes.MapLayout,
        *,
        first_clear_rooms: Iterable[str] = (),
        saved_route: routes.MapRoute | None = None,
        local_data: LocalDataStore | None = None,
        enders_blender_save: routes.EndersBlenderSave | None = None,
        audit_entities: dict[str, tuple[routes.MapPreviewEntity, ...]] | None = None,
        read_only: bool = False,
        initial_mode: MapPreviewMode = MapPreviewMode.REVIEW,
        title: str | None = None,
        dialogs: Mapping[str, Mapping[str, str]] | None = None,
        level_side: tuple[Level, LevelSide] | None = None,
        routable_maps: Mapping[str, tuple[Level, LevelSide]] | None = None,
    ) -> None:
        map_info = map_source.info if isinstance(map_source, LoadedMap) else map_source
        self._enders_blender_save = enders_blender_save
        room_names = layout.room_names
        first_clear = tuple(first_clear_rooms)
        defaults = saved_route.rooms if saved_route is not None else first_clear
        self._pages = [
            _MapPreviewPage(
                map_info,
                title or _map_title(map_info, dialogs or {}),
                layout,
                frozenset(room for room in defaults if room in room_names),
                (
                    {
                        room: count
                        for room, count in saved_route.room_counts.items()
                        if room in room_names
                    }
                    if saved_route is not None
                    else {}
                ),
                frozenset() if saved_route is None else saved_route.excluded_entities,
                first_clear,
                self._first_clear_room_deaths(map_info, layout, level_side),
                self._first_clear_room_times(map_info, layout, level_side),
            )
        ]
        self._page_index = 0
        self._local_data = local_data
        self._audit_entities = audit_entities or {}
        self._read_only = read_only
        self._initial_mode = MapPreviewMode.PREVIEW if read_only else initial_mode
        self._dialogs = dialogs or {}
        self._maps_by_sid = {} if routable_maps is None else dict(routable_maps)
        self._token = secrets.token_urlsafe(24)
        self._result: asyncio.Future[None] | None = None

    async def preview(self) -> None:
        """Open the browser GUI and wait until it is closed or navigated back past its start."""
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
                web.post(f'/preview/{self._token}/save', self._save),
                web.post(f'/preview/{self._token}/cancel', self._cancel),
                web.post(f'/preview/{self._token}/abandon', self._abandon),
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
        name = req.match_info['name']
        data = _sprite_data(name)
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type='image/png')

    async def _state(self, _: web.Request) -> web.Response:
        return self._state_response()

    def _state_response(self) -> web.Response:
        """Serialize the current page with the browser protocol's field aliases."""
        return web.json_response(self._state_data().model_dump(by_alias=True, exclude_none=True))

    def _state_data(self) -> _MapPreviewStateResp:
        page = self._pages[self._page_index]
        entrances: list[_PreviewEntranceResp] = []
        for entrance in () if self._read_only else page.layout.entrances:
            target_level_side = self._maps_by_sid.get(entrance.target_sid)
            target = (
                target_level_side[0].maps_by_side[target_level_side[1]]
                if target_level_side is not None
                else None
            )
            entrances.append(
                _PreviewEntranceResp(
                    room=entrance.room,
                    target_sid=entrance.target_sid,
                    x=entrance.x,
                    y=entrance.y,
                    width=entrance.width,
                    height=entrance.height,
                    target_title=(
                        map_display_name(
                            target_level_side[0],
                            target_level_side[1],
                            self._dialogs,
                            ('zh-cn', 'en'),
                        )
                        if target_level_side is not None
                        else entrance.target_sid
                    ),
                    available=target is not None,
                    source=None if entrance.source is None else str(entrance.source),
                    entity_id=entrance.element_name,
                    attrs=None if entrance.attrs is None else dict(entrance.attrs),
                )
            )
        return _MapPreviewStateResp(
            title=page.title,
            rooms=tuple(self._room_resp(room, page) for room in page.layout.rooms),
            selected=tuple(sorted(page.selected)),
            first_clear_rooms=page.first_clear_rooms,
            entrances=tuple(entrances),
            can_back=self._page_index > 0,
            can_forward=self._page_index < len(self._pages) - 1,
            can_home=self._page_index > 0,
            read_only=self._read_only,
            initial_mode=self._initial_mode,
        )

    def _room_resp(self, room: routes.MapRoom, page: _MapPreviewPage) -> _PreviewRoomResp:
        """Project one decoded room into the browser protocol."""
        entities = (*room.entities, *self._audit_entities.get(room.name, ()))
        return _PreviewRoomResp(
            name=room.name,
            x=room.x,
            y=room.y,
            width=room.width,
            height=room.height,
            background=room.background,
            solids=room.solids,
            entities=tuple(
                self._entity_resp(entity, page.excluded_entities) for entity in entities
            ),
            respawns=tuple(
                _PreviewRespawnResp(x=respawn.x, y=respawn.y) for respawn in room.respawns
            ),
            respawn_count=len(room.respawns),
            room_count=page.room_counts.get(room.name, 1),
            first_clear_death=page.first_clear_room_deaths.get(room.name),
            first_clear_time=page.first_clear_room_times.get(room.name),
        )

    @staticmethod
    def _entity_resp(
        entity: routes.MapPreviewEntity, excluded_entities: frozenset[str]
    ) -> _PreviewEntityResp:
        """Project one configured or audited entity into the browser protocol."""
        summary = (
            None
            if entity.summary_kind is None or entity.summary_stat is None
            else _PreviewEntitySummary(
                kind=entity.summary_kind,
                stat=str(entity.summary_stat),
                label=entity.summary_label or entity.summary_kind,
                value=entity.summary_value,
            )
        )
        return _PreviewEntityResp(
            x=entity.x,
            y=entity.y,
            kind=entity.kind,
            key=entity.key,
            excluded=True if entity.key in excluded_entities else None,
            sprite=entity.sprite,
            entity_id=entity.entity_name,
            attrs=None if entity.attrs is None else dict(entity.attrs),
            summary=summary,
        )

    async def _open(self, req: web.Request) -> web.Response:
        try:
            target_sid = _OpenMapReq.model_validate(await req.json()).target_sid
        except ValueError, web.HTTPException:
            return web.json_response({'error': '无效的目标地图。'}, status=400)
        page = self._pages[self._page_index]
        source_index = self._page_index
        source_map_file = page.map_info.file_path
        allowed = {entrance.target_sid for entrance in page.layout.entrances}
        target_level_side = self._maps_by_sid.get(target_sid)
        if target_level_side is None or target_sid not in allowed:
            return web.json_response({'error': '此路由目标无法打开。'}, status=400)
        target_level, target_side = target_level_side
        target = target_level.maps_by_side[target_side]
        try:
            layout = await asyncio.to_thread(routes.load_loaded_map_layout, target)
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        if (
            self._page_index != source_index
            or self._pages[source_index].map_info.file_path != source_map_file
        ):
            return self._state_response()
        del self._pages[self._page_index + 1 :]
        first_clear_rooms = (
            self._enders_blender_save.first_clear_room_order(target_level, target_side)
            if self._enders_blender_save is not None
            else ()
        )
        room_names = layout.room_names
        try:
            saved_route = (
                self._local_data.load_route(target.info.file_path.as_posix())
                if self._local_data is not None
                else None
            )
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        defaults = saved_route.rooms if saved_route is not None else first_clear_rooms
        self._pages.append(
            _MapPreviewPage(
                target.info,
                map_display_name(target_level, target_side, self._dialogs, ('zh-cn', 'en')),
                layout,
                frozenset(room for room in defaults if room in room_names),
                (
                    {
                        room: count
                        for room, count in saved_route.room_counts.items()
                        if room in room_names
                    }
                    if saved_route is not None
                    else {}
                ),
                frozenset() if saved_route is None else saved_route.excluded_entities,
                first_clear_rooms,
                self._first_clear_room_deaths(target.info, layout, target_level_side),
                self._first_clear_room_times(target.info, layout, target_level_side),
            )
        )
        self._page_index += 1
        return self._state_response()

    async def _back(self, _: web.Request) -> web.Response:
        if self._page_index == 0:
            if self._result is not None and not self._result.done():
                self._result.set_result(None)
            return web.json_response({'closed': True})
        self._page_index -= 1
        return self._state_response()

    async def _forward(self, _: web.Request) -> web.Response:
        if self._page_index >= len(self._pages) - 1:
            return web.json_response({'error': '没有可前进的地图'}, status=400)
        self._page_index += 1
        return self._state_response()

    async def _home(self, _: web.Request) -> web.Response:
        self._page_index = 0
        return self._state_response()

    async def _save(self, req: web.Request) -> web.Response:
        if self._read_only:
            return web.json_response({'error': '只读预览不能保存路线。'}, status=400)
        saved = await self._saved_selection(req)
        if saved is None:
            return web.json_response({'error': '无效的地图预览保存内容。'}, status=400)
        selected, excluded_entities, room_counts = saved
        page = self._pages[self._page_index]
        page.selected = selected
        page.room_counts = room_counts
        page.excluded_entities = excluded_entities
        route = routes.MapRoute(
            map_file=page.map_info.file_path.as_posix(),
            rooms=tuple(room.name for room in page.layout.rooms if room.name in selected),
            room_counts=room_counts,
            excluded_entities=excluded_entities,
        )
        if self._local_data is not None:
            self._local_data.save_route(route)
        return web.json_response({'ok': True})

    async def _cancel(self, _: web.Request) -> web.Response:
        """Discard current browser edits by returning the most recently saved state."""
        return self._state_response()

    async def _abandon(self, _: web.Request) -> web.Response:
        """End a browser session without changing its most recently saved routes."""
        if self._result is not None and not self._result.done():
            self._result.set_result(None)
        return web.json_response({'ok': True})

    async def _saved_selection(
        self, req: web.Request
    ) -> tuple[frozenset[str], frozenset[str], dict[str, int]] | None:
        try:
            data = _SaveRouteReq.model_validate(await req.json())
        except ValueError, web.HTTPException:
            return None
        page = self._pages[self._page_index]
        selected = frozenset(data.rooms)
        entity_keys = {
            entity.key
            for room in page.layout.rooms
            for entity in room.entities
            if entity.key is not None
        }
        excluded = frozenset(data.excluded_entities)
        if (
            not selected <= page.layout.room_names
            or not excluded <= entity_keys
            or not data.room_counts.keys() <= selected
        ):
            return None
        return (
            selected,
            excluded,
            {room: count for room, count in data.room_counts.items() if count != 1},
        )

    def _first_clear_room_deaths(
        self,
        map_info: MapInfo,
        layout: routes.MapLayout,
        level_side: tuple[Level, LevelSide] | None = None,
    ) -> dict[str, int]:
        if self._enders_blender_save is None:
            return {}
        level, side = level_side or (None, None)
        return {
            room.name: death
            for room in layout.rooms
            if (
                death := (
                    self._enders_blender_save.first_clear_room_death(level, side, room.name)
                    if level is not None and side is not None
                    else self._enders_blender_save.first_clear_room_death_for_file(
                        map_info, room.name
                    )
                )
            )
            is not None
        }

    def _first_clear_room_times(
        self,
        map_info: MapInfo,
        layout: routes.MapLayout,
        level_side: tuple[Level, LevelSide] | None = None,
    ) -> dict[str, int]:
        if self._enders_blender_save is None:
            return {}
        level, side = level_side or (None, None)
        return {
            room.name: time.total_milliseconds
            for room in layout.rooms
            if (
                time := (
                    self._enders_blender_save.first_clear_room_time(level, side, room.name)
                    if level is not None and side is not None
                    else self._enders_blender_save.first_clear_room_time_for_file(
                        map_info, room.name
                    )
                )
            )
            is not None
        }


def _web_file(name: str) -> str:
    return files(__package__).joinpath(STATIC_DIR, name).read_text(encoding='utf-8')


def _sprite_data(name: str) -> bytes | None:
    """Read one configured relative sprite without exposing arbitrary local paths."""
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        return None
    local = LOCAL_SPRITES_DIR.joinpath(*path.parts)
    if local.is_file():
        return local.read_bytes()
    shared = files('pist').joinpath('data', 'sprites', *path.parts)
    if shared.is_file():
        return shared.read_bytes()
    builtin = files(__package__).joinpath(GAME_ASSETS_DIR, *path.parts)
    if builtin.is_file():
        return builtin.read_bytes()
    return None
