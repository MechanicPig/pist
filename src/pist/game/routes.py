"""Read map room layouts and Ender's Blender first-clear routes."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pist.game.binmap import BinElement, BinMap, parse_map_bin
from pist.game.entities import (
    ClassifiedEntity,
    EntityRules,
    EntityStat,
    MapEntities,
    MapEntityStats,
    analyze_map_entities,
    kind_select_value,
    load_entity_rule_layers,
    stat_owner,
)
from pist.game.map_entrances import (
    DEFAULT_MAP_ENTRANCE_RULES,
    MapEntranceRules,
    MapEntranceSource,
)
from pist.game.modpath import BadModPath, ModPath
from pist.game.saves import SAVE_EXT, SAVES_DIRNAME, sid_for_map_file
from pist.models import InstalledMod, LocalMap
from pist.time import Time

ENDERS_BLENDER_MOD_NAME = 'EndersBlender'
ENDERS_BLENDER_ROOM_ORDER_KEY = 'mapDict_roomStat_firstClear_roomOrder'
ENDERS_BLENDER_ROOM_DEATH_KEY = 'mapDict_roomStat_firstClear_death'
ENDERS_BLENDER_ROOM_TIMER_KEY = 'mapDict_roomStat_firstClear_timer'
@dataclass(frozen=True, slots=True)
class MapMarker:
    """One configured entity marker positioned within a map room."""

    x: int | float
    y: int | float
    kind: str
    sprite: str | None = None
    key: str | None = None
    entity_name: str | None = None
    attrs: Mapping[str, object] | None = None
    summary_kind: str | None = None
    summary_stat: EntityStat | None = None
    summary_label: str | None = None
    summary_value: str | None = None


@dataclass(frozen=True, slots=True)
class MapRespawn:
    """One editor-defined player respawn point within a map room."""

    x: int | float
    y: int | float


@dataclass(frozen=True, slots=True)
class MapLink:
    """One explicit in-game map transition from a room to another map SID."""

    room: str
    target_sid: str
    x: int | float | None = None
    y: int | float | None = None
    width: int | float | None = None
    height: int | float | None = None


@dataclass(frozen=True, slots=True)
class MapRoom:
    """One map room, with optional editor-space bounds for route previewing."""

    name: str
    x: int | None
    y: int | None
    width: int | None
    height: int | None
    background: tuple[str, ...] = ()
    solids: tuple[str, ...] = ()
    markers: tuple[MapMarker, ...] = ()
    respawns: tuple[MapRespawn, ...] = ()

    @property
    def has_bounds(self) -> bool:
        """Return whether this room has all four editor-space bounds."""
        return None not in (self.x, self.y, self.width, self.height)


@dataclass(frozen=True, slots=True)
class MapLayout:
    """The rooms belonging to one decoded map."""

    rooms: tuple[MapRoom, ...]
    links: tuple[MapLink, ...] = ()

    @property
    def room_names(self) -> frozenset[str]:
        """Return all room names available for route selection."""
        return frozenset(room.name for room in self.rooms)


class MapRoute(BaseModel):
    """The user-confirmed main rooms for one concrete map file."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    map_file: str
    rooms: tuple[str, ...]
    room_counts: dict[str, int] = Field(default_factory=dict)
    excluded_markers: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode='after')
    def validate_rooms(self) -> MapRoute:
        if len(set(self.rooms)) != len(self.rooms):
            raise ValueError('Map route must not contain duplicate rooms.')
        if not self.room_counts.keys() <= set(self.rooms):
            raise ValueError('Map route room counts must refer to selected rooms.')
        if any(count < 1 for count in self.room_counts.values()):
            raise ValueError('Map route room counts must be positive.')
        return self

    @property
    def room_count(self) -> int:
        """Return the weighted number of user-confirmed main rooms."""
        return sum(self.room_counts.get(room, 1) for room in self.rooms)


@dataclass(frozen=True, slots=True)
class EndersBlenderSave:
    """First-clear room data indexed by map SID."""

    number: int
    first_clear_room_orders: dict[str, tuple[str, ...]]
    first_clear_room_deaths: dict[str, dict[str, int]] = field(default_factory=dict)
    first_clear_room_times: dict[str, dict[str, Time]] = field(default_factory=dict)

    def first_clear_room_order(self, map_info: LocalMap) -> tuple[str, ...]:
        """Return the recorded first-clear sequence for one map, if available."""
        return self.first_clear_room_orders.get(_map_save_key(map_info), ())

    def first_clear_room_time(self, map_info: LocalMap, room: str) -> Time | None:
        """Return one room's recorded first-clear time, if available."""
        return self.first_clear_room_times.get(_map_save_key(map_info), {}).get(room)

    def first_clear_room_death(self, map_info: LocalMap, room: str) -> int | None:
        """Return one room's recorded first-clear death count, if available."""
        return self.first_clear_room_deaths.get(_map_save_key(map_info), {}).get(room)


