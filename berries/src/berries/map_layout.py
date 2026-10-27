"""Interpret decoded maps as reusable preview layouts."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import Self

from .entities import classification, config, rules
from .game import map_data as source
from .game.binmap import AttrValue, BinElement, BinMap, NumericAttrValue
from .game.maps import Map
from .map_entrances import (
    MapEntranceRules,
    MapEntranceSource,
    load_default_map_entrance_rules,
)

type _StrPath = str | PathLike[str]

LAYER_ENTRANCE_SOURCES = MappingProxyType(
    {
        'entities': MapEntranceSource.ENTITY,
        'triggers': MapEntranceSource.TRIGGER,
    }
)


@dataclass(frozen=True, slots=True)
class MapPreviewEntity:
    """One classified entity projected into a map-preview room."""

    x: NumericAttrValue
    y: NumericAttrValue
    kind: str
    sprite: str | None = None
    key: str | None = None
    entity_name: str | None = None
    attrs: Mapping[str, AttrValue] | None = None


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
    x: NumericAttrValue | None = None
    y: NumericAttrValue | None = None
    width: NumericAttrValue | None = None
    height: NumericAttrValue | None = None
    source: MapEntranceSource | None = None
    element_name: str | None = None
    attrs: Mapping[str, AttrValue] | None = None


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

    @classmethod
    def from_bin_map(
        cls,
        map_data: BinMap,
        *,
        entrance_rules: MapEntranceRules | None = None,
        entity_rules: rules.EntityRules | None = None,
    ) -> Self:
        """Extract all map rooms and their editor-space bounds from a decoded map."""
        if map_data.root.child('levels') is None:
            return cls(())
        rule_set = config.load_default_entity_rules() if entity_rules is None else entity_rules
        entities = classification.classify_map_entities(map_data, rule_set=rule_set)
        preview_entities_by_room = _preview_entities_by_room(entities)
        rooms = tuple(
            _map_room(room, preview_entities_by_room.get(_str_attr(room.attrs.get('name')), ()))
            for room in map_data.iter_rooms()
        )
        entrance_rule_set = (
            load_default_map_entrance_rules() if entrance_rules is None else entrance_rules
        )
        return cls(rooms, _map_entrances(map_data.iter_rooms(), entrance_rule_set))

    @classmethod
    def from_map(
        cls,
        map_file: Map,
        *,
        entity_rules: rules.EntityRules | None = None,
        entrance_rules: MapEntranceRules | None = None,
    ) -> Self:
        """Read one active Game or Mod map through its typed content source."""
        return cls.from_bin_map(
            source.load_map_data(map_file), entity_rules=entity_rules, entrance_rules=entrance_rules
        )

    @classmethod
    def from_path(
        cls,
        path: Path,
        map_file: _StrPath,
        *,
        entity_rules: rules.EntityRules | None = None,
        entrance_rules: MapEntranceRules | None = None,
    ) -> Self:
        """Read one map layout from a Mod archive, Mod directory, or Game Content directory."""
        return cls.from_bin_map(
            source.load_map_data_from_path(path, map_file),
            entity_rules=entity_rules,
            entrance_rules=entrance_rules,
        )

    @property
    def room_names(self) -> frozenset[str]:
        """Return all room names available for route selection."""
        return frozenset(room.name for room in self.rooms)


def _map_room(room: BinElement, entities: tuple[MapPreviewEntity, ...]) -> MapRoom:
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


def _room_respawns(room: BinElement) -> tuple[MapRespawn, ...]:
    entities = room.child('entities')
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
    rooms: Iterable[BinElement], entrance_rules: MapEntranceRules
) -> tuple[MapEntrance, ...]:
    """Extract configured static map entrances from room entities and triggers."""
    result: list[MapEntrance] = []
    for room in rooms:
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
                result.append(
                    MapEntrance(
                        room_name,
                        target_sid,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        source=source,
                        element_name=item.name,
                        attrs=item.attrs,
                    )
                )
    return tuple(result)


def _entrance_source(layer_name: str) -> MapEntranceSource | None:
    return LAYER_ENTRANCE_SOURCES.get(layer_name)


def _preview_entities_by_room(
    entities: Iterable[classification.ClassifiedEntity],
) -> dict[str, tuple[MapPreviewEntity, ...]]:
    result: dict[str, list[MapPreviewEntity]] = {}
    for entity in entities:
        if entity.x is None or entity.y is None:
            continue
        result.setdefault(entity.room, []).append(
            MapPreviewEntity(
                x=entity.x,
                y=entity.y,
                kind=entity.kind,
                sprite=entity.sprite,
                key=entity.key,
                entity_name=entity.name,
                attrs=entity.attrs,
            )
        )
    return {room: tuple(entities) for room, entities in result.items()}


def _tile_rows(room: BinElement, name: str) -> tuple[str, ...]:
    layer = room.child(name)
    if layer is None:
        return ()
    text = layer.attrs.get('innerText')
    return tuple(text.splitlines()) if isinstance(text, str) else ()


def _str_attr(value: object) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: object) -> int | None:
    return value if type(value) is int else None


def _number_attr(value: object) -> NumericAttrValue | None:
    if type(value) is int:
        return value
    if type(value) is float:
        return value
    return None
