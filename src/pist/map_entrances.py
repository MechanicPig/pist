"""Configurable static rules for map-to-map entrance regions."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pist.game.binmap import AttrValue, NumericAttrValue

SHARED_MAP_ENTRANCES_PATH = Path(__file__).parent / 'data' / 'map_entrances.toml'
LOCAL_MAP_ENTRANCES_PATH = Path('.pist/map_entrances.toml')


class MapEntranceSource(StrEnum):
    """The map layer that contains an entrance definition."""

    ENTITY = 'entity'
    TRIGGER = 'trigger'


@dataclass(frozen=True, slots=True)
class MapEntranceRuleKey:
    """The fields that make one map-entrance rule override another."""

    source: MapEntranceSource
    name: str
    when: frozenset[tuple[str, AttrValue]]


class EntranceValue(BaseModel):
    """One number read from an attribute or supplied as a constant."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    attr: str | None = None
    value: NumericAttrValue | None = None
    default: NumericAttrValue | None = None
    offset: NumericAttrValue = 0

    @model_validator(mode='after')
    def validate_source(self) -> EntranceValue:
        if (self.attr is None) == (self.value is None):
            raise ValueError('Entrance value needs exactly one of attr or value.')
        if self.attr is None and self.default is not None:
            raise ValueError('Entrance value default requires attr.')
        if self.attr is not None and not self.attr:
            raise ValueError('Entrance value attribute cannot be empty.')
        return self

    def resolve(self, attrs: Mapping[str, AttrValue]) -> NumericAttrValue | None:
        """Resolve this value from one map element's attributes."""
        if self.attr is None:
            return self.value + self.offset if self.value is not None else None
        value = _number(attrs.get(self.attr, self.default))
        return value + self.offset if value is not None else None


class MapEntranceRegion(BaseModel):
    """The actual in-game rectangle represented by one map element."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    x: EntranceValue
    y: EntranceValue
    width: EntranceValue | None = None
    height: EntranceValue | None = None


class MapEntranceRule(BaseModel):
    """One exact static map entrance recognized in a map layer."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    source: MapEntranceSource
    name: str
    target_attr: str = 'map'
    when: dict[str, AttrValue] = Field(default_factory=dict)
    region: MapEntranceRegion

    @model_validator(mode='after')
    def validate_names(self) -> MapEntranceRule:
        if not self.name:
            raise ValueError('Map entrance name cannot be empty.')
        if not self.target_attr:
            raise ValueError('Map entrance target attribute cannot be empty.')
        return self

    def matches(self, source: MapEntranceSource, name: str, attrs: Mapping[str, AttrValue]) -> bool:
        """Return whether this rule applies to one concrete map element."""
        return (
            self.source is source
            and self.name == name
            and all(attrs.get(attr) == value for attr, value in self.when.items())
        )


class MapEntranceRules(BaseModel):
    """Validated static map entrance rules loaded from TOML."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    rules: tuple[MapEntranceRule, ...] = ()

    @model_validator(mode='after')
    def validate_unique_rules(self) -> MapEntranceRules:
        seen: set[MapEntranceRuleKey] = set()
        for rule in self.rules:
            key = _rule_key(rule)
            if key in seen:
                raise ValueError(f'Duplicate map entrance rule: {rule.source}:{rule.name!r}.')
            seen.add(key)
        return self

    def match(
        self, source: MapEntranceSource, name: str, attrs: Mapping[str, AttrValue]
    ) -> MapEntranceRule | None:
        """Return the first matching rule for a concrete map element."""
        return next((rule for rule in self.rules if rule.matches(source, name, attrs)), None)


def load_map_entrance_rules(path: Path) -> MapEntranceRules:
    """Load map entrance rules from one TOML configuration file."""
    try:
        return MapEntranceRules.model_validate(_load_toml(path))
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(f'Invalid map entrance rules config: {path!r}') from error


def load_map_entrance_rule_layers(
    shared_path: Path = SHARED_MAP_ENTRANCES_PATH,
    local_path: Path = LOCAL_MAP_ENTRANCES_PATH,
) -> MapEntranceRules:
    """Load shared map-entrance rules with optional local overrides."""
    try:
        shared = MapEntranceRules.model_validate(_load_toml(shared_path))
        local = (
            MapEntranceRules.model_validate(_load_toml(local_path))
            if local_path.is_file()
            else MapEntranceRules()
        )
        return _merge_rule_layers(shared, local)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(
            f'Invalid map entrance rules config layers: {shared_path!r}, {local_path!r}'
        ) from error


def _load_toml(path: Path) -> dict[str, object]:
    with path.open('rb') as file:
        return tomllib.load(file)


def _merge_rule_layers(shared: MapEntranceRules, local: MapEntranceRules) -> MapEntranceRules:
    local_keys = {_rule_key(rule) for rule in local.rules}
    return MapEntranceRules(
        rules=(*local.rules, *(rule for rule in shared.rules if _rule_key(rule) not in local_keys))
    )


def _rule_key(rule: MapEntranceRule) -> MapEntranceRuleKey:
    return MapEntranceRuleKey(rule.source, rule.name, frozenset(rule.when.items()))


def _number(value: object) -> NumericAttrValue | None:
    match value:
        case bool() | str() | None:
            return None
        case int() | float():
            return value
        case _:
            return None


DEFAULT_MAP_ENTRANCE_RULES = load_map_entrance_rule_layers()
