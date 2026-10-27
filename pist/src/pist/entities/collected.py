"""Check saved pickups against classification rules and Pist's human-review evidence."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from berries.entities import classification
from berries.entities.rules import EntityRules
from berries.game.binmap import AttrValue, BinMap
from berries.map_entity_id import MapEntityID

type VariantReview = Callable[[str, Mapping[str, AttrValue], Mapping[str, AttrValue]], bool]
type VariantReviewLoader = Callable[[frozenset[str]], VariantReview | None]


@dataclass(frozen=True, slots=True)
class UnreviewedCollectedEntity:
    """An encountered pickup whose exact variant still needs human review."""

    collected_id: MapEntityID
    entity_name: str
    attrs: dict[str, AttrValue]
    meta: dict[str, AttrValue] | None = None


type CollectedEntityReviewIssue = (
    classification.CollectedEntityRuleIssue | UnreviewedCollectedEntity
)


def collected_entity_review_issues(
    map_data: BinMap,
    collected: frozenset[MapEntityID],
    *,
    rule_set: EntityRules,
    needs_variant_review: VariantReview | None = None,
    variant_review_loader: VariantReviewLoader | None = None,
) -> tuple[CollectedEntityReviewIssue, ...]:
    """Combine public pickup-rule issues with unresolved human-review variants.

    Native saves only identify strawberry-style pickups as ``room:id``.  A missing
    map entity usually means the map was updated since the save was written, while
    a matched ``kind = null`` rule contradicts the game's collection record.
    Public issues are returned unchanged; an unreviewed variant takes precedence
    over the same pickup's rule result and has its own application-owned type.
    """
    if needs_variant_review is not None and variant_review_loader is not None:
        raise ValueError('Specify either a variant review checker or loader, not both.')
    checks = classification.check_collected_entity_rules(map_data, collected, rule_set=rule_set)
    if variant_review_loader is not None and (
        names := frozenset(check.entity.name for check in checks if check.entity is not None)
    ):
        needs_variant_review = variant_review_loader(names)
    issues: list[CollectedEntityReviewIssue] = []
    for check in checks:
        entity = check.entity
        if (
            entity is not None
            and needs_variant_review is not None
            and needs_variant_review(entity.name, entity.attrs, check.meta)
        ):
            issues.append(
                UnreviewedCollectedEntity(
                    check.collected_id,
                    entity.name,
                    entity.attrs.copy(),
                    dict(check.meta) or None,
                )
            )
        elif (issue := check.issue) is not None:
            issues.append(issue)
    return tuple(issues)
