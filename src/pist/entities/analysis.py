"""Classify map entities with configured entity rules."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from pist.entities.rules import (
    DEFAULT_ENTITY_RULES,
    EntityRules,
    EntityStat,
    EntityTableField,
    entity_kind,
    kind_is_a,
    kind_select_value,
    kind_sprite,
    stat_owner,
)
from pist.game.binmap import AttrValue, BinElement, BinMap, NumericAttrValue, parse_map_bin
from pist.types import RecordValues


class HeartStatus(StrEnum):
    """How a map's configured crystal-heart entities end the level."""

    NONE = 'none'
    COMPLETES_LEVEL = 'completes_level'
    EXTRA = 'extra'
    NEEDS_REVIEW = 'needs_review'


@dataclass(frozen=True, slots=True)
class ClassifiedEntity:
    """One classified map entity, with enough data to trace its source."""

    room: str
    name: str
    entity_id: int | None
    x: NumericAttrValue | None
    y: NumericAttrValue | None
    attrs: dict[str, AttrValue]
    kind: str
    sprite: str | None


@dataclass(frozen=True, slots=True)
class SelectConflict:
    """Distinct select values competing for one record field."""

    kind: str
    table_field: EntityTableField
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class MapEntityStats:
    """Configured count and existence summaries, grouped by their owning kind."""

    counted: dict[str, tuple[ClassifiedEntity, ...]]
    existing: dict[str, tuple[ClassifiedEntity, ...]]
    selected: dict[str, tuple[ClassifiedEntity, ...]] = field(default_factory=dict)
    table_fields: dict[str, EntityTableField] = field(default_factory=dict)
    stat_types: dict[str, EntityStat] = field(default_factory=dict)
    select_values: dict[str, frozenset[str]] = field(default_factory=dict)

    def count(self, kind: str) -> int:
        """Return the number of counted entities for one configured kind."""
        return len(self.counted.get(kind, ()))

    def exists(self, kind: str) -> bool:
        """Return whether at least one configured entity exists for one configured kind."""
        return bool(self.existing.get(kind, ()))

    @property
    def select_conflicts(self) -> tuple[SelectConflict, ...]:
        """Return select-stat fields whose map entities disagree on one output value."""
        return tuple(
            SelectConflict(kind, target, values)
            for kind, values in sorted(self.select_values.items())
            if len(values) > 1 and (target := self.table_fields.get(kind)) is not None
        )

    @property
    def record_values(self) -> RecordValues:
        """Return configured output values as ``table -> field -> scalar``."""
        values: RecordValues = {}
        for kind, target in self.table_fields.items():
            match self.stat_types.get(kind):
                case EntityStat.COUNT:
                    values.setdefault(target.table, {})[target.field] = self.count(kind)
                case EntityStat.EXIST:
                    values.setdefault(target.table, {})[target.field] = self.exists(kind)
                case EntityStat.SELECT:
                    select_values = self.select_values.get(kind, frozenset())
                    if len(select_values) == 1:
                        values.setdefault(target.table, {})[target.field] = next(
                            iter(select_values)
                        )
                case EntityStat.NONE:
                    pass
        return values


