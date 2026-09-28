"""Apply the personal record application's entity-stat projection rules."""

import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from berries.entities.classification import ClassifiedEntity, classify_map_entities
from berries.entities.rules import EntityRules
from berries.game.binmap import BinMap
from berries.models import FrozenModel
from berries.types import StrippedNonEmptyStr
from pydantic import Field, ValidationError, model_validator

from pist.types import RecordValues

ENTITY_STATS_PATH = Path(__file__).parent / 'app_resources' / 'entity_stats.toml'


class EntityStatAggregation(StrEnum):
    """How the personal application aggregates one classified kind subtree."""

    COUNT = 'count'
    EXIST = 'exist'
    SELECT = 'select'


class EntityStatRule(FrozenModel):
    """One personal statistic and its destination record field."""

    kind: StrippedNonEmptyStr
    aggregation: EntityStatAggregation
    table: StrippedNonEmptyStr
    field: StrippedNonEmptyStr
    values: dict[StrippedNonEmptyStr, StrippedNonEmptyStr] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_values(self) -> EntityStatRule:
        if self.aggregation is EntityStatAggregation.SELECT:
            if not self.values:
                raise ValueError('A select statistic requires at least one kind value.')
        elif self.values:
            raise ValueError('Only a select statistic can define kind values.')
        return self


class EntityStatRules(FrozenModel):
    """The personal application's complete entity-stat projection rules."""

    stats: dict[str, EntityStatRule] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_targets(self) -> EntityStatRules:
        targets: dict[tuple[str, str], str] = {}
        for name, rule in self.stats.items():
            target = (rule.table, rule.field)
            if (existing := targets.setdefault(target, name)) != name:
                raise ValueError(
                    f'Record field {rule.table!r}/{rule.field!r} is configured by both '
                    f'{existing!r} and {name!r}.'
                )
        return self

    def validate_kinds(self, entity_rules: EntityRules) -> None:
        """Reject references invalidated by a public entity-rule update."""
        for name, rule in self.stats.items():
            if rule.kind not in entity_rules.kinds:
                raise ValueError(f'Unknown kind {rule.kind!r} in entity statistic {name!r}.')
            for kind in rule.values:
                if kind not in entity_rules.kinds:
                    raise ValueError(f'Unknown select kind {kind!r} in entity statistic {name!r}.')
                if not entity_rules.kind_is_a(kind, rule.kind):
                    raise ValueError(
                        f'Select kind {kind!r} is outside the {rule.kind!r} subtree in '
                        f'entity statistic {name!r}.'
                    )


@dataclass(frozen=True, slots=True)
class SelectConflict:
    """Distinct select values competing for one personal record field."""

    stat: str
    rule: EntityStatRule
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class MapEntityStats:
    """Entity summaries produced by the personal application's statistic rules."""

    rules: EntityStatRules = field(default_factory=EntityStatRules)
    counts: dict[str, int] = field(default_factory=dict)
    existing_stats: frozenset[str] = field(default_factory=frozenset)
    selected_stats: frozenset[str] = field(default_factory=frozenset)
    select_values: dict[str, frozenset[str]] = field(default_factory=dict)

    def count(self, stat: str) -> int:
        return self.counts.get(stat, 0)

    def exists(self, stat: str) -> bool:
        return stat in self.existing_stats

    def has_stat(self, stat: str) -> bool:
        return stat in self.counts or stat in self.existing_stats or stat in self.selected_stats

    @property
    def select_conflicts(self) -> tuple[SelectConflict, ...]:
        return tuple(
            SelectConflict(name, self.rules.stats[name], values)
            for name, values in sorted(self.select_values.items())
            if len(values) > 1
        )

    @property
    def record_values(self) -> RecordValues:
        values: RecordValues = {}
        for name, rule in self.rules.stats.items():
            match rule.aggregation:
                case EntityStatAggregation.COUNT:
                    value: int | bool | str = self.count(name)
                case EntityStatAggregation.EXIST:
                    value = self.exists(name)
                case EntityStatAggregation.SELECT:
                    selected = self.select_values.get(name, frozenset())
                    if len(selected) != 1:
                        continue
                    value = next(iter(selected))
            values.setdefault(rule.table, {})[rule.field] = value
        return values


def load_entity_stat_rules(
    entity_rules: EntityRules,
    path: Path = ENTITY_STATS_PATH,
) -> EntityStatRules:
    """Load personal statistics and validate every reference to public kinds."""
    try:
        with path.open('rb') as file:
            rules = EntityStatRules.model_validate(tomllib.load(file))
        rules.validate_kinds(entity_rules)
        return rules
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(f'Invalid personal entity statistic rules: {path!r}') from error


def map_entity_stats(
    map_data: BinMap,
    *,
    entity_rules: EntityRules,
    stat_rules: EntityStatRules,
    excluded_entities: frozenset[str] = frozenset(),
) -> MapEntityStats:
    """Classify a map, then apply personal statistics after instance exclusions."""
    stat_rules.validate_kinds(entity_rules)
    counts: dict[str, int] = {}
    existing: set[str] = set()
    selected: set[str] = set()
    select_values: dict[str, set[str]] = {}
    for entity in classify_map_entities(map_data, rule_set=entity_rules):
        if entity.key in excluded_entities:
            continue
        for name, rule in stat_rules.stats.items():
            if not entity_rules.kind_is_a(entity.kind, rule.kind):
                continue
            match rule.aggregation:
                case EntityStatAggregation.COUNT:
                    counts[name] = counts.get(name, 0) + 1
                case EntityStatAggregation.EXIST:
                    existing.add(name)
                case EntityStatAggregation.SELECT:
                    selected.add(name)
                    if (value := _select_value(entity, rule, entity_rules)) is not None:
                        select_values.setdefault(name, set()).add(value)
    return MapEntityStats(
        rules=stat_rules,
        counts=counts,
        existing_stats=frozenset(existing),
        selected_stats=frozenset(selected),
        select_values={name: frozenset(values) for name, values in select_values.items()},
    )


def _select_value(
    entity: ClassifiedEntity,
    rule: EntityStatRule,
    entity_rules: EntityRules,
) -> str | None:
    return select_value_for_kind(entity.kind, rule, entity_rules)


def select_value_for_kind(
    kind: str,
    rule: EntityStatRule,
    entity_rules: EntityRules,
) -> str | None:
    """Return the closest configured select value for one classified kind."""
    while entity_rules.kind_is_a(kind, rule.kind):
        if (value := rule.values.get(kind)) is not None:
            return value
        definition = entity_rules.kinds[kind]
        if definition.parent is None:
            return None
        kind = definition.parent
    return None
