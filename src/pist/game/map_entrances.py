"""Configurable static rules for map-to-map entrance regions."""

import tomllib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pist.game.binmap import AttrValue

SHARED_MAP_ENTRANCES_PATH = Path(__file__).parent.parent / 'data' / 'map_entrances.toml'
LOCAL_MAP_ENTRANCES_PATH = Path('.pist/map_entrances.toml')


class MapEntranceSource(StrEnum):
    """The map layer that contains an entrance definition."""

    ENTITY = 'entity'
    TRIGGER = 'trigger'


class EntranceValue(BaseModel):
    """One number read from an attribute or supplied as a constant."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    attr: str | None = None
    value: int | float | None = None
    default: int | float | None = None
    offset: int | float = 0

    @model_validator(mode='after')
    def validate_source(self) -> EntranceValue:
        if (self.attr is None) == (self.value is None):
            raise ValueError('Entrance value needs exactly one of attr or value.')
        if self.attr is None and self.default is not None:
            raise ValueError('Entrance value default requires attr.')
        if self.attr is not None and not self.attr:
            raise ValueError('Entrance value attribute cannot be empty.')
        return self

    def resolve(self, attrs: Mapping[str, AttrValue]) -> int | float | None:
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

    rules: tuple[MapEntranceRule, ...]

    @model_validator(mode='after')
    def validate_unique_rules(self) -> MapEntranceRules:
        seen: set[tuple[MapEntranceSource, str, tuple[tuple[str, AttrValue], ...]]] = set()
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
        shared = _load_toml(shared_path)
        local = _load_toml(local_path) if local_path.is_file() else {}
        return MapEntranceRules.model_validate(_merge_rule_data(shared, local))
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(
            f'Invalid map entrance rules config layers: {shared_path!r}, {local_path!r}'
        ) from error


def _load_toml(path: Path) -> dict[str, object]:
    with path.open('rb') as file:
        data = tomllib.load(file)
    if not isinstance(data, dict):
        raise TypeError('Expected a TOML table.')
    return data


def _merge_rule_data(shared: dict[str, object], local: dict[str, object]) -> dict[str, object]:
    shared_rules = _toml_rules(shared.get('rules'))
    local_rules = _toml_rules(local.get('rules'))
    local_keys = {_raw_rule_key(rule) for rule in local_rules}
    rules = [
        *local_rules,
        *(rule for rule in shared_rules if _raw_rule_key(rule) not in local_keys),
    ]
    return {**shared, **local, 'rules': rules}


def _toml_rules(value: object) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(rule, dict) for rule in value):
        raise TypeError('Expected map entrance rules to be a TOML array.')
    return value


def _raw_rule_key(rule: dict[str, object]) -> tuple[str, str, tuple[tuple[str, AttrValue], ...]]:
    source = rule.get('source')
    name = rule.get('name')
    when = rule.get('when', {})
    if not isinstance(source, str) or not isinstance(name, str):
        raise TypeError('Expected map entrance rule source and name to be strings.')
    if not isinstance(when, dict) or not all(
        isinstance(attr, str) and type(value) in {bool, int, float, str}
        for attr, value in when.items()
    ):
        raise TypeError('Expected map entrance rule condition to be a TOML table.')
    return source, name, tuple(sorted(when.items()))


def _rule_key(
    rule: MapEntranceRule,
) -> tuple[MapEntranceSource, str, tuple[tuple[str, AttrValue], ...]]:
    return rule.source, rule.name, tuple(sorted(rule.when.items()))


def _number(value: object) -> int | float | None:
    match value:
        case bool() | str() | None:
            return None
        case int() | float():
            return value
        case _:
            return None


DEFAULT_MAP_ENTRANCE_RULES = load_map_entrance_rule_layers()
