"""Read map room layouts and Ender's Blender first-clear routes."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import yaml
from pydantic import Field, PositiveInt, ValidationError, model_validator

from pist.entities import classification, rules
from pist.entities.map_entity_id import MapEntityID
from pist.game import binmap
from pist.game.duration import Duration
from pist.game.mod_path import BadModPath, ModPath
from pist.game.mods import InstalledMod, LocalMap
from pist.game.saves import SAVE_EXT, SAVES_DIRNAME, sid_for_map_file
from pist.map_entrances import (
    DEFAULT_MAP_ENTRANCE_RULES,
    MapEntranceRules,
    MapEntranceSource,
)
from pist.models import ExternalModel, FrozenModel
from pist.types import NonNegativeDecimalInt

ENDERS_BLENDER_MOD_NAME = 'EndersBlender'
ENDERS_BLENDER_ROOM_ORDER_KEY = 'mapDict_roomStat_firstClear_roomOrder'
ENDERS_BLENDER_ROOM_DEATH_KEY = 'mapDict_roomStat_firstClear_death'
ENDERS_BLENDER_ROOM_TIMER_KEY = 'mapDict_roomStat_firstClear_timer'
LAYER_ENTRANCE_SOURCES = MappingProxyType(
    {
        'entities': MapEntranceSource.ENTITY,
        'triggers': MapEntranceSource.TRIGGER,
    }
)
ENDERS_BLENDER_FIELD_LABELS = MappingProxyType(
    {
        ENDERS_BLENDER_ROOM_ORDER_KEY: 'room orders',
        ENDERS_BLENDER_ROOM_DEATH_KEY: 'room deaths',
        ENDERS_BLENDER_ROOM_TIMER_KEY: 'room timers',
    }
)


@dataclass(frozen=True, slots=True)
class MapPreviewEntity:
    """One classified entity projected into a map-preview room."""

    x: binmap.NumericAttrValue
    y: binmap.NumericAttrValue
    kind: str
    sprite: str | None = None
    key: str | None = None
    entity_name: str | None = None
    attrs: Mapping[str, binmap.AttrValue] | None = None
    summary_kind: str | None = None
    summary_stat: rules.EntityStat | None = None
    summary_label: str | None = None
    summary_value: str | None = None


@dataclass(frozen=True, slots=True)
class MapRespawn:
    """One editor-defined player respawn point within a map room."""

    x: int | float
    y: int | float


@dataclass(frozen=True, slots=True)
class MapEntrance:
    """One explicit in-game map transition from a room to another map SID."""

    room: str
    target_sid: str
    x: binmap.NumericAttrValue | None = None
    y: binmap.NumericAttrValue | None = None
    width: binmap.NumericAttrValue | None = None
    height: binmap.NumericAttrValue | None = None


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
    entities: tuple[MapPreviewEntity, ...] = ()
    respawns: tuple[MapRespawn, ...] = ()

    @property
    def has_bounds(self) -> bool:
        """Return whether this room has all four editor-space bounds."""
        return None not in (self.x, self.y, self.width, self.height)


@dataclass(frozen=True, slots=True)
class MapLayout:
    """The rooms belonging to one decoded map."""

    rooms: tuple[MapRoom, ...]
    entrances: tuple[MapEntrance, ...] = ()

    @property
    def room_names(self) -> frozenset[str]:
        """Return all room names available for route selection."""
        return frozenset(room.name for room in self.rooms)


@dataclass(frozen=True, slots=True)
class MapEntityRecordReview:
    """One decoded map's corrected summaries and saved-pickup rule issues."""

    stats: classification.MapEntityStats
    collected_issues: tuple[classification.CollectedEntityRuleIssue, ...]


@dataclass(frozen=True, slots=True)
class MapEntityRecordSource:
    """One decoded map and the stable inputs for repeated record-rule reviews."""

    map_data: binmap.BinMap
    collected: frozenset[MapEntityID]
    excluded_entities: frozenset[str] = frozenset()

    def review(
        self,
        *,
        entity_rules: rules.EntityRules | None = None,
        needs_variant_review: classification.VariantReview | None = None,
        variant_review_loader: classification.VariantReviewLoader | None = None,
    ) -> MapEntityRecordReview:
        """Reclassify this map using the currently loaded entity rules."""
        if needs_variant_review is not None and variant_review_loader is not None:
            raise ValueError('Specify either a variant review checker or loader, not both.')
        rule_set = rules.load_entity_rule_layers() if entity_rules is None else entity_rules
        return MapEntityRecordReview(
            stats=classification.map_entity_stats(
                self.map_data,
                excluded_entities=self.excluded_entities,
                rule_set=rule_set,
            ),
            collected_issues=classification.collected_entity_rule_issues(
                self.map_data,
                self.collected,
                rule_set=rule_set,
                needs_variant_review=needs_variant_review,
                variant_review_loader=variant_review_loader,
            ),
        )


