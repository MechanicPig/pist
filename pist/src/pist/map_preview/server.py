"""Personal map preview with route editing and first-clear overlays."""

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from aiohttp import web
from pydantic import Field, PositiveInt

from berries import map_layout
from berries.entities import rules as entity_rules
from berries.game.binmap import AttrValue
from berries.game.enders_blender import EndersBlenderSave, FirstClearStats
from berries.game.levels import Level, LevelSide, Map
from berries.game.maps import MapInfo
from berries.map_preview.navigation import EntranceResp, OpenMapReq, PreviewNavigation, map_title
from berries.map_preview.session import PreviewAssets, PreviewSession
from berries.models import FrozenModel, StrictModel
from pist import entity_stats
from pist.paths import PIST_DIR
from pist.routes import models as routes
from pist.routes.store import RouteStore


class MapPreviewMode(StrEnum):
    """One interaction mode exposed by the browser map preview."""

    EDIT_ROUTE = 'edit_route'
    PREVIEW = 'preview'


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
    first_clear_cumulative_time: int | None = Field(
        default=None,
        serialization_alias='firstClearCumulativeTime',
    )


class _MapPreviewStateResp(FrozenModel):
    """The complete browser state returned by the local map-preview server."""

    title: str
    rooms: tuple[_PreviewRoomResp, ...]
    selected: tuple[str, ...]
    first_clear_rooms: tuple[str, ...] = Field(serialization_alias='firstClearRooms')
    entrances: tuple[EntranceResp, ...]
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
    layout: map_layout.MapLayout
    selected: frozenset[str]
    room_counts: dict[str, int]
    excluded_entities: frozenset[str]
    first_clear_rooms: tuple[str, ...]
    first_clear_room_deaths: dict[str, int]
    first_clear_room_times: dict[str, int]

    @property
    def first_clear_cumulative_times(self) -> dict[str, int]:
        """Return elapsed first-clear time through each room with a complete prefix."""
        elapsed = 0
        cumulative: dict[str, int] = {}
        for room in self.first_clear_rooms:
            if room in cumulative:
                continue
            room_time = self.first_clear_room_times.get(room)
            if room_time is None:
                break
            elapsed += room_time
            cumulative[room] = elapsed
        return cumulative