class EndersBlenderReader:
    """Read Ender's Blender YAML saves without modifying the game files."""

    def __init__(self, game_dir: Path) -> None:
        self._saves_dir = game_dir / SAVES_DIRNAME

    def available_numbers(self) -> list[int]:
        """Return save slots that have an Ender's Blender persistent save."""
        if not self._saves_dir.is_dir():
            return []
        prefix = '-modsave-EndersBlender'
        return sorted(
            int(number)
            for path in self._saves_dir.glob(f'*{prefix}{SAVE_EXT}')
            if (number := path.name.removesuffix(f'{prefix}{SAVE_EXT}')).isdigit()
        )

    def load(self, number: int) -> EndersBlenderSave:
        """Load first-clear room orders for one save slot."""
        if number < 0:
            raise ValueError('Save slot number must be non-negative.')
        path = self._path(number)
        try:
            data = yaml.load(path.read_bytes(), Loader=yaml.BaseLoader)
        except (OSError, yaml.YAMLError) as error:
            raise ValueError(f'Invalid Ender’s Blender save file: {path!r}') from error
        try:
            orders = _first_clear_room_orders(data, path)
            deaths = _first_clear_room_deaths(data, path)
            times = _first_clear_room_times(data, path)
        except TypeError as error:
            raise ValueError(str(error)) from error
        return EndersBlenderSave(number, orders, deaths, times)

    def _path(self, number: int) -> Path:
        return self._saves_dir / f'{number}-modsave-{ENDERS_BLENDER_MOD_NAME}{SAVE_EXT}'


def map_layout(
    map_data: BinMap,
    *,
    entrance_rules: MapEntranceRules = DEFAULT_MAP_ENTRANCE_RULES,
    entity_rules: EntityRules | None = None,
) -> MapLayout:
    """Extract all map rooms and their editor-space bounds from a decoded map."""
    levels = next((child for child in map_data.root.children if child.name == 'levels'), None)
    if levels is None:
        return MapLayout(())
    rules = load_entity_rule_layers() if entity_rules is None else entity_rules
    entities = analyze_map_entities(map_data, rules=rules)
    markers_by_room = _markers_by_room(entities, rules)
    rooms = tuple(
        _map_room(room, markers_by_room.get(_str_attr(room.attrs.get('name')), ()))
        for room in levels.children
        if room.name == 'level'
    )
    return MapLayout(rooms, _map_entrances(levels, entrance_rules))


def load_map_layout(mod: InstalledMod, map_info: LocalMap) -> MapLayout:
    """Read one installed Mod map's room layout, including known trailing payloads."""
    return load_map_layout_from_path(Path(mod.path), map_info.file_path)


def load_map_layout_from_path(path: Path, map_file: str) -> MapLayout:
    """Read one map layout from a Mod archive, Mod directory, or Game Content directory."""
    return map_layout(_load_map_bin(path, map_file))


def load_map_entity_table_values_from_path(
    path: Path,
    map_file: str,
    *,
    excluded_markers: frozenset[str] = frozenset(),
    entity_rules: EntityRules | None = None,
) -> dict[str, dict[str, int | bool | str]]:
    """Read one map and summarize only entities not excluded by its saved route."""
    return map_entity_table_values(
        _load_map_bin(path, map_file),
        excluded_markers=excluded_markers,
        entity_rules=entity_rules,
    )


def map_entity_table_values(
    map_data: BinMap,
    *,
    excluded_markers: frozenset[str] = frozenset(),
    entity_rules: EntityRules | None = None,
) -> dict[str, dict[str, int | bool | str]]:
    """Return configured table values after applying per-entity route exclusions.

    The preview stores exclusions using the stable marker key, rather than a rule
    condition, because an inaccessible instance does not make every matching
    entity inaccessible. Rebuilding the aggregate here keeps draft data aligned
    with the correction summary shown in the preview.
    """
    rules = load_entity_rule_layers() if entity_rules is None else entity_rules
    entities = analyze_map_entities(map_data, rules=rules)
    counted: dict[str, list[ClassifiedEntity]] = {}
    existing: dict[str, list[ClassifiedEntity]] = {}
    selected: dict[str, list[ClassifiedEntity]] = {}
    select_values: dict[str, set[str]] = {}
    for entity in entities.entities:
        if _marker_key(entity) in excluded_markers:
            continue
        match stat_owner(entity.kind, rules=rules):
            case stat_kind, EntityStat.COUNT, _:
                counted.setdefault(stat_kind, []).append(entity)
            case stat_kind, EntityStat.EXIST, _:
                existing.setdefault(stat_kind, []).append(entity)
            case stat_kind, EntityStat.SELECT, _:
                selected.setdefault(stat_kind, []).append(entity)
                if (value := kind_select_value(entity.kind, rules=rules)) is not None:
                    select_values.setdefault(stat_kind, set()).add(value)
    stats = MapEntityStats(
        counted={kind: tuple(items) for kind, items in counted.items()},
        existing={kind: tuple(items) for kind, items in existing.items()},
        selected={kind: tuple(items) for kind, items in selected.items()},
        table_fields=entities.stats.table_fields,
        stat_types=entities.stats.stat_types,
        select_values={kind: frozenset(values) for kind, values in select_values.items()},
    )
    return stats.table_values