class MapRoute(FrozenModel):
    """The user-confirmed main rooms for one concrete map file."""

    map_file: str
    rooms: tuple[str, ...]
    room_counts: dict[str, PositiveInt] = Field(default_factory=dict)
    excluded_entities: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode='after')
    def validate_rooms(self) -> MapRoute:
        if len(set(self.rooms)) != len(self.rooms):
            raise ValueError('Map route must not contain duplicate rooms.')
        if not self.room_counts.keys() <= set(self.rooms):
            raise ValueError('Map route room counts must refer to selected rooms.')
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
    first_clear_room_times: dict[str, dict[str, Duration]] = field(default_factory=dict)

    def first_clear_room_order(self, map_info: LocalMap) -> tuple[str, ...]:
        """Return the recorded first-clear sequence for one map, if available."""
        return self.first_clear_room_orders.get(_map_save_key(map_info), ())

    def first_clear_room_time(self, map_info: LocalMap, room: str) -> Duration | None:
        """Return one room's recorded first-clear time, if available."""
        return self.first_clear_room_times.get(_map_save_key(map_info), {}).get(room)

    def first_clear_room_death(self, map_info: LocalMap, room: str) -> int | None:
        """Return one room's recorded first-clear death count, if available."""
        return self.first_clear_room_deaths.get(_map_save_key(map_info), {}).get(room)


class _EndersBlenderData(ExternalModel):
    """The subset of Ender's Blender YAML that Pist consumes.

    ``yaml.BaseLoader`` deliberately preserves scalar values as strings. The
    nested field annotations validate their decimal encoding and convert them.
    """

    room_orders: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        validation_alias=ENDERS_BLENDER_ROOM_ORDER_KEY,
    )
    room_deaths: dict[str, dict[str, NonNegativeDecimalInt]] = Field(
        default_factory=dict,
        validation_alias=ENDERS_BLENDER_ROOM_DEATH_KEY,
    )
    room_timers: dict[str, dict[str, Duration]] = Field(
        default_factory=dict,
        validation_alias=ENDERS_BLENDER_ROOM_TIMER_KEY,
    )


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
            saved_data = _EndersBlenderData.model_validate(data)
        except ValidationError as error:
            raise _enders_blender_validation_error(path, error) from error
        return EndersBlenderSave(
            number,
            saved_data.room_orders,
            saved_data.room_deaths,
            saved_data.room_timers,
        )

    def _path(self, number: int) -> Path:
        return self._saves_dir / f'{number}-modsave-{ENDERS_BLENDER_MOD_NAME}{SAVE_EXT}'


