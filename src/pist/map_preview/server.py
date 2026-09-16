"""Local browser GUI for previewing one map."""

import asyncio
import secrets
import webbrowser
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import cast

from aiohttp import web

from pist.game.dialog import localized_name
from pist.game.mods import InstalledMod, LocalMap
from pist.game.routes import (
    EndersBlenderSave,
    MapLayout,
    MapMarker,
    MapRoute,
    load_map_layout,
)
from pist.game.saves import sid_for_map_file
from pist.local_data import LocalDataStore

STATIC_DIR = 'static'
GAME_ASSETS_DIR = 'game_assets'
MAP_PREVIEW_HTML_FILE = 'index.html'
MAP_PREVIEW_ASSETS = frozenset({'map_preview.css', 'map_preview.js'})
LOCAL_SPRITES_DIR = Path('.pist/sprites')


class MapPreviewError(RuntimeError):
    """The local browser map-preview session could not be started."""


@dataclass(slots=True)
class _MapPreviewPage:
    """One map opened during a browser map-preview session."""

    map_info: LocalMap
    layout: MapLayout
    selected: frozenset[str]
    room_counts: dict[str, int]
    excluded_markers: frozenset[str]
    first_clear_rooms: tuple[str, ...]
    first_clear_room_deaths: dict[str, int]
    first_clear_room_times: dict[str, int]


