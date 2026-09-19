"""Configurable classification rules for map entities."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Self

import tomlkit
from pydantic import (
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from tomlkit.items import Array, InlineTable

from pist.game.binmap import AttrValue
from pist.models import FrozenModel
from pist.types import NonEmptyStr, StrippedNonEmptyStr


class EntityStat(StrEnum):
    """How map data summarizes entities belonging to one kind."""

    NONE = 'none'
    COUNT = 'count'
    EXIST = 'exist'
    SELECT = 'select'


class EntityTableField(FrozenModel):
    """One table-and-field destination for a configured entity summary."""

    table: StrippedNonEmptyStr
    field: StrippedNonEmptyStr


SHARED_ENTITIES_PATH = Path(__file__).parent.parent / 'data' / 'entities.toml'
SHARED_KINDS_PATH = Path(__file__).parent.parent / 'data' / 'kinds.toml'
LOCAL_ENTITIES_PATH = Path('.pist/entities.toml')


class EntityKind(FrozenModel):
    """One user-extensible entity kind in the classification tree."""

    label: str
    parent: str | None = None
    stat: EntityStat = EntityStat.NONE
    table_field: EntityTableField | None = None
    select_value: StrippedNonEmptyStr | None = None
    sprite: str | None = None

    @model_validator(mode='after')
    def validate_table_field(self) -> EntityKind:
        """Keep external table destinations meaningful and type-directed."""
        if self.stat is EntityStat.NONE and self.table_field is not None:
            raise ValueError('A table field requires stat = "count" or "exist".')
        return self

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


@dataclass(frozen=True, slots=True)
class EntityRuleConditions:
    """The complete value and presence conditions identifying one entity rule."""

    when: tuple[tuple[str, AttrValue], ...]
    meta: tuple[tuple[str, AttrValue], ...]
    missing: tuple[str, ...]
    missing_meta: tuple[str, ...]

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
    def identity(self) -> EntityRuleConditions:
        """Return the condition that identifies one local override."""
        return EntityRuleConditions(
            tuple(sorted(self.when.items())),
            tuple(sorted(self.meta.items())),
            tuple(sorted(self.missing)),
            tuple(sorted(self.missing_meta)),
        )


class EntityRulesForId(FrozenModel):
    """Ordered classification rules for one exact entity ID."""

    rules: tuple[EntityRule, ...]

    def merged(self, local: Self) -> Self:
        """Apply local rule overrides before shared rules for one entity ID."""
        local_identities = {rule.identity for rule in local.rules}
        return type(self).model_validate(
            {
                'rules': (
                    *local.rules,
                    *(rule for rule in self.rules if rule.identity not in local_identities),
                )
            }
        )

    def overrides(self, baseline: Self | None) -> Self | None:
        """Return only rules whose outcomes differ from an optional shared baseline."""
        if baseline is None:
            return self
        baseline_by_identity = {rule.identity: rule for rule in baseline.rules}
        rules = tuple(
            rule for rule in self.rules if baseline_by_identity.get(rule.identity) != rule
        )
        return None if not rules else type(self).model_validate({'rules': rules})


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

    def merged(self, local: Self) -> Self:
        """Apply a local layer to this layer without cross-layer validation."""
        entities = self.entities.copy()
        for name, local_rules in local.entities.items():
            shared_rules = entities.get(name)
            entities[name] = (
                local_rules if shared_rules is None else shared_rules.merged(local_rules)
            )
        return type(self).model_validate({'entities': entities})

    def conflicts(self, local: EntityRuleLayer) -> tuple[EntityRuleConflict, ...]:
        """Return local rules that replace a shared outcome with the same condition."""
        conflicts: list[EntityRuleConflict] = []
        for entity_name, local_rules in local.entities.items():
            shared_rules = self.entities.get(entity_name)
            if shared_rules is None:
                continue
            shared_by_identity = {rule.identity: rule for rule in shared_rules.rules}
            for local_rule in local_rules.rules:
                shared_rule = shared_by_identity.get(local_rule.identity)
                if shared_rule is not None and shared_rule.kind != local_rule.kind:
                    conflicts.append(
                        EntityRuleConflict(
                            entity_name,
                            local_rule.identity,
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

    @model_validator(mode='after')
    def validate_references(self) -> EntityRules:
        table_fields: dict[EntityTableField, str] = {}
        for kind_name, kind in self.kinds.items():
            if kind.parent is not None and kind.parent not in self.kinds:
                raise ValueError(f'Unknown parent kind {kind.parent!r} for {kind_name!r}.')
            parents: set[str] = set()
            parent = kind.parent
            while parent is not None:
                if parent in parents or parent == kind_name:
                    raise ValueError(f'Circular kind parent for {kind_name!r}.')
                parents.add(parent)
                parent = self.kinds[parent].parent
            if kind.table_field is not None:
                existing_kind = table_fields.setdefault(kind.table_field, kind_name)
                if existing_kind != kind_name:
                    raise ValueError(
                        f'Table field {kind.table_field.table!r}/{kind.table_field.field!r} '
                        f'is configured by both {existing_kind!r} and {kind_name!r}.'
                    )
            if kind.select_value is not None:
                owner = kind_name
                while self.kinds[owner].stat is EntityStat.NONE:
                    parent = self.kinds[owner].parent
                    if parent is None:
                        raise ValueError(
                            f'Select value for {kind_name!r} has no select-stat ancestor.'
                        )
                    owner = parent
                if self.kinds[owner].stat is not EntityStat.SELECT or owner == kind_name:
                    raise ValueError(
                        f'Select value for {kind_name!r} requires a select-stat ancestor.'
                    )
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
            new_rule if rule.identity == new_rule.identity else rule for rule in entity_rules.rules
        )
        if new_rule not in rules:
            rules = (new_rule, *rules)
        rules = _specific_rules_first(rules)
        entities = self.entities.copy()
        entities[entity_name] = EntityRulesForId.model_validate({'rules': rules})
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
            new_rule if rule.identity == new_rule.identity else rule for rule in entity_rules.rules
        )
        if new_rule not in rules:
            rules = (new_rule, *rules)
        rules = _specific_rules_first(rules)
        entities = self.entities.copy()
        entities[entity_name] = EntityRulesForId.model_validate({'rules': rules})
        return self._updated(entities=entities)

    def with_kind(
        self,
        name: str,
        label: str,
        *,
        parent: str | None = None,
        sprite: str | None = None,
        stat: EntityStat = EntityStat.NONE,
        table_field: EntityTableField | None = None,
        select_value: str | None = None,
    ) -> EntityRules:
        """Return rules with one new user-defined kind in the hierarchy."""
        if not name:
            raise ValueError('Entity kind name must not be empty.')
        if name in self.kinds:
            raise ValueError(f'Entity kind already exists: {name!r}')
        if parent is not None and parent not in self.kinds:
            raise ValueError(f'Unknown parent kind: {parent!r}')
        kinds = self.kinds.copy()
        kinds[name] = EntityKind(
            label=label or name,
            parent=parent,
            sprite=sprite,
            stat=stat,
            table_field=table_field,
            select_value=select_value,
        )
        return self._updated(kinds=kinds)

    def with_updated_kind(
        self,
        name: str,
        label: str,
        *,
        parent: str | None = None,
        sprite: str | None = None,
        stat: EntityStat | None = None,
        table_field: EntityTableField | None = None,
        select_value: str | None = None,
    ) -> EntityRules:
        """Return rules with one existing kind's editable fields updated."""
        if name not in self.kinds:
            raise ValueError(f'Unknown entity kind: {name!r}')
        if parent is not None and parent not in self.kinds:
            raise ValueError(f'Unknown parent kind: {parent!r}')
        kinds = self.kinds.copy()
        current = kinds[name]
        if stat is None:
            stat = current.stat
            table_field = current.table_field
            select_value = current.select_value
        kinds[name] = EntityKind(
            label=label or name,
            parent=parent,
            stat=stat,
            table_field=table_field,
            select_value=select_value,
            sprite=sprite,
        )
        return self._updated(kinds=kinds)

    def with_renamed_kind(
        self,
        old_name: str,
        new_name: str,
        label: str,
        *,
        parent: str | None = None,
        sprite: str | None = None,
        stat: EntityStat | None = None,
        table_field: EntityTableField | None = None,
        select_value: str | None = None,
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
        return renamed.with_updated_kind(
            new_name,
            label,
            parent=parent,
            sprite=sprite,
            stat=stat,
            table_field=table_field,
            select_value=select_value,
        )

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


def load_entity_rules(path: Path, *, kinds_path: Path | None = None) -> EntityRules:
    """Load one entity-rule file with its separate category configuration."""
    try:
        if kinds_path is None:
            if path.resolve() != SHARED_ENTITIES_PATH.resolve():
                raise ValueError(
                    'Specify a separate kinds.toml path for a custom entity rules file.'
                )
            kinds_path = SHARED_KINDS_PATH
        layer = EntityRuleLayer.model_validate(_load_toml(path))
        kinds = EntityKindLayer.model_validate(_load_toml(kinds_path))
        return EntityRules.from_layers(kinds, layer)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(f'Invalid entity rules config: {path!r}') from error


def load_entity_rule_layers(
    shared_path: Path = SHARED_ENTITIES_PATH,
    local_path: Path = LOCAL_ENTITIES_PATH,
    *,
    kinds_path: Path | None = None,
) -> EntityRules:
    """Load shared and optional local rule layers in priority order."""
    if (shared_path, local_path) == (
        SHARED_ENTITIES_PATH,
        LOCAL_ENTITIES_PATH,
    ) and kinds_path is None:
        kinds_path = SHARED_KINDS_PATH
    if kinds_path is None:
        raise ValueError('Specify a separate kinds.toml path for custom entity rule layers.')
    rules, _ = _load_entity_rule_layers(shared_path, kinds_path, local_path)
    return rules


def _load_entity_rule_layers(
    shared_path: Path,
    kinds_path: Path,
    local_path: Path,
) -> tuple[EntityRules, tuple[EntityRuleConflict, ...]]:
    try:
        shared = EntityRuleLayer.model_validate(_load_toml(shared_path))
        kinds = EntityKindLayer.model_validate(_load_toml(kinds_path))
        local = (
            EntityRuleLayer.model_validate(_load_toml(local_path))
            if local_path.is_file()
            else EntityRuleLayer()
        )
        return EntityRules.from_layers(kinds, shared, local), shared.conflicts(local)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(
            f'Invalid entity rules config layers: {kinds_path!r}, {shared_path!r}, {local_path!r}'
        ) from error


class EntityConfigStore:
    """Load shared rules and persist either shared or local rule additions."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        shared: bool = False,
        shared_path: Path = SHARED_ENTITIES_PATH,
        kinds_path: Path | None = None,
        local_path: Path = LOCAL_ENTITIES_PATH,
    ) -> None:
        if path is not None:
            self._shared_path = path
            if kinds_path is None and path.resolve() != SHARED_ENTITIES_PATH.resolve():
                raise ValueError('Specify kinds_path for a custom entity rules file.')
            self._kinds_path = SHARED_KINDS_PATH if kinds_path is None else kinds_path
            self._local_path: Path | None = None
        else:
            self._shared_path = shared_path
            if kinds_path is None and (shared_path, local_path) != (
                SHARED_ENTITIES_PATH,
                LOCAL_ENTITIES_PATH,
            ):
                raise ValueError('Specify kinds_path for custom entity rule layers.')
            self._kinds_path = SHARED_KINDS_PATH if kinds_path is None else kinds_path
            self._local_path = None if shared else local_path
        self._conflicts: tuple[EntityRuleConflict, ...] = ()

    @property
    def path(self) -> Path:
        """Return the rule file this store writes."""
        return self._shared_path if self._local_path is None else self._local_path

    @property
    def conflicts(self) -> tuple[EntityRuleConflict, ...]:
        """Return local-over-shared rule overrides observed during the last load."""
        return self._conflicts

    def load(self) -> EntityRules:
        if self._local_path is None:
            self._conflicts = ()
            return EntityRules.from_layers(
                EntityKindLayer.model_validate(_load_toml(self._kinds_path)),
                EntityRuleLayer.model_validate(_load_toml(self._shared_path)),
            )
        rules, self._conflicts = _load_entity_rule_layers(
            self._shared_path, self._kinds_path, self._local_path
        )
        return rules

    def save(self, rules: EntityRules) -> None:
        current_kinds = EntityKindLayer.model_validate(_load_toml(self._kinds_path))
        kind_layer = EntityKindLayer(kinds=rules.kinds)
        if current_kinds.kinds != rules.kinds:
            self._kinds_path.parent.mkdir(parents=True, exist_ok=True)
            self._kinds_path.write_text(entity_kinds_toml(kind_layer), encoding='utf-8')
        baseline = (
            EntityRules.from_layers(kind_layer)
            if self._local_path is None
            else EntityRules.from_layers(
                kind_layer,
                EntityRuleLayer.model_validate(_load_toml(self._shared_path)),
            )
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(entity_rules_toml(rules, baseline=baseline), encoding='utf-8')

    def rename_kind(self, old_name: str, new_name: str, rules: EntityRules) -> None:
        """Persist a kind-ID rename across every rule layer this store owns."""
        if old_name == new_name:
            self.save(rules)
            return

        paths_and_layers: list[tuple[Path, EntityRuleLayer]] = [
            (self._shared_path, EntityRuleLayer.model_validate(_load_toml(self._shared_path))),
        ]
        if self._local_path is not None and self._local_path.is_file():
            paths_and_layers.append(
                (self._local_path, EntityRuleLayer.model_validate(_load_toml(self._local_path)))
            )

        renamed_layers = [
            (
                path,
                EntityRuleLayer(entities=_renamed_entity_rules(layer.entities, old_name, new_name)),
            )
            for path, layer in paths_and_layers
        ]
        kind_layer = EntityKindLayer(kinds=rules.kinds)
        # Validate all writes together before modifying any user-visible file.
        EntityRules.from_layers(kind_layer, *(layer for _, layer in renamed_layers))

        self._kinds_path.parent.mkdir(parents=True, exist_ok=True)
        self._kinds_path.write_text(entity_kinds_toml(kind_layer), encoding='utf-8')
        for path, layer in renamed_layers:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entity_rules_toml(layer), encoding='utf-8')

    def delete_kind(self, name: str, fallback_kind: str, rules: EntityRules) -> None:
        """Persist a kind deletion across every rule layer this store owns."""
        paths_and_layers: list[tuple[Path, EntityRuleLayer]] = [
            (self._shared_path, EntityRuleLayer.model_validate(_load_toml(self._shared_path))),
        ]
        if self._local_path is not None and self._local_path.is_file():
            paths_and_layers.append(
                (self._local_path, EntityRuleLayer.model_validate(_load_toml(self._local_path)))
            )
        updated_layers = [
            (
                path,
                EntityRuleLayer(
                    entities=_renamed_entity_rules(layer.entities, name, fallback_kind)
                ),
            )
            for path, layer in paths_and_layers
        ]
        kind_layer = EntityKindLayer(kinds=rules.kinds)
        EntityRules.from_layers(kind_layer, *(layer for _, layer in updated_layers))

        self._kinds_path.parent.mkdir(parents=True, exist_ok=True)
        self._kinds_path.write_text(entity_kinds_toml(kind_layer), encoding='utf-8')
        for path, layer in updated_layers:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entity_rules_toml(layer), encoding='utf-8')

    def load_shared_layer(self) -> EntityRuleLayer:
        """Load the shared entity rules without kinds or user-local overrides."""
        try:
            return EntityRuleLayer.model_validate(_load_toml(self._shared_path))
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
            raise ValueError(f'Invalid shared entity rules: {self._shared_path!r}') from error

    def save_generated_layer(self, layer: EntityRuleLayer) -> None:
        """Replace the published shared entity rules with audited generated rules."""
        self._shared_path.parent.mkdir(parents=True, exist_ok=True)
        self._shared_path.write_text(entity_rules_toml(layer), encoding='utf-8')


def _load_toml(path: Path) -> dict[str, object]:
    with path.open('rb') as file:
        data = tomllib.load(file)
    if not isinstance(data, dict):
        raise TypeError('Expected a TOML table.')
    return data


def _specific_rules_first(rules: tuple[EntityRule, ...]) -> tuple[EntityRule, ...]:
    """Keep specific conditions before defaults, regardless of write order."""
    return tuple(sorted(rules, key=_rule_specificity, reverse=True))


def _rule_specificity(rule: EntityRule) -> int:
    return rule.identity.specificity


def entity_kinds_toml(kinds: EntityKindLayer) -> str:
    """Serialize the category tree in pist's canonical TOML layout."""
    return tomlkit.dumps(kinds.model_dump(mode='json', exclude_none=True, exclude_defaults=True))


def entity_rules_toml(
    rules: EntityRuleLayer | EntityRules, *, baseline: EntityRules | None = None
) -> str:
    """Serialize validated entity rules in pist's canonical TOML layout."""
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
        table.add(tomlkit.ws(' '))
    return table


def _toml_value(
    value: AttrValue | Mapping[str, AttrValue] | tuple[str, ...],
) -> AttrValue | Array | InlineTable:
    if isinstance(value, Mapping):
        table = _empty_inline_table()
        for key, item in value.items():
            table[key] = item
            table.add(tomlkit.ws(' '))
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


def entity_kind(
    name: str,
    attrs: Mapping[str, AttrValue],
    *,
    meta: Mapping[str, AttrValue] | None = None,
    rules: EntityRules,
) -> str | None:
    """Return the first configured outcome matching entity attributes and map metadata."""
    rule = matching_entity_rule(name, attrs, meta=meta, rules=rules)
    return None if rule is None else rule.kind


def matching_entity_rule(
    name: str,
    attrs: Mapping[str, AttrValue],
    *,
    meta: Mapping[str, AttrValue] | None = None,
    rules: EntityRules,
) -> EntityRule | None:
    """Return the exact matching rule, preserving an explicit ``kind = null`` outcome."""
    entity_rules = rules.entities.get(name)
    if entity_rules is None:
        return None
    for rule in entity_rules.rules:
        if _rule_matches(rule, attrs, {} if meta is None else meta):
            return rule
    return None


def stat_owner(
    kind: str,
    *,
    rules: EntityRules,
) -> tuple[str, EntityStat] | None:
    """Return the closest summary-owning ancestor of a kind, if any."""
    while True:
        definition = rules.kinds.get(kind)
        if definition is None:
            return None
        if definition.stat is not EntityStat.NONE:
            return kind, definition.stat
        if definition.parent is None:
            return None
        kind = definition.parent


def kind_select_value(
    kind: str,
    *,
    rules: EntityRules,
) -> str | None:
    """Return the closest configured output value beneath a select-stat owner."""
    while True:
        definition = rules.kinds.get(kind)
        if definition is None:
            return None
        if definition.select_value is not None:
            return definition.select_value
        if definition.parent is None:
            return None
        kind = definition.parent


def kind_sprite(
    kind: str,
    *,
    rules: EntityRules,
) -> str | None:
    """Return the closest configured preview sprite in the kind hierarchy."""
    while True:
        definition = rules.kinds.get(kind)
        if definition is None:
            return None
        if definition.sprite is not None:
            return definition.sprite
        if definition.parent is None:
            return None
        kind = definition.parent


def kind_is_a(
    kind: str,
    parent: str,
    *,
    rules: EntityRules,
) -> bool:
    """Return whether a kind is equal to or descends from another configured kind."""
    while True:
        if kind == parent:
            return True
        definition = rules.kinds.get(kind)
        if definition is None or definition.parent is None:
            return False
        kind = definition.parent


def _attrs_match(actual: Mapping[str, AttrValue], expected: Mapping[str, AttrValue]) -> bool:
    for attr, expected_value in expected.items():
        if attr not in actual:
            return False
        actual_value = actual[attr]
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            return False
    return True


def _rule_matches(
    rule: EntityRule, attrs: Mapping[str, AttrValue], meta: Mapping[str, AttrValue]
) -> bool:
    return (
        _attrs_match(attrs, rule.when)
        and _attrs_match(meta, rule.meta)
        and all(name not in attrs for name in rule.missing)
        and all(name not in meta for name in rule.missing_meta)
    )