def _load_map_bin(path: Path, map_file: str) -> BinMap:
    """Read one map BIN from an archive, directory, or the Game Content directory."""
    try:
        with ModPath(path) as mod_path:
            map_path = mod_path.joinpath(map_file)
            if not map_path.is_file():
                raise ValueError(f'Map file does not exist in {path!r}: {map_file!r}')
            map_data = parse_map_bin(map_path.read_bytes(), allow_trailing=True)
    except BadModPath as error:
        raise ValueError(f'Invalid map package path: {path!r}') from error
    return map_data


def _first_clear_room_orders(data: object, path: Path) -> dict[str, tuple[str, ...]]:
    if not isinstance(data, Mapping):
        raise TypeError(f'Invalid Ender’s Blender save file: {path!r}')
    orders = data.get(ENDERS_BLENDER_ROOM_ORDER_KEY, {})
    if not isinstance(orders, Mapping):
        raise TypeError(f'Invalid Ender’s Blender room orders in: {path!r}')
    result: dict[str, tuple[str, ...]] = {}
    for sid, rooms in orders.items():
        if not isinstance(sid, str) or not isinstance(rooms, list):
            raise TypeError(f'Invalid Ender’s Blender room order in: {path!r}')
        if not all(isinstance(room, str) for room in rooms):
            raise TypeError(f'Invalid Ender’s Blender room order in: {path!r}')
        result[sid] = tuple(rooms)
    return result


def _first_clear_room_times(data: object, path: Path) -> dict[str, dict[str, Time]]:
    if not isinstance(data, Mapping):
        raise TypeError(f'Invalid Ender’s Blender save file: {path!r}')
    timers = data.get(ENDERS_BLENDER_ROOM_TIMER_KEY, {})
    if not isinstance(timers, Mapping):
        raise TypeError(f'Invalid Ender’s Blender room timers in: {path!r}')
    result: dict[str, dict[str, Time]] = {}
    for sid, room_timers in timers.items():
        if not isinstance(sid, str) or not isinstance(room_timers, Mapping):
            raise TypeError(f'Invalid Ender’s Blender room timer in: {path!r}')
        times: dict[str, Time] = {}
        for room, value in room_timers.items():
            if not isinstance(room, str) or not isinstance(value, str):
                raise TypeError(f'Invalid Ender’s Blender room timer in: {path!r}')
            try:
                times[room] = Time.from_filetime(int(value))
            except ValueError as error:
                raise TypeError(f'Invalid Ender’s Blender room timer in: {path!r}') from error
        result[sid] = times
    return result


def _first_clear_room_deaths(data: object, path: Path) -> dict[str, dict[str, int]]:
    if not isinstance(data, Mapping):
        raise TypeError(f'Invalid Ender’s Blender save file: {path!r}')
    deaths = data.get(ENDERS_BLENDER_ROOM_DEATH_KEY, {})
    if not isinstance(deaths, Mapping):
        raise TypeError(f'Invalid Ender’s Blender room deaths in: {path!r}')
    result: dict[str, dict[str, int]] = {}
    for sid, room_deaths in deaths.items():
        if not isinstance(sid, str) or not isinstance(room_deaths, Mapping):
            raise TypeError(f'Invalid Ender’s Blender room death in: {path!r}')
        values: dict[str, int] = {}
        for room, value in room_deaths.items():
            if not isinstance(room, str) or not isinstance(value, str):
                raise TypeError(f'Invalid Ender’s Blender room death in: {path!r}')
            try:
                death = int(value)
            except ValueError as error:
                raise TypeError(f'Invalid Ender’s Blender room death in: {path!r}') from error
            if death < 0:
                raise TypeError(f'Invalid Ender’s Blender room death in: {path!r}')
            values[room] = death
        result[sid] = values
    return result


