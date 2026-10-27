"""Classify map entities and explain their matching rules."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from berries.game.binmap import AttrValue, BinElement, BinMap, NumericAttrValue
from berries.map_entity_id import MapEntityID

from . import rules


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

    @property
    def key(self) -> str | None:
        """Return the Game-compatible key when this entity has an editor ID."""
        if self.entity_id is None:
            return None
        return str(MapEntityID(self.room, self.entity_id))


class EntityRuleStatus(StrEnum):
    """How an entity relates to the configured classification rules."""

    MATCHED = 'matched'
    EXCLUDED = 'excluded'
    UNMATCHED = 'unmatched'


@dataclass(frozen=True, slots=True)
class MapEntityExplanation:
    """One room entity and its exact matching rule, if any.

    The element and rule are borrowed from the supplied map and rule set; this
    result does not copy or freeze their nested attributes.
    """

    room: str
    element: BinElement
    rule: rules.EntityRule | None

    @property
    def status(self) -> EntityRuleStatus:
        """Distinguish an explicit exclusion from an absent matching rule."""
        if self.rule is None:
            return EntityRuleStatus.UNMATCHED
        return EntityRuleStatus.EXCLUDED if self.rule.kind is None else EntityRuleStatus.MATCHED


class CollectedEntityRuleIssueStatus(StrEnum):
    """A discrepancy between saved pickup IDs and the current map's rules."""

    UNMATCHED = 'unmatched'
    EXCLUDED = 'excluded'
    NOT_FOUND = 'not_found'


@dataclass(frozen=True, slots=True)
class CollectedEntityRuleIssue:
    """One missing pickup or a pickup not supported by the active rules."""

    collected_id: MapEntityID
    status: CollectedEntityRuleIssueStatus
    entity_name: str | None = None
    attrs: dict[str, AttrValue] | None = None
    meta: dict[str, AttrValue] | None = None


@dataclass(frozen=True, slots=True)
class CollectedEntityRuleCheck:
    """A saved pickup's current entity and matching rule, including successful matches.

    Entity, rule and metadata references are borrowed from the supplied inputs.
    Applications can inspect these facts without rescanning the map or supplying
    application-specific review callbacks to the classification layer.
    """

    collected_id: MapEntityID
    entity: BinElement | None
    rule: rules.EntityRule | None
    meta: Mapping[str, AttrValue]

    @property
    def issue(self) -> CollectedEntityRuleIssue | None:
        """Return a detached discrepancy, or None for a classified pickup."""
        if self.entity is None:
            return CollectedEntityRuleIssue(
                self.collected_id, CollectedEntityRuleIssueStatus.NOT_FOUND
            )
        if self.rule is not None and self.rule.kind is not None:
            return None
        status = (
            CollectedEntityRuleIssueStatus.UNMATCHED
            if self.rule is None
            else CollectedEntityRuleIssueStatus.EXCLUDED
        )
        return CollectedEntityRuleIssue(
            self.collected_id,
            status,
            self.entity.name,
            self.entity.attrs.copy(),
            dict(self.meta) or None,
        )


def check_collected_entity_rules(
    map_data: BinMap,
    collected: frozenset[MapEntityID],
    *,
    rule_set: rules.EntityRules,
) -> tuple[CollectedEntityRuleCheck, ...]:
    """Resolve saved pickup IDs and their rules in stable room/ID order.

    Include successful matches and missing entities. Only selected room entities
    are checked; no human-review state is consulted. Duplicate map IDs retain the
    last occurrence, as in the existing lookup; uniqueness checks are separate.
    """
    by_id: dict[MapEntityID, BinElement] = {}
    for room, entity in _map_entities(map_data):
        entity_id = _int_attr(entity.attrs.get('id'))
        if entity_id is not None and (key := MapEntityID(room, entity_id)) in collected:
            by_id[key] = entity
    metadata = map_mode_metadata(map_data.root)
    return tuple(
        CollectedEntityRuleCheck(
            collected_id,
            entity := by_id.get(collected_id),
            None
            if entity is None
            else rule_set.matching_entity_rule(entity.name, entity.attrs, meta=metadata),
            metadata,
        )
        for collected_id in sorted(collected, key=lambda item: (item.room, item.entity_id))
    )


def collected_entity_rule_issues(
    map_data: BinMap,
    collected: frozenset[MapEntityID],
    *,
    rule_set: rules.EntityRules,
) -> tuple[CollectedEntityRuleIssue, ...]:
    """Find saved pickup IDs that are missing, unmatched or explicitly excluded."""
    return tuple(
        issue
        for check in check_collected_entity_rules(map_data, collected, rule_set=rule_set)
        if (issue := check.issue) is not None
    )


def classify_map_entities(
    map_data: BinMap,
    *,
    rule_set: rules.EntityRules,
) -> Iterator[ClassifiedEntity]:
    """Yield configured entities in all rooms of a decoded map."""
    metadata = map_mode_metadata(map_data.root)
    for room, entity in _map_entities(map_data):
        kind = rule_set.entity_kind(entity.name, entity.attrs, meta=metadata)
        if kind is not None:
            yield _classified_entity(room, entity, kind, rule_set.kind_sprite(kind))


def explain_map_entities(
    map_data: BinMap,
    *,
    rule_set: rules.EntityRules,
) -> Iterator[MapEntityExplanation]:
    """Explain every room entity in file order without guessing unknown kinds.

    Includes unmatched and explicitly excluded entities, including those without
    editor IDs. Only room entities are classified; triggers remain available
    through the raw BinMap tree. Map identity is owned by the caller.
    """
    metadata = map_mode_metadata(map_data.root)
    for room, entity in _map_entities(map_data):
        yield MapEntityExplanation(
            room,
            entity,
            rule_set.matching_entity_rule(entity.name, entity.attrs, meta=metadata),
        )


def map_mode_metadata(root: BinElement) -> dict[str, AttrValue]:
    """Return the active mode metadata used by Game entity behavior."""
    metadata = root.child('meta')
    if metadata is None:
        return {}
    mode = metadata.child('mode')
    return {} if mode is None else mode.attrs


def _map_entities(map_data: BinMap) -> Iterator[tuple[str, BinElement]]:
    """Yield room entities in their original room and sibling order."""
    for room in map_data.iter_rooms():
        room_name = _str_attr(room.attrs.get('name'))
        for entity in _room_entities(room):
            yield room_name, entity


def _room_entities(room: BinElement) -> tuple[BinElement, ...]:
    entities = room.child('entities')
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


def _str_attr(value: AttrValue | None) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: AttrValue | None) -> int | None:
    return cast(int, value) if type(value) is int else None


def _number_attr(value: AttrValue | None) -> NumericAttrValue | None:
    return cast(NumericAttrValue, value) if type(value) in {int, float} else None
