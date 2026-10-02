"""Read-only loopback browser preview for Celeste maps."""

from collections.abc import Mapping
from dataclasses import dataclass

from aiohttp import web
from pydantic import Field

from berries import map_layout
from berries.game.binmap import AttrValue
from berries.game.levels import Level, LevelSide, Map
from berries.game.maps import MapInfo
from berries.map_preview.navigation import EntranceResp, OpenMapReq, PreviewNavigation, map_title
from berries.map_preview.session import PreviewAssets, PreviewSession
from berries.models import FrozenModel


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


class _StateResp(FrozenModel):
    title: str
    rooms: tuple[_RoomResp, ...]
    entrances: tuple[EntranceResp, ...]
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
        self._navigation = PreviewNavigation(
            _PreviewPage(map_info, title or map_title(map_info, self._dialogs), layout),
            dialogs=self._dialogs,
            maps=routable_maps,
        )
        self._extra_entities = {} if extra_entities is None else dict(extra_entities)
        self._session = PreviewSession(PreviewAssets('berries.map_preview'))

    async def preview(self) -> None:
        """Open the browser preview and wait until its page is closed."""
        base = self._session.base_path
        await self._session.run(
            (
                web.get(f'{base}/state', self._state),
                web.post(f'{base}/open', self._open),
                web.post(f'{base}/back', self._back),
                web.post(f'{base}/forward', self._forward),
                web.post(f'{base}/home', self._home),
                web.post(f'{base}/close', self._close),
            )
        )

    async def _state(self, _: web.Request) -> web.Response:
        return self._state_response()

    def _state_response(self) -> web.Response:
        return web.json_response(self._state_data().model_dump(by_alias=True, exclude_none=True))

    def _state_data(self) -> _StateResp:
        page = self._navigation.current
        return _StateResp(
            title=page.title,
            rooms=tuple(self._room_resp(room) for room in page.layout.rooms),
            entrances=self._navigation.entrances(page.layout),
            can_back=self._navigation.can_back,
            can_forward=self._navigation.can_forward,
            can_home=self._navigation.can_back,
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
            target_sid = OpenMapReq.model_validate(await req.json()).target_sid
        except ValueError, web.HTTPException:
            return web.json_response({'error': '无效的目标地图。'}, status=400)
        page = self._navigation.current
        try:
            target_level, target_side, layout = await self._navigation.load_target(
                page.layout, target_sid
            )
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        if self._navigation.current is not page:
            return self._state_response()
        target = target_level[target_side]
        self._navigation.append(
            _PreviewPage(
                target.map_info,
                target_level.display_name(target_side, self._dialogs, ('zh-cn', 'en')),
                layout,
            )
        )
        return self._state_response()

    async def _back(self, _: web.Request) -> web.Response:
        if not self._navigation.back():
            return web.json_response({'error': '没有可后退的地图。'}, status=400)
        return self._state_response()

    async def _forward(self, _: web.Request) -> web.Response:
        if not self._navigation.forward():
            return web.json_response({'error': '没有可前进的地图。'}, status=400)
        return self._state_response()

    async def _home(self, _: web.Request) -> web.Response:
        self._navigation.home()
        return self._state_response()

    async def _close(self, _: web.Request) -> web.Response:
        self._session.close()
        return web.json_response({'ok': True})
