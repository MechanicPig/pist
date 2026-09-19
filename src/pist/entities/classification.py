"""Classify map entities and aggregate configured statistics."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from pist.entities import rules
from pist.entities.map_entity_id import MapEntityID
from pist.game.binmap import AttrValue, BinElement, BinMap, NumericAttrValue
from pist.types import RecordValues

type VariantReview = Callable[[str, Mapping[str, AttrValue], Mapping[str, AttrValue]], bool]
type VariantReviewLoader = Callable[[frozenset[str]], VariantReview | None]


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
class _MatchedMapEntity:
    """One rule-matched map element before projection into the preview model."""

    room: str
    element: BinElement
    kind: str


class CollectedEntityRuleIssueStatus(StrEnum):
    """How a collected native entity ID relates to the active rule library."""

    UNMATCHED = 'unmatched'
    EXCLUDED = 'excluded'
    UNREVIEWED_VARIANT = 'unreviewed_variant'
    NOT_FOUND = 'not_found'


@dataclass(frozen=True, slots=True)
class CollectedEntityRuleIssue:
    """A saved strawberry instance that needs rule review or map-version attention."""

    collected_id: MapEntityID
    status: CollectedEntityRuleIssueStatus
    entity_name: str | None = None
    attrs: dict[str, AttrValue] | None = None
    meta: dict[str, AttrValue] | None = None


@dataclass(frozen=True, slots=True)
class SelectConflict:
    """Distinct select values competing for one record field."""

    kind: str
    table_field: rules.EntityTableField
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class MapEntityStats:
    """Configured count and existence summaries, grouped by their owning kind."""

    counts: dict[str, int] = field(default_factory=dict)
    existing_kinds: frozenset[str] = field(default_factory=frozenset)
    selected_kinds: frozenset[str] = field(default_factory=frozenset)
    table_fields: dict[str, rules.EntityTableField] = field(default_factory=dict)
    stat_types: dict[str, rules.EntityStat] = field(default_factory=dict)
    select_values: dict[str, frozenset[str]] = field(default_factory=dict)

    def count(self, kind: str) -> int:
        """Return the number of counted entities for one configured kind."""
        return self.counts.get(kind, 0)

    def exists(self, kind: str) -> bool:
        """Return whether at least one configured entity exists for one configured kind."""
        return kind in self.existing_kinds

    def has_stat_kind(self, kind: str) -> bool:
        """Return whether a configured statistic has at least one entity of this kind."""
        return kind in self.counts or kind in self.existing_kinds or kind in self.selected_kinds

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
                case rules.EntityStat.COUNT:
                    values.setdefault(target.table, {})[target.field] = self.count(kind)
                case rules.EntityStat.EXIST:
                    values.setdefault(target.table, {})[target.field] = self.exists(kind)
                case rules.EntityStat.SELECT:
                    select_values = self.select_values.get(kind, frozenset())
                    if len(select_values) == 1:
                        values.setdefault(target.table, {})[target.field] = next(
                            iter(select_values)
                        )
                case rules.EntityStat.NONE:
                    pass
        return values


def classify_map_entities(
    map_data: BinMap,
    *,
    rule_set: rules.EntityRules,
) -> Iterator[ClassifiedEntity]:
    """Yield configured entities in all rooms of a decoded map."""
    for matched in _matched_map_entities(map_data, rule_set=rule_set):
        yield _classified_entity(
            matched.room,
            matched.element,
            matched.kind,
            rules.kind_sprite(matched.kind, rules=rule_set),
        )


def map_entity_stats(
    map_data: BinMap,
    *,
    excluded_entities: frozenset[str] = frozenset(),
    rule_set: rules.EntityRules,
) -> MapEntityStats:
    """Summarize configured entities after applying per-instance exclusions."""
    counts: dict[str, int] = {}
    existing_kinds: set[str] = set()
    selected_kinds: set[str] = set()
    select_values: dict[str, set[str]] = {}
    for matched in _matched_map_entities(map_data, rule_set=rule_set):
        entity_id = _int_attr(matched.element.attrs.get('id'))
        key = None if entity_id is None else str(MapEntityID(matched.room, entity_id))
        if key in excluded_entities:
            continue
        match rules.stat_owner(matched.kind, rules=rule_set):
            case stat_kind, rules.EntityStat.COUNT:
                counts[stat_kind] = counts.get(stat_kind, 0) + 1
            case stat_kind, rules.EntityStat.EXIST:
                existing_kinds.add(stat_kind)
            case stat_kind, rules.EntityStat.SELECT:
                selected_kinds.add(stat_kind)
                if (value := rules.kind_select_value(matched.kind, rules=rule_set)) is not None:
                    select_values.setdefault(stat_kind, set()).add(value)
    return MapEntityStats(
        counts=counts,
        existing_kinds=frozenset(existing_kinds),
        selected_kinds=frozenset(selected_kinds),
        table_fields={
            name: kind.table_field
            for name, kind in rule_set.kinds.items()
            if kind.table_field is not None
        },
        stat_types={
            name: kind.stat for name, kind in rule_set.kinds.items() if kind.table_field is not None
        },
        select_values={kind: frozenset(values) for kind, values in select_values.items()},
    )


def collected_entity_rule_issues(
    map_data: BinMap,
    collected: frozenset[MapEntityID],
    *,
    rule_set: rules.EntityRules,
    needs_variant_review: VariantReview | None = None,
    variant_review_loader: VariantReviewLoader | None = None,
) -> tuple[CollectedEntityRuleIssue, ...]:
    """Find saved strawberry IDs lacking a rule or contradicting an exclusion.

    Native saves only identify strawberry-style pickups as ``room:id``.  A missing
    map entity usually means the map was updated since the save was written, while
    a matched ``kind = null`` rule contradicts the game's collection record.
    """
    if needs_variant_review is not None and variant_review_loader is not None:
        raise ValueError('Specify either a variant review checker or loader, not both.')
    by_id: dict[MapEntityID, BinElement] = {}
    for room in _map_rooms(map_data.root):
        room_name = _str_attr(room.attrs.get('name'))
        for entity in _room_entities(room):
            entity_id = _int_attr(entity.attrs.get('id'))
            if entity_id is None:
                continue
            collected_id = MapEntityID(room_name, entity_id)
            if collected_id in collected:
                by_id[collected_id] = entity
    if by_id and variant_review_loader is not None:
        needs_variant_review = variant_review_loader(
            frozenset(entity.name for entity in by_id.values())
        )
    metadata = map_mode_metadata(map_data.root)
    issues: list[CollectedEntityRuleIssue] = []
    for collected_id in sorted(collected, key=lambda item: (item.room, item.entity_id)):
        entity = by_id.get(collected_id)
        if entity is None:
            issues.append(
                CollectedEntityRuleIssue(collected_id, CollectedEntityRuleIssueStatus.NOT_FOUND)
            )
            continue
        if needs_variant_review is not None and needs_variant_review(
            entity.name, entity.attrs, metadata
        ):
            issues.append(
                CollectedEntityRuleIssue(
                    collected_id,
                    CollectedEntityRuleIssueStatus.UNREVIEWED_VARIANT,
                    entity.name,
                    entity.attrs.copy(),
                    dict(metadata) or None,
                )
            )
        elif (
            rule := rules.matching_entity_rule(
                entity.name, entity.attrs, meta=metadata, rules=rule_set
            )
        ) is None:
            issues.append(
                CollectedEntityRuleIssue(
                    collected_id,
                    CollectedEntityRuleIssueStatus.UNMATCHED,
                    entity.name,
                    entity.attrs.copy(),
                )
            )
        elif rule.kind is None:
            issues.append(
                CollectedEntityRuleIssue(
                    collected_id,
                    CollectedEntityRuleIssueStatus.EXCLUDED,
                    entity.name,
                    entity.attrs.copy(),
                )
            )
    return tuple(issues)


def map_mode_metadata(root: BinElement) -> Mapping[str, AttrValue]:
    """Return the active mode metadata used by Game entity behavior."""
    metadata = next((child for child in root.children if child.name == 'meta'), None)
    if metadata is None:
        return {}
    mode = next((child for child in metadata.children if child.name == 'mode'), None)
    return {} if mode is None else mode.attrs


def _matched_map_entities(
    map_data: BinMap, *, rule_set: rules.EntityRules
) -> Iterator[_MatchedMapEntity]:
    """Yield rule-matched map elements without allocating preview-only data."""
    metadata = map_mode_metadata(map_data.root)
    for room in _map_rooms(map_data.root):
        room_name = _str_attr(room.attrs.get('name'))
        for entity in _room_entities(room):
            kind = rules.entity_kind(entity.name, entity.attrs, meta=metadata, rules=rule_set)
            if kind is not None:
                yield _MatchedMapEntity(room_name, entity, kind)


def _map_rooms(root: BinElement) -> tuple[BinElement, ...]:
    levels = next((child for child in root.children if child.name == 'levels'), None)
    return (
        () if levels is None else tuple(child for child in levels.children if child.name == 'level')
    )


def _room_entities(room: BinElement) -> tuple[BinElement, ...]:
    entities = next((child for child in room.children if child.name == 'entities'), None)
    return () if entities is None else entities.children


def _classified_entity(
    room: str, entity: BinElement, kind: str, sprite: str | None
) -> ClassifiedEntity:
    entity_id = entity.attrs.get('id')
    x = entity.attrs.get('x')
    y = entity.attrs.get('y')
    return ClassifiedEntity(
        room=room,
        name=entity.name,
        entity_id=_int_attr(entity_id),
        x=_number_attr(x),
        y=_number_attr(y),
        attrs=entity.attrs.copy(),
        kind=kind,
        sprite=sprite,
    )


def entity_key(entity: ClassifiedEntity) -> str | None:
    """Return the Game-compatible key for a preview entity with an editor entity ID."""
    if entity.entity_id is None:
        return None
    return str(MapEntityID(entity.room, entity.entity_id))


def _str_attr(value: AttrValue | None) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: AttrValue | None) -> int | None:
    return cast(int, value) if type(value) is int else None


def _number_attr(value: AttrValue | None) -> NumericAttrValue | None:
    return cast(NumericAttrValue, value) if type(value) in {int, float} else None
