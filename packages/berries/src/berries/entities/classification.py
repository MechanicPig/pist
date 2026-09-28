"""Classify map entities and aggregate configured statistics."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from berries.entities import rules
from berries.game.binmap import AttrValue, BinElement, BinMap, NumericAttrValue
from berries.map_entity_id import MapEntityID

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

    @property
    def key(self) -> str | None:
        """Return the Game-compatible key when this entity has an editor ID."""
        if self.entity_id is None:
            return None
        return str(MapEntityID(self.room, self.entity_id))


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
            rule_set.kind_sprite(matched.kind),
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
            rule := rule_set.matching_entity_rule(entity.name, entity.attrs, meta=metadata)
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


def map_mode_metadata(root: BinElement) -> dict[str, AttrValue]:
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
            kind = rule_set.entity_kind(entity.name, entity.attrs, meta=metadata)
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


def _str_attr(value: AttrValue | None) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: AttrValue | None) -> int | None:
    return cast(int, value) if type(value) is int else None


def _number_attr(value: AttrValue | None) -> NumericAttrValue | None:
    return cast(NumericAttrValue, value) if type(value) in {int, float} else None