def _map_save_key(map_info: LocalMap) -> str:
    sid = sid_for_map_file(map_info.file_path)
    return f'{sid}_{map_info.side}' if map_info.side is not None else sid


def _map_room(room: BinElement, markers: tuple[MapMarker, ...]) -> MapRoom:
    return MapRoom(
        name=_str_attr(room.attrs.get('name')),
        x=_int_attr(room.attrs.get('x')),
        y=_int_attr(room.attrs.get('y')),
        width=_int_attr(room.attrs.get('width')),
        height=_int_attr(room.attrs.get('height')),
        background=_tile_rows(room, 'bg'),
        solids=_tile_rows(room, 'solids'),
        markers=markers,
        respawns=_room_respawns(room),
    )


def _room_respawns(room: BinElement) -> tuple[MapRespawn, ...]:
    entities = next((child for child in room.children if child.name == 'entities'), None)
    if entities is None:
        return ()
    result: list[MapRespawn] = []
    for entity in entities.children:
        if entity.name != 'player':
            continue
        x = _int_attr(entity.attrs.get('x'))
        y = _int_attr(entity.attrs.get('y'))
        if x is not None and y is not None:
            result.append(MapRespawn(x, y))
    return tuple(result)


def _map_entrances(levels: BinElement, rules: MapEntranceRules) -> tuple[MapLink, ...]:
    """Extract configured static map entrances from room entities and triggers."""
    result: list[MapLink] = []
    for room in levels.children:
        if room.name != 'level':
            continue
        room_name = _str_attr(room.attrs.get('name'))
        for layer in room.children:
            source = _entrance_source(layer.name)
            if source is None:
                continue
            for item in layer.children:
                rule = rules.match(source, item.name, item.attrs)
                if rule is None:
                    continue
                target_sid = item.attrs.get(rule.target_attr)
                x = rule.region.x.resolve(item.attrs)
                y = rule.region.y.resolve(item.attrs)
                if not isinstance(target_sid, str) or not target_sid or x is None or y is None:
                    continue
                width = (
                    rule.region.width.resolve(item.attrs) if rule.region.width is not None else None
                )
                height = (
                    rule.region.height.resolve(item.attrs)
                    if rule.region.height is not None
                    else None
                )
                result.append(MapLink(room_name, target_sid, x, y, width, height))
    return tuple(result)


def _entrance_source(layer_name: str) -> MapEntranceSource | None:
    match layer_name:
        case 'entities':
            return MapEntranceSource.ENTITY
        case 'triggers':
            return MapEntranceSource.TRIGGER
        case _:
            return None


def _markers_by_room(entities: MapEntities, rules: EntityRules) -> dict[str, tuple[MapMarker, ...]]:
    result: dict[str, list[MapMarker]] = {}
    for entity in _classified_entities(entities):
        if entity.x is None or entity.y is None:
            continue
        owner = stat_owner(entity.kind, rules=rules)
        if owner is None:
            summary_kind = None
            summary_stat = None
            summary_label = None
            summary_value = None
        else:
            summary_kind, summary_stat, _ = owner
            summary_label = rules.kinds[summary_kind].label
            summary_value = kind_select_value(entity.kind, rules=rules)
        result.setdefault(entity.room, []).append(
            MapMarker(
                x=entity.x,
                y=entity.y,
                kind=entity.kind,
                sprite=entity.sprite,
                key=_marker_key(entity),
                entity_name=entity.name,
                attrs=entity.attrs,
                summary_kind=summary_kind,
                summary_stat=summary_stat,
                summary_label=summary_label,
                summary_value=summary_value,
            )
        )
    return {room: tuple(markers) for room, markers in result.items()}


def _classified_entities(entities: MapEntities) -> tuple[ClassifiedEntity, ...]:
    """Return every configured entity with a map position, not only statistics inputs."""
    return entities.entities


def _marker_key(entity: ClassifiedEntity) -> str:
    """Return the persisted per-instance key shared by previews and draft aggregation."""
    return f'{entity.room}\x1f{entity.name}\x1f{entity.entity_id}\x1f{entity.x}\x1f{entity.y}'


def _tile_rows(room: BinElement, name: str) -> tuple[str, ...]:
    layer = next((child for child in room.children if child.name == name), None)
    if layer is None:
        return ()
    text = layer.attrs.get('innerText')
    return tuple(text.splitlines()) if isinstance(text, str) else ()


def _str_attr(value: object) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: object) -> int | None:
    return value if type(value) is int else None


def _number_attr(value: object) -> int | float | None:
    if type(value) is int:
        return value
    if type(value) is float:
        return value
    return None
