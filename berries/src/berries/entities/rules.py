"""Configurable classification rules for map entities."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Self

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from berries.game.binmap import AttrValue
from berries.models import FrozenModel
from berries.types import NonEmptyStr

from .values import typed_attrs_key


class EntityKind(FrozenModel):
    """One entity kind in the shared classification tree."""

    label: str
    parent: str | None = None
    sprite: str | None = None

    @field_validator('sprite')
    @classmethod
    def validate_sprite(cls, sprite: str | None) -> str | None:
        """Keep configured sprite paths within the sprite directories."""
        if sprite is None:
            return None
        path = PurePosixPath(sprite)
        if not sprite or path.is_absolute() or '..' in path.parts:
            raise ValueError('Sprite path must be a relative path without "..".')
        return sprite


@dataclass(frozen=True, slots=True, eq=False)
class EntityRuleConditions:
    """The complete value and presence conditions identifying one entity rule."""

    when: tuple[tuple[str, AttrValue], ...]
    meta: tuple[tuple[str, AttrValue], ...]
    missing: tuple[str, ...]
    missing_meta: tuple[str, ...]

    def __hash__(self) -> int:
        return hash(
            (
                typed_attrs_key(self.when),
                typed_attrs_key(self.meta),
                self.missing,
                self.missing_meta,
            )
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EntityRuleConditions):
            return NotImplemented
        return (
            typed_attrs_key(self.when) == typed_attrs_key(other.when)
            and typed_attrs_key(self.meta) == typed_attrs_key(other.meta)
            and self.missing == other.missing
            and self.missing_meta == other.missing_meta
        )

    @property
    def specificity(self) -> int:
        """Return how many value or presence checks identify this condition."""
        return len(self.when) + len(self.meta) + len(self.missing) + len(self.missing_meta)


class EntityRule(FrozenModel):
    """One ordered entity rule whose optional kind is its terminal outcome."""

    kind: str | None = None
    when: dict[str, AttrValue] = Field(default_factory=dict)
    meta: dict[str, AttrValue] = Field(default_factory=dict)
    missing: tuple[NonEmptyStr, ...] = ()
    missing_meta: tuple[NonEmptyStr, ...] = ()

    @field_validator('missing', 'missing_meta')
    @classmethod
    def validate_missing_names(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        """Require each missing-condition name exactly once."""
        if len(set(names)) != len(names):
            raise ValueError('Missing condition names must be unique.')
        return names

    @model_validator(mode='after')
    def validate_conditions(self) -> EntityRule:
        """Keep value and missing conditions non-overlapping and unambiguous."""
        if overlap := self.when.keys() & set(self.missing):
            raise ValueError(f'Attribute cannot be both matched and missing: {overlap.pop()!r}.')
        if overlap := self.meta.keys() & set(self.missing_meta):
            raise ValueError(f'Metadata cannot be both matched and missing: {overlap.pop()!r}.')
        return self

    @property
    def conditions(self) -> EntityRuleConditions:
        """Return the conditions used to match rule overrides, independent of their outcome."""
        return EntityRuleConditions(
            tuple(sorted(self.when.items())),
            tuple(sorted(self.meta.items())),
            tuple(sorted(self.missing)),
            tuple(sorted(self.missing_meta)),
        )

    def matches(self, attrs: Mapping[str, AttrValue], meta: Mapping[str, AttrValue]) -> bool:
        """Match attribute values and presence conditions, preserving scalar types."""
        return (
            _attrs_match(attrs, self.when)
            and _attrs_match(meta, self.meta)
            and all(name not in attrs for name in self.missing)
            and all(name not in meta for name in self.missing_meta)
        )


class EntityRulesForId(FrozenModel):
    """Ordered classification rules for one exact entity ID."""

    rules: tuple[EntityRule, ...]

    def merged(self, local: Self) -> Self:
        """Apply local rule overrides before shared rules for one entity ID."""
        local_conditions = {rule.conditions for rule in local.rules}
        return type(self)(
            rules=(
                *local.rules,
                *(rule for rule in self.rules if rule.conditions not in local_conditions),
            )
        )

    def overrides(self, baseline: Self | None) -> Self | None:
        """Return only rules whose outcomes differ from an optional shared baseline."""
        if baseline is None:
            return self
        baseline_by_conditions = {rule.conditions: rule for rule in baseline.rules}
        rules = tuple(
            rule for rule in self.rules if baseline_by_conditions.get(rule.conditions) != rule
        )
        return None if not rules else type(self)(rules=rules)


@dataclass(frozen=True, slots=True)
class EntityRuleConflict:
    """One local rule that overrides a shared rule with the same condition."""

    entity_name: str
    conditions: EntityRuleConditions
    shared_kind: str | None
    local_kind: str | None


class EntityKindLayer(FrozenModel):
    """The category tree stored in ``kinds.toml``."""

    kinds: dict[str, EntityKind] = Field(default_factory=dict)


class EntityRuleLayer(FrozenModel):
    """One shared or local entity-rule TOML layer."""

    entities: dict[str, EntityRulesForId] = Field(default_factory=dict)

    def with_replaced_kind(self, old_name: str, new_name: str) -> Self:
        """Replace category references in this layer without changing its conditions."""
        return type(self)(entities=_renamed_entity_rules(self.entities, old_name, new_name))

    def merged(self, local: Self) -> Self:
        """Apply a local layer to this layer without cross-layer validation."""
        entities = self.entities.copy()
        for name, local_rules in local.entities.items():
            shared_rules = entities.get(name)
            entities[name] = (
                local_rules if shared_rules is None else shared_rules.merged(local_rules)
            )
        return type(self)(entities=entities)

    def conflicts(self, local: EntityRuleLayer) -> tuple[EntityRuleConflict, ...]:
        """Return local rules that replace a shared outcome with the same condition."""
        conflicts: list[EntityRuleConflict] = []
        for entity_name, local_rules in local.entities.items():
            shared_rules = self.entities.get(entity_name)
            if shared_rules is None:
                continue
            shared_by_conditions = {rule.conditions: rule for rule in shared_rules.rules}
            for local_rule in local_rules.rules:
                shared_rule = shared_by_conditions.get(local_rule.conditions)
                if shared_rule is not None and shared_rule.kind != local_rule.kind:
                    conflicts.append(
                        EntityRuleConflict(
                            entity_name,
                            local_rule.conditions,
                            shared_rule.kind,
                            local_rule.kind,
                        )
                    )
        return tuple(conflicts)

    def local_overrides(self, baseline: EntityRuleLayer | None) -> EntityRuleLayer:
        """Return the smallest layer that reproduces this layer over a baseline."""
        entities: dict[str, EntityRulesForId] = {}
        for name, entity_rules in self.entities.items():
            overrides = entity_rules.overrides(
                None if baseline is None else baseline.entities.get(name)
            )
            if overrides is not None:
                entities[name] = overrides
        return EntityRuleLayer(entities=entities)


class EntityRules(FrozenModel):
    """Validated entity kinds and rules ready for classification."""

    kinds: dict[str, EntityKind] = Field(default_factory=dict)
    entities: dict[str, EntityRulesForId] = Field(default_factory=dict)

    @classmethod
    def from_layers(cls, kinds: EntityKindLayer, *layers: EntityRuleLayer) -> Self:
        """Build rules from one kind layer and precedence-ordered entity layers."""
        merged = EntityRuleLayer()
        for layer in layers:
            merged = merged.merged(layer)
        return cls(kinds=kinds.kinds, entities=merged.entities)

    def _updated(self, **changes: object) -> Self:
        """Apply a change through full Pydantic reference validation."""
        return type(self).model_validate({'kinds': self.kinds, 'entities': self.entities} | changes)

    @property
    def leaf_kind_names(self) -> frozenset[str]:
        """Return the kinds that have no child kinds."""
        parents = {kind.parent for kind in self.kinds.values() if kind.parent is not None}
        return frozenset(self.kinds).difference(parents)

    def entity_kind(
        self,
        name: str,
        attrs: Mapping[str, AttrValue],
        *,
        meta: Mapping[str, AttrValue] | None = None,
    ) -> str | None:
        """Return the first configured outcome matching entity attributes and map metadata."""
        rule = self.matching_entity_rule(name, attrs, meta=meta)
        return None if rule is None else rule.kind

    def matching_entity_rule(
        self,
        name: str,
        attrs: Mapping[str, AttrValue],
        *,
        meta: Mapping[str, AttrValue] | None = None,
    ) -> EntityRule | None:
        """Return the exact matching rule, preserving an explicit null-kind outcome."""
        entity_rules = self.entities.get(name)
        if entity_rules is None:
            return None
        for rule in entity_rules.rules:
            if rule.matches(attrs, {} if meta is None else meta):
                return rule
        return None

    def kind_sprite(self, kind: str) -> str | None:
        """Return the closest configured preview sprite in the kind hierarchy."""
        while True:
            definition = self.kinds.get(kind)
            if definition is None:
                return None
            if definition.sprite is not None:
                return definition.sprite
            if definition.parent is None:
                return None
            kind = definition.parent

    def kind_is_a(self, kind: str, parent: str) -> bool:
        """Return whether a kind is equal to or descends from another configured kind."""
        while True:
            if kind == parent:
                return True
            definition = self.kinds.get(kind)
            if definition is None or definition.parent is None:
                return False
            kind = definition.parent

    @model_validator(mode='after')
    def validate_refs(self) -> EntityRules:
        for kind_name, kind in self.kinds.items():
            if kind.parent is not None and kind.parent not in self.kinds:
                raise ValueError(f'Unknown parent kind {kind.parent!r} for {kind_name!r}.')
        for kind_name, kind in self.kinds.items():
            parents: set[str] = set()
            parent = kind.parent
            while parent is not None:
                if parent in parents or parent == kind_name:
                    raise ValueError(f'Circular kind parent for {kind_name!r}.')
                parents.add(parent)
                parent = self.kinds[parent].parent
        for entity_name, entity_rules in self.entities.items():
            for rule in entity_rules.rules:
                if rule.kind is None:
                    continue
                if rule.kind not in self.kinds:
                    raise ValueError(f'Unknown kind {rule.kind!r} in entity rule {entity_name!r}.')
        return self

    def with_rule(
        self,
        entity_name: str,
        kind: str,
        when: Mapping[str, AttrValue],
        *,
        meta: Mapping[str, AttrValue] | None = None,
    ) -> EntityRules:
        """Return rules with one entity-and-attribute combination added or updated."""
        if kind not in self.kinds:
            raise ValueError(f'Unknown entity kind: {kind!r}')
        new_rule = EntityRule(kind=kind, when=dict(when), meta={} if meta is None else dict(meta))
        entity_rules = self.entities.get(entity_name, EntityRulesForId(rules=()))
        rules = tuple(
            new_rule if rule.conditions == new_rule.conditions else rule
            for rule in entity_rules.rules
        )
        if not any(rule.conditions == new_rule.conditions for rule in rules):
            rules = (new_rule, *rules)
        rules = _specific_rules_first(rules)
        entities = self.entities.copy()
        entities[entity_name] = EntityRulesForId(rules=rules)
        return self._updated(entities=entities)

    def with_exclusion(
        self,
        entity_name: str,
        when: Mapping[str, AttrValue],
        *,
        meta: Mapping[str, AttrValue] | None = None,
    ) -> EntityRules:
        """Return rules with one terminal non-collectible condition added or updated."""
        new_rule = EntityRule(kind=None, when=dict(when), meta={} if meta is None else dict(meta))
        entity_rules = self.entities.get(entity_name, EntityRulesForId(rules=()))
        rules = tuple(
            new_rule if rule.conditions == new_rule.conditions else rule
            for rule in entity_rules.rules
        )
        if not any(rule.conditions == new_rule.conditions for rule in rules):
            rules = (new_rule, *rules)
        rules = _specific_rules_first(rules)
        entities = self.entities.copy()
        entities[entity_name] = EntityRulesForId(rules=rules)
        return self._updated(entities=entities)

    def with_kind(
        self,
        name: str,
        definition: EntityKind,
    ) -> EntityRules:
        """Return rules with one new kind in the hierarchy."""
        if not name:
            raise ValueError('Entity kind name must not be empty.')
        if name in self.kinds:
            raise ValueError(f'Entity kind already exists: {name!r}')
        if definition.parent is not None and definition.parent not in self.kinds:
            raise ValueError(f'Unknown parent kind: {definition.parent!r}')
        kinds = self.kinds.copy()
        kinds[name] = (
            definition if definition.label else definition.model_copy(update={'label': name})
        )
        return self._updated(kinds=kinds)

    def with_updated_kind(
        self,
        name: str,
        definition: EntityKind,
    ) -> EntityRules:
        """Return rules with one existing kind's editable fields updated."""
        if name not in self.kinds:
            raise ValueError(f'Unknown entity kind: {name!r}')
        if definition.parent is not None and definition.parent not in self.kinds:
            raise ValueError(f'Unknown parent kind: {definition.parent!r}')
        kinds = self.kinds.copy()
        kinds[name] = (
            definition if definition.label else definition.model_copy(update={'label': name})
        )
        return self._updated(kinds=kinds)

    def with_renamed_kind(
        self,
        old_name: str,
        new_name: str,
        definition: EntityKind,
    ) -> EntityRules:
        """Rename one kind and every hierarchy or entity-rule reference to it."""
        if old_name not in self.kinds:
            raise ValueError(f'Unknown entity kind: {old_name!r}')
        if not new_name:
            raise ValueError('Entity kind name must not be empty.')
        if new_name != old_name and new_name in self.kinds:
            raise ValueError(f'Entity kind already exists: {new_name!r}')
        kinds = {
            (new_name if name == old_name else name): definition.model_copy(
                update={'parent': new_name if definition.parent == old_name else definition.parent}
            )
            for name, definition in self.kinds.items()
        }
        renamed = self._updated(
            kinds=kinds,
            entities=_renamed_entity_rules(self.entities, old_name, new_name),
        )
        return renamed.with_updated_kind(new_name, definition)

    def with_deleted_kind(self, name: str) -> EntityRules:
        """Delete a non-root kind, falling its rules and children back to its parent."""
        definition = self.kinds.get(name)
        if definition is None:
            raise ValueError(f'Unknown entity kind: {name!r}')
        if definition.parent is None:
            raise ValueError(f'Cannot delete root entity kind: {name!r}')
        kinds = {
            kind_name: kind.model_copy(
                update={'parent': definition.parent if kind.parent == name else kind.parent}
            )
            for kind_name, kind in self.kinds.items()
            if kind_name != name
        }
        return self._updated(
            kinds=kinds,
            entities=_renamed_entity_rules(self.entities, name, definition.parent),
        )

    def direct_rule_count(self, kind: str) -> int:
        """Return the number of effective entity rules that directly choose one kind."""
        return sum(
            rule.kind == kind
            for entity_rules in self.entities.values()
            for rule in entity_rules.rules
        )


def _renamed_entity_rules(
    entities: Mapping[str, EntityRulesForId], old_name: str, new_name: str
) -> dict[str, EntityRulesForId]:
    return {
        entity_name: EntityRulesForId(
            rules=tuple(
                rule.model_copy(update={'kind': new_name if rule.kind == old_name else rule.kind})
                for rule in entity_rules.rules
            )
        )
        for entity_name, entity_rules in entities.items()
    }


def _specific_rules_first(rules: tuple[EntityRule, ...]) -> tuple[EntityRule, ...]:
    """Keep specific conditions before defaults, regardless of write order."""
    return tuple(sorted(rules, key=_rule_specificity, reverse=True))


def _rule_specificity(rule: EntityRule) -> int:
    return rule.conditions.specificity


def _attrs_match(actual: Mapping[str, AttrValue], expected: Mapping[str, AttrValue]) -> bool:
    for attr, expected_value in expected.items():
        if attr not in actual:
            return False
        actual_value = actual[attr]
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            return False
    return True