@dataclass(frozen=True, slots=True)
class MapEntities:
    """Classified entities extracted from one decoded map."""

    entities: tuple[ClassifiedEntity, ...]
    stats: MapEntityStats
    special: dict[str, tuple[ClassifiedEntity, ...]]
    heart_status: HeartStatus

    @property
    def strawberries(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities counted as regular strawberries."""
        return self.stats.counted.get('strawberry', ())

    @property
    def moonberries(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities counted as moonberries or their descendants."""
        return self.stats.counted.get('moonberry', ())

    @property
    def cassettes(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities contributing to cassette existence."""
        return self.stats.existing.get('cassette', ())

    @property
    def hearts(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities contributing to the configured heart summary."""
        return self.stats.selected.get('heart', self.stats.existing.get('heart', ()))

    @property
    def has_cassette(self) -> bool:
        """Return whether this map contains at least one configured cassette entity."""
        return bool(self.cassettes)


def analyze_map_entities(
    map_data: BinMap,
    *,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> MapEntities:
    """Classify configured entities in all rooms of a decoded map."""
    metadata = map_mode_metadata(map_data.root)
    entities: list[ClassifiedEntity] = []
    counted: dict[str, list[ClassifiedEntity]] = {}
    existing: dict[str, list[ClassifiedEntity]] = {}
    selected: dict[str, list[ClassifiedEntity]] = {}
    select_values: dict[str, set[str]] = {}
    table_fields = {
        name: kind.table_field for name, kind in rules.kinds.items() if kind.table_field is not None
    }
    stat_types = {
        name: kind.stat for name, kind in rules.kinds.items() if kind.table_field is not None
    }
    special: dict[str, list[ClassifiedEntity]] = {}
    for room in _map_rooms(map_data.root):
        for entity in _room_entities(room):
            kind = entity_kind(entity.name, entity.attrs, meta=metadata, rules=rules)
            if kind is None:
                continue
            occurrence = _classified_entity(room, entity, kind, kind_sprite(kind, rules=rules))
            entities.append(occurrence)
            match stat_owner(kind, rules=rules):
                case stat_kind, EntityStat.COUNT, _:
                    counted.setdefault(stat_kind, []).append(occurrence)
                case stat_kind, EntityStat.EXIST, _:
                    existing.setdefault(stat_kind, []).append(occurrence)
                case stat_kind, EntityStat.SELECT, _:
                    selected.setdefault(stat_kind, []).append(occurrence)
                    if (value := kind_select_value(kind, rules=rules)) is not None:
                        select_values.setdefault(stat_kind, set()).add(value)
            if kind_is_a(kind, 'special', rules=rules):
                special.setdefault(kind, []).append(occurrence)
    return MapEntities(
        entities=tuple(entities),
        stats=MapEntityStats(
            counted={kind: tuple(values) for kind, values in counted.items()},
            existing={kind: tuple(values) for kind, values in existing.items()},
            selected={kind: tuple(values) for kind, values in selected.items()},
            table_fields=table_fields,
            stat_types=stat_types,
            select_values={kind: frozenset(values) for kind, values in select_values.items()},
        ),
        special={kind: tuple(entities) for kind, entities in special.items()},
        heart_status=_heart_status(selected.get('heart', existing.get('heart', []))),
    )


def analyze_map_bin(
    data: bytes,
    *,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> MapEntities:
    """Decode and classify configured entities in one map BIN file."""
    return analyze_map_entities(parse_map_bin(data), rules=rules)


def map_mode_metadata(root: BinElement) -> Mapping[str, AttrValue]:
    """Return the active mode metadata used by Game entity behavior."""
    metadata = next((child for child in root.children if child.name == 'meta'), None)
    if metadata is None:
        return {}
    mode = next((child for child in metadata.children if child.name == 'mode'), None)
    return {} if mode is None else mode.attrs


def _map_rooms(root: BinElement) -> tuple[BinElement, ...]:
    levels = next((child for child in root.children if child.name == 'levels'), None)
    return (
        () if levels is None else tuple(child for child in levels.children if child.name == 'level')
    )


def _room_entities(room: BinElement) -> tuple[BinElement, ...]:
    entities = next((child for child in room.children if child.name == 'entities'), None)
    return () if entities is None else entities.children


def _classified_entity(
    room: BinElement, entity: BinElement, kind: str, sprite: str | None
) -> ClassifiedEntity:
    entity_id = entity.attrs.get('id')
    x = entity.attrs.get('x')
    y = entity.attrs.get('y')
    return ClassifiedEntity(
        room=_str_attr(room.attrs.get('name')),
        name=entity.name,
        entity_id=_int_attr(entity_id),
        x=_number_attr(x),
        y=_number_attr(y),
        attrs=entity.attrs.copy(),
        kind=kind,
        sprite=sprite,
    )


def _heart_status(hearts: list[ClassifiedEntity]) -> HeartStatus:
    if not hearts:
        return HeartStatus.NONE
    if all(heart.kind == 'end_level_heart' for heart in hearts):
        return HeartStatus.COMPLETES_LEVEL
    if all(heart.kind == 'keep_going_heart' for heart in hearts):
        return HeartStatus.EXTRA
    return HeartStatus.NEEDS_REVIEW


def _str_attr(value: AttrValue | None) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: AttrValue | None) -> int | None:
    return cast(int, value) if type(value) is int else None


def _number_attr(value: AttrValue | None) -> NumericAttrValue | None:
    return cast(NumericAttrValue, value) if type(value) in {int, float} else None
