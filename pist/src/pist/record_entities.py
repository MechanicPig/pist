"""Build personal record statistics from public map classification results."""

from dataclasses import dataclass
from pathlib import Path

from berries import map_layout
from berries.entities import classification, rules
from berries.game import binmap, content, levels
from berries.map_entity_id import MapEntityID
from pist import entity_stats
from pist.paths import PIST_DIR

LOCAL_ENTITIES_PATH = PIST_DIR / 'entities.toml'


@dataclass(frozen=True, slots=True)
class MapEntityRecordReview:
    """One decoded map's personal summaries and saved-pickup rule issues."""

    stats: entity_stats.MapEntityStats
    collected_issues: tuple[classification.CollectedEntityRuleIssue, ...]


@dataclass(frozen=True, slots=True)
class MapEntityRecordSource:
    """One decoded map and stable inputs for repeated rule evaluations."""

    map_data: binmap.BinMap
    collected: frozenset[MapEntityID]
    excluded_entities: frozenset[str] = frozenset()

    def eval_rules(
        self,
        *,
        entity_rules: rules.EntityRules | None = None,
        stat_rules: entity_stats.EntityStatRules | None = None,
        needs_variant_review: classification.VariantReview | None = None,
        variant_review_loader: classification.VariantReviewLoader | None = None,
    ) -> MapEntityRecordReview:
        """Return personal statistics and collected-entity issues under the given rules.

        Missing rules are loaded from the packaged and user-local rule files.
        Each call returns new results without changing this source.
        """
        if needs_variant_review is not None and variant_review_loader is not None:
            raise ValueError('Specify either a variant review checker or loader, not both.')
        current_entity_rules = (
            rules.load_entity_rule_layers(local_path=LOCAL_ENTITIES_PATH)
            if entity_rules is None
            else entity_rules
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
            collected_issues=classification.collected_entity_rule_issues(
                self.map_data,
                self.collected,
                rule_set=current_entity_rules,
                needs_variant_review=needs_variant_review,
                variant_review_loader=variant_review_loader,
            ),
        )


def load_map_entity_record_source_from_path(
    path: Path,
    map_file: content.StrPath,
    collected: frozenset[MapEntityID],
    *,
    excluded_entities: frozenset[str] = frozenset(),
) -> MapEntityRecordSource:
    """Read one map once, retaining inputs needed for rule refreshes."""
    return MapEntityRecordSource(
        map_layout.load_map_data_from_path(path, map_file),
        collected,
        excluded_entities,
    )


def load_map_entity_record_source(
    map_file: levels.Map,
    collected: frozenset[MapEntityID],
    *,
    excluded_entities: frozenset[str] = frozenset(),
) -> MapEntityRecordSource:
    """Read one active map once, retaining inputs needed for rule refreshes."""
    return MapEntityRecordSource(
        map_layout.load_map_data(map_file),
        collected,
        excluded_entities,
    )
