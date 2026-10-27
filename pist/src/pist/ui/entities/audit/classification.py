"""Classification review groups, independent of Textual application state."""

from collections import defaultdict
from dataclasses import dataclass

from berries.entities.values import TypedAttrsKey, typed_attrs_key
from berries.game.binmap import AttrValue
from pist.entities import audit as backend


@dataclass(frozen=True, slots=True)
class ClassificationGroup:
    """Raw variants sharing every attribute not confirmed irrelevant to kind."""

    attrs: dict[str, AttrValue | None]
    meta: dict[str, AttrValue | None]
    variants: tuple[backend.EntityVariant, ...]

    @property
    def occurrence_count(self) -> int:
        return sum(variant.occurrence_count for variant in self.variants)

    @property
    def classification(self) -> tuple[backend.ObservationStatus, str | None] | None:
        """Return a shared terminal conclusion, only when every variant has one."""
        conclusions: set[tuple[backend.ObservationStatus, str | None]] = set()
        for variant in self.variants:
            if variant.classification is None:
                return None
            conclusions.add(variant.classification)
        return conclusions.pop() if len(conclusions) == 1 else None


def classification_groups(detail: backend.EntityAuditDetail) -> tuple[ClassificationGroup, ...]:
    """Merge only attributes already confirmed irrelevant to kind."""
    excluded = {
        backend.AttrAuditStatus.DOES_NOT_AFFECT_KIND,
        backend.AttrAuditStatus.LIKELY_NOT_AFFECT_KIND,
        backend.AttrAuditStatus.AFFECTS_BEHAVIOR,
    }
    names = tuple(
        attribute.name for attribute in detail.attr_summaries if attribute.status not in excluded
    )
    groups: dict[TypedAttrsKey, list[backend.EntityVariant]] = defaultdict(list)
    for variant in detail.variants:
        groups[
            typed_attrs_key(
                (
                    name,
                    variant.meta.get(name.removeprefix(backend.META_ATTR_PREFIX))
                    if name.startswith(backend.META_ATTR_PREFIX)
                    else variant.attrs.get(name),
                )
                for name in names
            )
        ].append(variant)
    return tuple(
        ClassificationGroup(
            {
                name: value
                for name, _, value in values
                if not name.startswith(backend.META_ATTR_PREFIX)
            },
            {
                name.removeprefix(backend.META_ATTR_PREFIX): value
                for name, _, value in values
                if name.startswith(backend.META_ATTR_PREFIX)
            },
            tuple(variants),
        )
        for values, variants in sorted(
            groups.items(),
            key=lambda item: (
                -sum(variant.occurrence_count for variant in item[1]),
                repr(item[0]),
            ),
        )
    )
