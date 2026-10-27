"""Canonical TOML serialization for entity rules and category definitions."""

from collections.abc import Mapping

import tomlkit
from tomlkit.items import Array, InlineTable

from berries.game.binmap import AttrValue

from .rules import EntityKindLayer, EntityRule, EntityRuleLayer, EntityRules


def entity_kinds_toml(kinds: EntityKindLayer) -> str:
    """Serialize the category tree in Berries' canonical TOML layout."""
    return tomlkit.dumps(kinds.model_dump(mode='json', exclude_none=True, exclude_defaults=True))


def entity_rules_toml(
    rules: EntityRuleLayer | EntityRules, *, baseline: EntityRules | None = None
) -> str:
    """Serialize validated entity rules in Berries' canonical TOML layout."""
    layer = EntityRuleLayer(entities=rules.entities)
    if baseline is not None:
        layer = layer.local_overrides(EntityRuleLayer(entities=baseline.entities))
    document = tomlkit.document()
    entities = tomlkit.table()
    document['entities'] = entities
    for entity_name, rules_for_id in layer.entities.items():
        entity = tomlkit.table()
        entity['rules'] = _toml_rules(rules_for_id.rules)
        entities[entity_name] = entity
    return tomlkit.dumps(document)


def _toml_rules(rules: tuple[EntityRule, ...]) -> Array:
    if not rules:
        return tomlkit.array()
    values = tomlkit.array().multiline(True)
    values.extend(_toml_rule(rule) for rule in rules)
    return values


def _toml_rule(rule: EntityRule) -> InlineTable:
    fields: list[tuple[str, AttrValue | Mapping[str, AttrValue] | tuple[str, ...]]] = []
    if rule.kind is not None:
        fields.append(('kind', rule.kind))
    if rule.when:
        fields.append(('when', rule.when))
    if rule.meta:
        fields.append(('meta', rule.meta))
    if rule.missing:
        fields.append(('missing', rule.missing))
    if rule.missing_meta:
        fields.append(('missing_meta', rule.missing_meta))
    table = _empty_inline_table()
    for key, value in fields:
        table[key] = _toml_value(value)
        table.add(tomlkit.ws(' '))  # pyright: ignore[reportUnusedCallResult]
    return table


def _toml_value(
    value: AttrValue | Mapping[str, AttrValue] | tuple[str, ...],
) -> AttrValue | Array | InlineTable:
    if isinstance(value, Mapping):
        table = _empty_inline_table()
        for key, item in value.items():
            table[key] = item
            table.add(tomlkit.ws(' '))  # pyright: ignore[reportUnusedCallResult]
        return table
    if isinstance(value, tuple):
        values = tomlkit.array()
        values.extend(value)
        return values
    return value


def _empty_inline_table() -> InlineTable:
    """Create an inline table with the canonical spaces inside its braces."""
    value = tomlkit.parse('value = { }')['value']
    assert isinstance(value, InlineTable)
    return value