def map_layout(
    map_data: binmap.BinMap,
    *,
    entrance_rules: MapEntranceRules = DEFAULT_MAP_ENTRANCE_RULES,
    entity_rules: rules.EntityRules | None = None,
) -> MapLayout:
    """Extract all map rooms and their editor-space bounds from a decoded map."""
    levels = next((child for child in map_data.root.children if child.name == 'levels'), None)
    if levels is None:
        return MapLayout(())
    rule_set = rules.load_entity_rule_layers() if entity_rules is None else entity_rules
    entities = classification.classify_map_entities(map_data, rule_set=rule_set)
    preview_entities_by_room = _preview_entities_by_room(entities, rule_set)
    rooms = tuple(
        _map_room(room, preview_entities_by_room.get(_str_attr(room.attrs.get('name')), ()))
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


def load_map_entity_record_source_from_path(
    path: Path,
    map_file: str,
    collected: frozenset[MapEntityID],
    *,
    excluded_entities: frozenset[str] = frozenset(),
) -> MapEntityRecordSource:
    """Read one map once, retaining data needed for later rule refreshes."""
    return MapEntityRecordSource(
        _load_map_bin(path, map_file),
        collected,
        excluded_entities,
    )


def _load_map_bin(path: Path, map_file: str) -> binmap.BinMap:
    """Read one map BIN from an archive, directory, or the Game Content directory."""
    try:
        with ModPath(path) as mod_path:
            map_path = mod_path.joinpath(map_file)
            if not map_path.is_file():
                raise ValueError(f'Map file does not exist in {path!r}: {map_file!r}')
            map_data = binmap.parse_map_bin(map_path.read_bytes(), allow_trailing=True)
    except BadModPath as error:
        raise ValueError(f'Invalid map package path: {path!r}') from error
    return map_data


def _enders_blender_validation_error(path: Path, error: ValidationError) -> ValueError:
    detail = error.errors(include_url=False)[0]
    location = detail['loc']
    field = location[0] if location else None
    label = (
        ENDERS_BLENDER_FIELD_LABELS.get(field, 'save data')
        if isinstance(field, str)
        else 'save data'
    )
    nested_path = ''.join(f'[{part!r}]' for part in location[1:])
    suffix = f' at {nested_path}' if nested_path else ''
    return ValueError(f'Invalid Ender’s Blender {label}{suffix}: {detail["msg"]} in {path!r}')


def _map_save_key(map_info: LocalMap) -> str:
    sid = sid_for_map_file(map_info.file_path)
    return f'{sid}_{map_info.side}' if map_info.side is not None else sid


def _map_room(room: binmap.BinElement, entities: tuple[MapPreviewEntity, ...]) -> MapRoom:
    return MapRoom(
        name=_str_attr(room.attrs.get('name')),
        x=_int_attr(room.attrs.get('x')),
        y=_int_attr(room.attrs.get('y')),
        width=_int_attr(room.attrs.get('width')),
        height=_int_attr(room.attrs.get('height')),
        background=_tile_rows(room, 'bg'),
        solids=_tile_rows(room, 'solids'),
        entities=entities,
        respawns=_room_respawns(room),
    )


def _room_respawns(room: binmap.BinElement) -> tuple[MapRespawn, ...]:
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


def _map_entrances(
    levels: binmap.BinElement, entrance_rules: MapEntranceRules
) -> tuple[MapEntrance, ...]:
    """Extract configured static map entrances from room entities and triggers."""
    result: list[MapEntrance] = []
    for room in levels.children:
        if room.name != 'level':
            continue
        room_name = _str_attr(room.attrs.get('name'))
        for layer in room.children:
            source = _entrance_source(layer.name)
            if source is None:
                continue
            for item in layer.children:
                rule = entrance_rules.match(source, item.name, item.attrs)
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
                result.append(MapEntrance(room_name, target_sid, x, y, width, height))
    return tuple(result)


def _entrance_source(layer_name: str) -> MapEntranceSource | None:
    return LAYER_ENTRANCE_SOURCES.get(layer_name)


def _preview_entities_by_room(
    entities: Iterable[classification.ClassifiedEntity], rule_set: rules.EntityRules
) -> dict[str, tuple[MapPreviewEntity, ...]]:
    result: dict[str, list[MapPreviewEntity]] = {}
    for entity in entities:
        if entity.x is None or entity.y is None:
            continue
        owner = rules.stat_owner(entity.kind, rules=rule_set)
        if owner is None:
            summary_kind = None
            summary_stat = None
            summary_label = None
            summary_value = None
        else:
            summary_kind, summary_stat = owner
            summary_label = rule_set.kinds[summary_kind].label
            summary_value = rules.kind_select_value(entity.kind, rules=rule_set)
        result.setdefault(entity.room, []).append(
            MapPreviewEntity(
                x=entity.x,
                y=entity.y,
                kind=entity.kind,
                sprite=entity.sprite,
                key=classification.entity_key(entity),
                entity_name=entity.name,
                attrs=entity.attrs,
                summary_kind=summary_kind,
                summary_stat=summary_stat,
                summary_label=summary_label,
                summary_value=summary_value,
            )
        )
    return {room: tuple(entities) for room, entities in result.items()}


def _tile_rows(room: binmap.BinElement, name: str) -> tuple[str, ...]:
    layer = next((child for child in room.children if child.name == name), None)
    if layer is None:
        return ()
    text = layer.attrs.get('innerText')
    return tuple(text.splitlines()) if isinstance(text, str) else ()


def _str_attr(value: object) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: object) -> int | None:
    return value if type(value) is int else None


def _number_attr(value: object) -> binmap.NumericAttrValue | None:
    if type(value) is int:
        return value
    if type(value) is float:
        return value
    return None