class MapPreview:
    """Serve one short-lived, loopback-only browser map-preview session."""

    def __init__(
        self,
        map_info: LocalMap,
        layout: MapLayout,
        *,
        first_clear_rooms: Iterable[str] = (),
        saved_route: MapRoute | None = None,
        local_data: LocalDataStore | None = None,
        mod: InstalledMod | None = None,
        enders_blender_save: EndersBlenderSave | None = None,
        audit_markers: dict[str, tuple[MapMarker, ...]] | None = None,
        read_only: bool = False,
    ) -> None:
        self._enders_blender_save = enders_blender_save
        room_names = layout.room_names
        first_clear = tuple(first_clear_rooms)
        defaults = saved_route.rooms if saved_route is not None else first_clear
        self._pages = [
            _MapPreviewPage(
                map_info,
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
                frozenset() if saved_route is None else saved_route.excluded_markers,
                first_clear,
                self._first_clear_room_deaths(map_info, layout),
                self._first_clear_room_times(map_info, layout),
            )
        ]
        self._page_index = 0
        self._local_data = local_data
        self._mod = mod
        self._audit_markers = audit_markers or {}
        self._read_only = read_only
        self._maps_by_sid = (
            {sid_for_map_file(item.file_path): item for item in mod.maps} if mod is not None else {}
        )
        self._token = secrets.token_urlsafe(24)
        self._result: asyncio.Future[MapRoute | None] | None = None

    async def preview(self) -> MapRoute | None:
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

    async def _page(self, request: web.Request) -> web.Response:
        html = _web_file(MAP_PREVIEW_HTML_FILE).replace('assets/', f'{request.path}/assets/')
        return web.Response(text=html, content_type='text/html')

    async def _asset(self, request: web.Request) -> web.Response:
        name = request.match_info['name']
        if name not in MAP_PREVIEW_ASSETS:
            raise web.HTTPNotFound()
        content_type = 'text/css' if name.endswith('.css') else 'text/javascript'
        return web.Response(text=_web_file(name), content_type=content_type)

    async def _game_asset(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info['name']
        data = _sprite_data(name)
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type='image/png')

    async def _state(self, _: web.Request) -> web.Response:
        return web.json_response(self._state_data())

    def _state_data(self) -> dict[str, object]:
        page = self._pages[self._page_index]
        title = localized_name(page.map_info.names, ('zh-cn', 'en')) or page.map_info.fallback_name
        links: list[dict[str, object]] = []
        for link in () if self._read_only else page.layout.links:
            target = self._maps_by_sid.get(link.target_sid)
            links.append(
                {
                    'room': link.room,
                    'targetSid': link.target_sid,
                    'x': link.x,
                    'y': link.y,
                    'width': link.width,
                    'height': link.height,
                    'targetTitle': (
                        localized_name(target.names, ('zh-cn', 'en')) or target.fallback_name
                        if target is not None
                        else link.target_sid
                    ),
                    'available': target is not None,
                }
            )
        return {
            'title': title,
            'rooms': [
                {
                    'name': room.name,
                    'x': room.x,
                    'y': room.y,
                    'width': room.width,
                    'height': room.height,
                    'background': room.background,
                    'solids': room.solids,
                    'markers': [
                        {
                            'x': marker.x,
                            'y': marker.y,
                            'kind': str(marker.kind),
                            **({} if marker.key is None else {'key': marker.key}),
                            **(
                                {}
                                if marker.key not in page.excluded_markers
                                else {'excluded': True}
                            ),
                            **({} if marker.sprite is None else {'sprite': marker.sprite}),
                            **(
                                {}
                                if marker.entity_name is None
                                else {'entityId': marker.entity_name}
                            ),
                            **({} if marker.attrs is None else {'attrs': marker.attrs}),
                            **(
                                {}
                                if marker.summary_kind is None or marker.summary_stat is None
                                else {
                                    'summary': {
                                        'kind': marker.summary_kind,
                                        'stat': str(marker.summary_stat),
                                        'label': marker.summary_label,
                                        'value': marker.summary_value,
                                    }
                                }
                            ),
                        }
                        for marker in (*room.markers, *self._audit_markers.get(room.name, ()))
                    ],
                    'respawns': [{'x': respawn.x, 'y': respawn.y} for respawn in room.respawns],
                    'respawnCount': len(room.respawns),
                    'roomCount': page.room_counts.get(room.name, 1),
                    'firstClearDeath': page.first_clear_room_deaths.get(room.name),
                    'firstClearTime': page.first_clear_room_times.get(room.name),
                }
                for room in page.layout.rooms
            ],
            'selected': sorted(page.selected),
            'firstClearRooms': page.first_clear_rooms,
            'links': links,
            'canBack': self._page_index > 0,
            'canForward': self._page_index < len(self._pages) - 1,
            'canHome': self._page_index > 0,
            'readOnly': self._read_only,
        }

    async def _open(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except ValueError, web.HTTPException:
            return web.json_response({'error': '无效的目标地图。'}, status=400)
        target_sid = data.get('targetSid') if isinstance(data, dict) else None
        page = self._pages[self._page_index]
        source_index = self._page_index
        source_map_file = page.map_info.file_path
        allowed = {link.target_sid for link in page.layout.links}
        target = self._maps_by_sid.get(target_sid) if isinstance(target_sid, str) else None
        if target is None or target_sid not in allowed or self._mod is None:
            return web.json_response({'error': '此路由目标无法打开。'}, status=400)
        try:
            layout = await asyncio.to_thread(load_map_layout, self._mod, target)
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        if (
            self._page_index != source_index
            or self._pages[source_index].map_info.file_path != source_map_file
        ):
            return web.json_response(self._state_data())
        del self._pages[self._page_index + 1 :]
        first_clear_rooms = (
            self._enders_blender_save.first_clear_room_order(target)
            if self._enders_blender_save is not None
            else ()
        )
        room_names = layout.room_names
        try:
            saved_route = (
                self._local_data.load_route(target.file_path)
                if self._local_data is not None
                else None
            )
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        defaults = saved_route.rooms if saved_route is not None else first_clear_rooms
        self._pages.append(
            _MapPreviewPage(
                target,
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
                frozenset() if saved_route is None else saved_route.excluded_markers,
                first_clear_rooms,
                self._first_clear_room_deaths(target, layout),
                self._first_clear_room_times(target, layout),
            )
        )
        self._page_index += 1
        return web.json_response(self._state_data())

    async def _back(self, _: web.Request) -> web.Response:
        if self._page_index == 0:
            if self._result is not None and not self._result.done():
                self._result.set_result(None)
            return web.json_response({'closed': True})
        self._page_index -= 1
        return web.json_response(self._state_data())

    async def _forward(self, _: web.Request) -> web.Response:
        if self._page_index >= len(self._pages) - 1:
            return web.json_response({'error': '没有可前进的地图'}, status=400)
        self._page_index += 1
        return web.json_response(self._state_data())

    async def _home(self, _: web.Request) -> web.Response:
        self._page_index = 0
        return web.json_response(self._state_data())

    async def _save(self, request: web.Request) -> web.Response:
        if self._read_only:
            return web.json_response({'error': '只读预览不能保存路线。'}, status=400)
        saved = await self._saved_selection(request)
        if saved is None:
            return web.json_response({'error': '无效的地图预览保存内容。'}, status=400)
        selected, excluded_markers, room_counts = saved
        page = self._pages[self._page_index]
        page.selected = selected
        page.room_counts = room_counts
        page.excluded_markers = excluded_markers
        route = MapRoute(
            map_file=page.map_info.file_path,
            rooms=tuple(room.name for room in page.layout.rooms if room.name in selected),
            room_counts=room_counts,
            excluded_markers=excluded_markers,
        )
        if self._local_data is not None:
            self._local_data.save_route(route)
        return web.json_response({'ok': True})

    async def _cancel(self, _: web.Request) -> web.Response:
        """Discard current browser edits by returning the most recently saved state."""
        return web.json_response(self._state_data())

    async def _abandon(self, _: web.Request) -> web.Response:
        """End a browser session without changing its most recently saved routes."""
        if self._result is not None and not self._result.done():
            self._result.set_result(None)
        return web.json_response({'ok': True})

    async def _saved_selection(
        self, request: web.Request
    ) -> tuple[frozenset[str], frozenset[str], dict[str, int]] | None:
        try:
            data = await request.json()
        except ValueError, web.HTTPException:
            return None
        if not isinstance(data, dict):
            return None
        rooms = data.get('rooms')
        room_counts = data.get('roomCounts', {})
        excluded_markers = data.get('excludedMarkers', [])
        if not isinstance(rooms, list) or not all(isinstance(room, str) for room in rooms):
            return None
        if not isinstance(excluded_markers, list) or not all(
            isinstance(marker, str) for marker in excluded_markers
        ):
            return None
        if not isinstance(room_counts, dict) or not all(
            isinstance(room, str) and type(count) is int and count >= 1
            for room, count in room_counts.items()
        ):
            return None
        page = self._pages[self._page_index]
        selected = frozenset(rooms)
        marker_keys = {
            marker.key
            for room in page.layout.rooms
            for marker in room.markers
            if marker.key is not None
        }
        excluded = frozenset(excluded_markers)
        if (
            not selected <= page.layout.room_names
            or not excluded <= marker_keys
            or not room_counts.keys() <= selected
        ):
            return None
        return (
            selected,
            excluded,
            {room: count for room, count in room_counts.items() if count != 1},
        )

    def _first_clear_room_deaths(self, map_info: LocalMap, layout: MapLayout) -> dict[str, int]:
        if self._enders_blender_save is None:
            return {}
        return {
            room.name: death
            for room in layout.rooms
            if (death := self._enders_blender_save.first_clear_room_death(map_info, room.name))
            is not None
        }

    def _first_clear_room_times(self, map_info: LocalMap, layout: MapLayout) -> dict[str, int]:
        if self._enders_blender_save is None:
            return {}
        return {
            room.name: time.total_milliseconds
            for room in layout.rooms
            if (time := self._enders_blender_save.first_clear_room_time(map_info, room.name))
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


MAP_PREVIEW_HTML = _web_file(MAP_PREVIEW_HTML_FILE)
