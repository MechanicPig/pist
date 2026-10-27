"""Configurable static rules for map-to-map entrance regions."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, ValidationError, model_validator

from .entities.values import TypedAttrsKey, typed_attrs_key
from .game.binmap import AttrValue, NumericAttrValue
from .models import FrozenModel
from .types import NonEmptyStr

SHARED_MAP_ENTRANCES_PATH = Path(__file__).parent / 'data' / 'map_entrances.toml'


class MapEntranceSource(StrEnum):
    """The map layer that contains an entrance definition."""

    ENTITY = 'entity'
    TRIGGER = 'trigger'


@dataclass(frozen=True, slots=True)
class MapEntranceRuleKey:
    """The fields that make one map-entrance rule override another."""

    source: MapEntranceSource
    name: str
    when: TypedAttrsKey


class EntranceValue(FrozenModel):
    """One number read from an attribute or supplied as a constant."""

    attr: NonEmptyStr | None = None
    value: NumericAttrValue | None = None
    default: NumericAttrValue | None = None
    offset: NumericAttrValue = 0

    @model_validator(mode='after')
    def validate_source(self) -> EntranceValue:
        if (self.attr is None) == (self.value is None):
            raise ValueError('Entrance value needs exactly one of attr or value.')
        if self.attr is None and self.default is not None:
            raise ValueError('Entrance value default requires attr.')
        return self

    def resolve(self, attrs: Mapping[str, AttrValue]) -> NumericAttrValue | None:
        """Resolve this value from one map element's attributes."""
        if self.attr is None:
            return self.value + self.offset if self.value is not None else None
        value = _number(attrs.get(self.attr, self.default))
        return value + self.offset if value is not None else None


class MapEntranceRegion(FrozenModel):
    """The actual in-game rectangle represented by one map element."""

    x: EntranceValue
    y: EntranceValue
    width: EntranceValue | None = None
    height: EntranceValue | None = None


class MapEntranceRule(FrozenModel):
    """One exact static map entrance recognized in a map layer."""

    source: MapEntranceSource
    name: NonEmptyStr
    target_attr: NonEmptyStr = 'map'
    when: dict[str, AttrValue] = Field(default_factory=dict)
    region: MapEntranceRegion

    @property
    def key(self) -> MapEntranceRuleKey:
        """Return the fields used to match local rule overrides."""
        return MapEntranceRuleKey(self.source, self.name, typed_attrs_key(self.when.items()))

    def matches(self, source: MapEntranceSource, name: str, attrs: Mapping[str, AttrValue]) -> bool:
        """Return whether this rule applies to one concrete map element."""
        return (
            self.source is source
            and self.name == name
            and all(
                attr in attrs and type(attrs[attr]) is type(value) and attrs[attr] == value
                for attr, value in self.when.items()
            )
        )


class MapEntranceRules(FrozenModel):
    """Validated static map entrance rules loaded from TOML."""

    rules: tuple[MapEntranceRule, ...] = ()

    def merged(self, local: Self) -> Self:
        """Apply local overrides before shared rules with different keys."""
        local_keys = {rule.key for rule in local.rules}
        return type(self)(
            rules=(*local.rules, *(rule for rule in self.rules if rule.key not in local_keys))
        )

    @model_validator(mode='after')
    def validate_unique_rules(self) -> MapEntranceRules:
        seen: set[MapEntranceRuleKey] = set()
        for rule in self.rules:
            key = rule.key
            if key in seen:
                raise ValueError(f'Duplicate map entrance rule: {rule.source}:{rule.name!r}.')
            seen.add(key)
        return self

    def match(
        self, source: MapEntranceSource, name: str, attrs: Mapping[str, AttrValue]
    ) -> MapEntranceRule | None:
        """Return the first matching rule for a concrete map element."""
        return next((rule for rule in self.rules if rule.matches(source, name, attrs)), None)


def parse_map_entrance_rules(data: str) -> MapEntranceRules:
    """Parse and validate map entrance rules without reading files."""
    try:
        return MapEntranceRules.model_validate(tomllib.loads(data))
    except (TypeError, ValidationError, ValueError) as error:
        raise ValueError('Invalid map entrance rules config.') from error


def load_map_entrance_rules(path: Path) -> MapEntranceRules:
    """Read one explicitly selected TOML file; missing files are errors."""
    try:
        return parse_map_entrance_rules(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError(f'Invalid map entrance rules config: {path!r}') from error


def load_default_map_entrance_rules() -> MapEntranceRules:
    """Load packaged rules only, without discovering user configuration."""
    return load_map_entrance_rules(SHARED_MAP_ENTRANCES_PATH)


def _number(value: object) -> NumericAttrValue | None:
    match value:
        case bool() | str() | None:
            return None
        case int() | float():
            return value
        case _:
            return None