class MapPreview:
    """Serve one short-lived, loopback-only browser map-preview session."""

    def __init__(
        self,
        map_source: MapInfo | Map,
        layout: map_layout.MapLayout,
        *,
        first_clear_rooms: Iterable[str] = (),
        saved_route: routes.MapRoute | None = None,
        route_store: RouteStore | None = None,
        enders_blender_save: EndersBlenderSave | None = None,
        audit_entities: dict[str, tuple[map_layout.MapPreviewEntity, ...]] | None = None,
        read_only: bool = False,
        initial_mode: MapPreviewMode = MapPreviewMode.EDIT_ROUTE,
        title: str | None = None,
        dialogs: Mapping[str, Mapping[str, str]] | None = None,
        level_side: tuple[Level, LevelSide] | None = None,
        routable_maps: Mapping[str, tuple[Level, LevelSide]] | None = None,
        classification_rules: entity_rules.EntityRules | None = None,
        statistic_rules: entity_stats.EntityStatRules | None = None,
    ) -> None:
        map_info = map_source.map_info if isinstance(map_source, Map) else map_source
        self._enders_blender_save = enders_blender_save
        room_names = layout.room_names
        first_clear = tuple(first_clear_rooms)
        defaults = saved_route.rooms if saved_route is not None else first_clear
        self._navigation = PreviewNavigation(
            _MapPreviewPage(
                map_info,
                title or map_title(map_info, dialogs or {}),
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
            ),
            dialogs=dialogs or {},
            maps=routable_maps,
        )
        self._route_store = route_store
        self._audit_entities = audit_entities or {}
        self._read_only = read_only
        self._initial_mode = MapPreviewMode.PREVIEW if read_only else initial_mode
        self._dialogs = dialogs or {}
        if statistic_rules is not None and classification_rules is None:
            raise ValueError('Statistic rules require classification rules.')
        self._classification_rules = classification_rules
        self._statistic_rules = statistic_rules
        self._session = PreviewSession(
            PreviewAssets(
                'pist.map_preview',
                sprite_dir=PIST_DIR / 'sprites',
                scripts=('edit_state.js', 'overlays.js', 'room_list.js'),
            )
        )

    async def preview(self) -> None:
        """Open the browser GUI and wait until it is closed or navigated back past its start."""
        base = self._session.base_path
        await self._session.run(
            (
                web.get(f'{base}/state', self._state),
                web.post(f'{base}/open', self._open),
                web.post(f'{base}/back', self._back),
                web.post(f'{base}/forward', self._forward),
                web.post(f'{base}/home', self._home),
                web.post(f'{base}/save', self._save),
                web.post(f'{base}/cancel', self._cancel),
                web.post(f'{base}/abandon', self._abandon),
            )
        )

    async def _state(self, _: web.Request) -> web.Response:
        return self._state_response()

    def _state_response(self) -> web.Response:
        """Serialize the current page with the browser protocol's field aliases."""
        return web.json_response(self._state_data().model_dump(by_alias=True, exclude_none=True))

    def _state_data(self) -> _MapPreviewStateResp:
        page = self._navigation.current
        cumulative_times = page.first_clear_cumulative_times
        return _MapPreviewStateResp(
            title=page.title,
            rooms=tuple(
                self._room_resp(room, page, cumulative_times) for room in page.layout.rooms
            ),
            selected=tuple(sorted(page.selected)),
            first_clear_rooms=page.first_clear_rooms,
            entrances=() if self._read_only else self._navigation.entrances(page.layout),
            can_back=self._navigation.can_back,
            can_forward=self._navigation.can_forward,
            can_home=self._navigation.can_back,
            read_only=self._read_only,
            initial_mode=self._initial_mode,
        )

    def _room_resp(
        self,
        room: map_layout.MapRoom,
        page: _MapPreviewPage,
        cumulative_times: Mapping[str, int],
    ) -> _PreviewRoomResp:
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
            first_clear_cumulative_time=cumulative_times.get(room.name),
        )

    def _entity_resp(
        self,
        entity: map_layout.MapPreviewEntity,
        excluded_entities: frozenset[str],
    ) -> _PreviewEntityResp:
        """Project one configured or audited entity into the browser protocol."""
        summary = None
        if self._classification_rules is not None and self._statistic_rules is not None:
            for name, rule in self._statistic_rules.stats.items():
                if self._classification_rules.kind_is_a(entity.kind, rule.kind):
                    summary = _PreviewEntitySummary(
                        kind=name,
                        stat=rule.aggregation,
                        label=self._classification_rules.kinds[rule.kind].label,
                        value=entity_stats.select_value_for_kind(
                            entity.kind,
                            rule,
                            self._classification_rules,
                        ),
                    )
                    break
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
        target_level_side = (target_level, target_side)
        target = target_level[target_side]
        first_clear_rooms = (
            self._enders_blender_save.first_clear_room_order(target_level, target_side)
            if self._enders_blender_save is not None
            else ()
        )
        room_names = layout.room_names
        try:
            saved_route = (
                self._route_store.load(target.file_path.as_posix())
                if self._route_store is not None
                else None
            )
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        defaults = saved_route.rooms if saved_route is not None else first_clear_rooms
        self._navigation.append(
            _MapPreviewPage(
                target.map_info,
                target_level.display_name(target_side, self._dialogs, ('zh-cn', 'en')),
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
                self._first_clear_room_deaths(target.map_info, layout, target_level_side),
                self._first_clear_room_times(target.map_info, layout, target_level_side),
            )
        )
        return self._state_response()

    async def _back(self, _: web.Request) -> web.Response:
        if not self._navigation.back():
            self._session.close()
            return web.json_response({'closed': True})
        return self._state_response()

    async def _forward(self, _: web.Request) -> web.Response:
        if not self._navigation.forward():
            return web.json_response({'error': '没有可前进的地图。'}, status=400)
        return self._state_response()

    async def _home(self, _: web.Request) -> web.Response:
        self._navigation.home()
        return self._state_response()

    async def _save(self, req: web.Request) -> web.Response:
        if self._read_only:
            return web.json_response({'error': '只读预览不能保存路线。'}, status=400)
        saved = await self._saved_selection(req)
        if saved is None:
            return web.json_response({'error': '无效的地图预览保存内容。'}, status=400)
        selected, excluded_entities, room_counts = saved
        page = self._navigation.current
        route = routes.MapRoute(
            map_file=page.map_info.file_path.as_posix(),
            rooms=tuple(room.name for room in page.layout.rooms if room.name in selected),
            room_counts=room_counts,
            excluded_entities=excluded_entities,
        )
        if self._route_store is not None:
            try:
                self._route_store.save(route)
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as error:
                return web.json_response({'error': f'路线保存失败：{error}'}, status=500)
        page.selected = selected
        page.room_counts = room_counts
        page.excluded_entities = excluded_entities
        return web.json_response({'ok': True})

    async def _cancel(self, _: web.Request) -> web.Response:
        """Discard current browser edits by returning the most recently saved state."""
        return self._state_response()

    async def _abandon(self, _: web.Request) -> web.Response:
        """End a browser session without changing its most recently saved routes."""
        self._session.close()
        return web.json_response({'ok': True})

    async def _saved_selection(
        self, req: web.Request
    ) -> tuple[frozenset[str], frozenset[str], dict[str, int]] | None:
        try:
            data = _SaveRouteReq.model_validate(await req.json())
        except ValueError, web.HTTPException:
            return None
        page = self._navigation.current
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

    def _first_clear_stats(
        self,
        map_info: MapInfo,
        level_side: tuple[Level, LevelSide] | None,
    ) -> FirstClearStats | None:
        if self._enders_blender_save is None:
            return None
        if level_side is not None:
            return self._enders_blender_save.first_clear_stats(*level_side)
        return self._enders_blender_save.first_clear_stats_for_file(map_info)

    def _first_clear_room_deaths(
        self,
        map_info: MapInfo,
        layout: map_layout.MapLayout,
        level_side: tuple[Level, LevelSide] | None = None,
    ) -> dict[str, int]:
        if (stats := self._first_clear_stats(map_info, level_side)) is None:
            return {}
        return {
            room.name: death
            for room in layout.rooms
            if (death := stats.room_deaths.get(room.name)) is not None
        }

    def _first_clear_room_times(
        self,
        map_info: MapInfo,
        layout: map_layout.MapLayout,
        level_side: tuple[Level, LevelSide] | None = None,
    ) -> dict[str, int]:
        if (stats := self._first_clear_stats(map_info, level_side)) is None:
            return {}
        return {
            room.name: time.total_milliseconds
            for room in layout.rooms
            if (time := stats.room_times.get(room.name)) is not None
        }
