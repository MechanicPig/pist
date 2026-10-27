"""Build personal record statistics from public map classification results."""

from dataclasses import dataclass
from typing import Self

from berries.entities import rules
from berries.game import binmap, map_data
from berries.game.maps import Map
from berries.map_entity_id import MapEntityID
from pist import entity_stats
from pist.entities import collected as collected_entities
from pist.entities import config
from pist.routes.models import MapRoute
from pist.types import CellValue


def record_fields(
    stats: entity_stats.MapEntityStats, route: MapRoute | None
) -> dict[str, CellValue]:
    """Build record fields, applying the caller's explicit route correction."""
    fields: dict[str, CellValue] = dict(stats.record_fields)
    if route is not None:
        if route.room_count:
            fields['n_main_rooms'] = route.room_count
        else:
            fields.pop('n_main_rooms', None)  # pyright: ignore[reportUnusedCallResult]
    return fields


@dataclass(frozen=True, slots=True)
class MapEntityRecordReview:
    """One decoded map's personal summaries and saved-pickup rule issues."""

    stats: entity_stats.MapEntityStats
    collected_issues: tuple[collected_entities.CollectedEntityReviewIssue, ...]


@dataclass(frozen=True, slots=True)
class MapEntityRecordSource:
    """One decoded map and stable inputs for repeated rule evaluations."""

    map_data: binmap.BinMap
    collected: frozenset[MapEntityID]
    excluded_entities: frozenset[str] = frozenset()

    @classmethod
    def from_map(
        cls,
        map_file: Map,
        collected: frozenset[MapEntityID],
        *,
        excluded_entities: frozenset[str] = frozenset(),
    ) -> Self:
        """Read one active map once, retaining inputs needed for rule refreshes."""
        return cls(
            map_data.load_map_data(map_file),
            collected,
            excluded_entities,
        )

    def eval_rules(
        self,
        *,
        entity_rules: rules.EntityRules | None = None,
        stat_rules: entity_stats.EntityStatRules | None = None,
        needs_variant_review: collected_entities.VariantReview | None = None,
        variant_review_loader: collected_entities.VariantReviewLoader | None = None,
    ) -> MapEntityRecordReview:
        """Return personal statistics and collected-entity issues under the given rules.

        Missing rules are loaded from the packaged and user-local rule files.
        Each call returns new results without changing this source.
        """
        if needs_variant_review is not None and variant_review_loader is not None:
            raise ValueError('Specify either a variant review checker or loader, not both.')
        current_entity_rules = (
            config.load_entity_rule_layers() if entity_rules is None else entity_rules
        )
        current_stat_rules = (
            entity_stats.load_entity_stat_rules(current_entity_rules)
            if stat_rules is None
            else stat_rules
        )
        return MapEntityRecordReview(
            stats=entity_stats.map_entity_stats(
                self.map_data,
                excluded_entities=self.excluded_entities,
                entity_rules=current_entity_rules,
                stat_rules=current_stat_rules,
            ),
            collected_issues=collected_entities.collected_entity_review_issues(
                self.map_data,
                self.collected,
                rule_set=current_entity_rules,
                needs_variant_review=needs_variant_review,
                variant_review_loader=variant_review_loader,
            ),
        )
