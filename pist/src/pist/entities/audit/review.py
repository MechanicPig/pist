"""Resolve manual review coverage after classification-irrelevant attributes are removed."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .models import (
    EntityVariantSignature,
    ObservationQuestion,
    ObservationStatus,
    VariantObservation,
)


@dataclass(frozen=True, slots=True)
class VariantReviewIndex:
    """Classification evidence shared by equivalent raw variants, without modifying storage."""

    observations: Mapping[EntityVariantSignature, tuple[VariantObservation, ...]]

    @classmethod
    def build(
        cls, records: Iterable[tuple[EntityVariantSignature, VariantObservation]]
    ) -> VariantReviewIndex:
        grouped: dict[EntityVariantSignature, list[VariantObservation]] = defaultdict(list)
        for signature, observation in records:
            if observation.status is ObservationStatus.CONFLICT or (
                observation.question is ObservationQuestion.ENTITY_CLASSIFICATION
                and observation.status
                in {ObservationStatus.CONFIRMED, ObservationStatus.NOT_COLLECTIBLE}
            ):
                grouped[signature].append(observation)
        return cls(MappingProxyType({key: tuple(values) for key, values in grouped.items()}))

    def needs_review(self, signature: EntityVariantSignature) -> bool:
        """Require review for new or conflicting combinations, not equivalent confirmed variants."""
        if not self.observations:
            return False
        evidence = self.observations.get(signature, ())
        if any(item.status is ObservationStatus.CONFLICT for item in evidence):
            return True
        outcomes = {(item.status, item.kind) for item in evidence}
        return len(outcomes) != 1
