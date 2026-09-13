"""Configurable classification rules for map entities."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Self, cast

import tomli_w
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pist.game.binmap import AttrValue, BinElement, BinMap, parse_map_bin


class EntityStat(StrEnum):
    """How map data summarizes entities belonging to one kind."""

    NONE = 'none'
    COUNT = 'count'
    EXIST = 'exist'
    SELECT = 'select'


class EntityTableField(BaseModel):
    """One table-and-field destination for a configured entity summary."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    table: str
    field: str

    @field_validator('table', 'field')
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Require a non-empty human-readable destination component."""
        if not (name := value.strip()):
            raise ValueError('Table and field names must not be empty.')
        return name


class HeartStatus(StrEnum):
    """How a map's configured crystal-heart entities end the level."""

    NONE = 'none'
    COMPLETES_LEVEL = 'completes_level'
    EXTRA = 'extra'
    NEEDS_REVIEW = 'needs_review'


SHARED_ENTITIES_PATH = Path(__file__).parent.parent / 'data' / 'entities.toml'
SHARED_KINDS_PATH = Path(__file__).parent.parent / 'data' / 'kinds.toml'
LOCAL_ENTITIES_PATH = Path('.pist/entities.toml')


class EntityKind(BaseModel):
    """One user-extensible entity kind in the classification tree."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    label: str
    parent: str | None = None
    stat: EntityStat = EntityStat.NONE
    table_field: EntityTableField | None = None
    select_value: str | None = None
    sprite: str | None = None

    @model_validator(mode='after')
    def validate_table_field(self) -> EntityKind:
        """Keep external table destinations meaningful and type-directed."""
        if self.stat is EntityStat.NONE and self.table_field is not None:
            raise ValueError('A table field requires stat = "count" or "exist".')
        return self

    @field_validator('select_value')
    @classmethod
    def validate_select_value(cls, value: str | None) -> str | None:
        """Keep configured single-select values non-empty and whitespace-normalized."""
        if value is None:
            return None
        if not (value := value.strip()):
            raise ValueError('A select value must not be empty.')
        return value

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


class EntityRule(BaseModel):
    """One ordered entity rule whose optional kind is its terminal outcome."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: str | None = None
    when: dict[str, AttrValue] = Field(default_factory=dict)
    meta: dict[str, AttrValue] = Field(default_factory=dict)

    @property
    def identity(self) -> tuple[tuple[tuple[str, AttrValue], ...], ...]:
        """Return the condition that identifies one local override."""
        return tuple(sorted(self.when.items())), tuple(sorted(self.meta.items()))


class EntityRulesForId(BaseModel):
    """Ordered classification rules for one exact entity ID."""

    model_config = ConfigDict(extra='forbid', frozen=True)

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
    when: tuple[tuple[str, AttrValue], ...]
    meta: tuple[tuple[str, AttrValue], ...]
    shared_kind: str | None
    local_kind: str | None


class EntityRuleLayer(BaseModel):
    """One independently valid shared or local entity-rule TOML layer."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kinds: dict[str, EntityKind] = Field(default_factory=dict)
    entities: dict[str, EntityRulesForId] = Field(default_factory=dict)

    def merged(self, local: Self) -> Self:
        """Apply a local layer to this layer without cross-layer validation."""
        kinds = self.kinds.copy()
        for name, local_kind in local.kinds.items():
            shared_kind = kinds.get(name)
            kinds[name] = (
                local_kind
                if shared_kind is None
                else EntityKind.model_validate(
                    shared_kind.model_dump()
                    | local_kind.model_dump(include=local_kind.model_fields_set)
                )
            )
        entities = self.entities.copy()
        for name, local_rules in local.entities.items():
            shared_rules = entities.get(name)
            entities[name] = (
                local_rules if shared_rules is None else shared_rules.merged(local_rules)
            )
        return type(self).model_validate({'kinds': kinds, 'entities': entities})

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
                            local_rule.identity[0],
                            local_rule.identity[1],
                            shared_rule.kind,
                            local_rule.kind,
                        )
                    )
        return tuple(conflicts)

    def local_overrides(self, baseline: EntityRuleLayer | None) -> EntityRuleLayer:
        """Return the smallest layer that reproduces this layer over a baseline."""
        kinds = {
            name: kind
            for name, kind in self.kinds.items()
            if baseline is None or baseline.kinds.get(name) != kind
        }
        entities: dict[str, EntityRulesForId] = {}
        for name, entity_rules in self.entities.items():
            overrides = entity_rules.overrides(
                None if baseline is None else baseline.entities.get(name)
            )
            if overrides is not None:
                entities[name] = overrides
        return EntityRuleLayer(kinds=kinds, entities=entities)

    def renamed_kind(self, old_name: str, new_name: str) -> Self:
        """Return this independent layer with references to one kind renamed.

        Layers intentionally permit incomplete references, so this only rewrites
        names. The merged :class:`EntityRules` validates the resulting hierarchy.
        """
        if old_name == new_name:
            return self
        kinds: dict[str, EntityKind] = {}
        for name, definition in self.kinds.items():
            target_name = new_name if name == old_name else name
            if target_name in kinds:
                raise ValueError(f'Entity kind already exists: {new_name!r}')
            kinds[target_name] = definition.model_copy(
                update={'parent': new_name if definition.parent == old_name else definition.parent}
            )
        entities = {
            entity_name: EntityRulesForId(
                rules=tuple(
                    rule.model_copy(
                        update={'kind': new_name if rule.kind == old_name else rule.kind}
                    )
                    for rule in entity_rules.rules
                )
            )
            for entity_name, entity_rules in self.entities.items()
        }
        return type(self)(kinds=kinds, entities=entities)

    def without_kind(self, name: str, fallback_kind: str) -> Self:
        """Remove one kind while moving its layer-local references to its parent."""
        kinds = {
            kind_name: definition.model_copy(
                update={'parent': fallback_kind if definition.parent == name else definition.parent}
            )
            for kind_name, definition in self.kinds.items()
            if kind_name != name
        }
        entities = {
            entity_name: EntityRulesForId(
                rules=tuple(
                    rule.model_copy(
                        update={
                            'kind': fallback_kind if rule.kind == name else rule.kind,
                        }
                    )
                    for rule in entity_rules.rules
                )
            )
            for entity_name, entity_rules in self.entities.items()
        }
        return type(self)(kinds=kinds, entities=entities)


class EntityRules(EntityRuleLayer):
    """Validated entity kinds and rules ready for classification."""

    @classmethod
    def from_layers(cls, *layers: EntityRuleLayer) -> Self:
        """Merge independently valid TOML layers, then validate their references."""
        if not layers:
            return cls()
        merged = layers[0]
        for layer in layers[1:]:
            merged = merged.merged(layer)
        return cls.model_validate(merged.model_dump())

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
            new_rule if rule.when == new_rule.when and rule.meta == new_rule.meta else rule
            for rule in entity_rules.rules
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
            new_rule if rule.when == new_rule.when and rule.meta == new_rule.meta else rule
            for rule in entity_rules.rules
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
        renamed_layer = EntityRuleLayer.renamed_kind(self, old_name, new_name)
        renamed = type(self).model_validate(renamed_layer.model_dump())
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
        layer = EntityRuleLayer.without_kind(self, name, definition.parent)
        return type(self).model_validate(layer.model_dump())

    def direct_rule_count(self, kind: str) -> int:
        """Return the number of effective entity rules that directly choose one kind."""
        return sum(
            rule.kind == kind
            for entity_rules in self.entities.values()
            for rule in entity_rules.rules
        )


@dataclass(frozen=True, slots=True)
class ClassifiedEntity:
    """One classified map entity, with enough data to trace its source."""

    room: str
    name: str
    entity_id: int | None
    x: int | float | None
    y: int | float | None
    attrs: dict[str, AttrValue]
    kind: str
    sprite: str | None


@dataclass(frozen=True, slots=True)
class MapEntityStats:
    """Configured count and existence summaries, grouped by their owning kind."""

    counted: dict[str, tuple[ClassifiedEntity, ...]]
    existing: dict[str, tuple[ClassifiedEntity, ...]]
    selected: dict[str, tuple[ClassifiedEntity, ...]] = field(default_factory=dict)
    table_fields: dict[str, EntityTableField] = field(default_factory=dict)
    stat_types: dict[str, EntityStat] = field(default_factory=dict)
    select_values: dict[str, frozenset[str]] = field(default_factory=dict)

    def count(self, kind: str) -> int:
        """Return the number of counted entities for one configured kind."""
        return len(self.counted.get(kind, ()))

    def exists(self, kind: str) -> bool:
        """Return whether at least one configured entity exists for one configured kind."""
        return bool(self.existing.get(kind, ()))

    @property
    def table_values(self) -> dict[str, dict[str, int | bool | str]]:
        """Return configured output values as ``table -> field -> scalar``.

        ``count`` owners emit :class:`int`; ``exist`` owners emit :class:`bool`;
        ``select`` owners emit a string only when all their entities agree.
        Kinds without a ``table_field`` remain available for preview and analysis,
        but intentionally do not produce an external-table value.
        """
        values: dict[str, dict[str, int | bool | str]] = {}
        for kind, target in self.table_fields.items():
            match self.stat_types.get(kind):
                case EntityStat.COUNT:
                    values.setdefault(target.table, {})[target.field] = self.count(kind)
                case EntityStat.EXIST:
                    values.setdefault(target.table, {})[target.field] = self.exists(kind)
                case EntityStat.SELECT:
                    select_values = self.select_values.get(kind, frozenset())
                    if len(select_values) == 1:
                        values.setdefault(target.table, {})[target.field] = next(
                            iter(select_values)
                        )
                case EntityStat.NONE:
                    pass
        return values


@dataclass(frozen=True, slots=True)
class MapEntities:
    """Classified entities extracted from one decoded map."""

    entities: tuple[ClassifiedEntity, ...]
    stats: MapEntityStats
    special: dict[str, tuple[ClassifiedEntity, ...]]
    heart_status: HeartStatus

    @property
    def strawberries(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities counted as regular strawberries."""
        return self.stats.counted.get('strawberry', ())

    @property
    def moonberries(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities counted as moonberries or their descendants."""
        return self.stats.counted.get('moonberry', ())

    @property
    def cassettes(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities contributing to cassette existence."""
        return self.stats.existing.get('cassette', ())

    @property
    def hearts(self) -> tuple[ClassifiedEntity, ...]:
        """Return entities contributing to the configured heart summary."""
        return self.stats.selected.get('heart', self.stats.existing.get('heart', ()))

    @property
    def has_cassette(self) -> bool:
        """Return whether this map contains at least one configured cassette entity."""
        return bool(self.cassettes)


def load_entity_rules(path: Path) -> EntityRules:
    """Load entity kinds and rules from a TOML configuration file."""
    try:
        layer = EntityRuleLayer.model_validate(_load_toml(path))
        if path.resolve() == SHARED_ENTITIES_PATH.resolve():
            kinds = EntityRuleLayer.model_validate(_load_toml(SHARED_KINDS_PATH))
            return EntityRules.from_layers(kinds, layer)
        return EntityRules.model_validate(layer.model_dump())
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(f'Invalid entity rules config: {path!r}') from error


def load_entity_rule_layers(
    shared_path: Path = SHARED_ENTITIES_PATH,
    local_path: Path = LOCAL_ENTITIES_PATH,
    *,
    kinds_path: Path | None = None,
) -> EntityRules:
    """Load shared and optional local rule layers in priority order."""
    if (shared_path, local_path) == (SHARED_ENTITIES_PATH, LOCAL_ENTITIES_PATH) and kinds_path is None:
        kinds_path = SHARED_KINDS_PATH
    rules, _ = _load_entity_rule_layers(shared_path, kinds_path, local_path)
    return rules


def _load_entity_rule_layers(
    shared_path: Path,
    kinds_path: Path | None,
    local_path: Path,
) -> tuple[EntityRules, tuple[EntityRuleConflict, ...]]:
    try:
        shared = EntityRuleLayer.model_validate(_load_toml(shared_path))
        kinds = (
            EntityRuleLayer.model_validate(_load_toml(kinds_path))
            if kinds_path is not None
            else EntityRuleLayer()
        )
        local = (
            EntityRuleLayer.model_validate(_load_toml(local_path))
            if local_path.is_file()
            else EntityRuleLayer()
        )
        baseline = EntityRules.from_layers(kinds, shared)
        return EntityRules.from_layers(baseline, local), baseline.conflicts(local)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValidationError, ValueError) as error:
        raise ValueError(
            f'Invalid entity rules config layers: '
            f'{kinds_path!r}, {shared_path!r}, {local_path!r}'
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
            self._kinds_path: Path | None = None
            self._local_path: Path | None = None
        else:
            self._shared_path = shared_path
            self._kinds_path = (
                SHARED_KINDS_PATH
                if kinds_path is None
                and (shared_path, local_path) == (SHARED_ENTITIES_PATH, LOCAL_ENTITIES_PATH)
                else kinds_path
            )
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
            if self._kinds_path is None:
                return load_entity_rules(self._shared_path)
            return EntityRules.from_layers(
                EntityRuleLayer.model_validate(_load_toml(self._kinds_path)),
                EntityRuleLayer.model_validate(_load_toml(self._shared_path)),
            )
        rules, self._conflicts = _load_entity_rule_layers(
            self._shared_path, self._kinds_path, self._local_path
        )
        return rules

    def save(self, rules: EntityRules) -> None:
        if self._kinds_path is not None:
            current_kinds = EntityRuleLayer.model_validate(_load_toml(self._kinds_path))
            if current_kinds.kinds != rules.kinds:
                self._kinds_path.parent.mkdir(parents=True, exist_ok=True)
                self._kinds_path.write_text(
                    entity_rules_toml(EntityRuleLayer(kinds=rules.kinds)), encoding='utf-8'
                )
        baseline = (
            (
                EntityRules.from_layers(
                    EntityRuleLayer.model_validate(_load_toml(self._kinds_path))
                )
                if self._kinds_path is not None
                else None
            )
            if self._local_path is None
            else EntityRules.from_layers(
                EntityRuleLayer.model_validate(_load_toml(self._kinds_path))
                if self._kinds_path is not None
                else EntityRuleLayer(),
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
        if self._kinds_path is None:
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
            (path, layer.renamed_kind(old_name, new_name)) for path, layer in paths_and_layers
        ]
        kind_layer = EntityRuleLayer(kinds=rules.kinds)
        # Validate all writes together before modifying any user-visible file.
        layers = [kind_layer, *(layer for _, layer in renamed_layers)]
        EntityRules.from_layers(*layers)

        self._kinds_path.parent.mkdir(parents=True, exist_ok=True)
        self._kinds_path.write_text(entity_rules_toml(kind_layer), encoding='utf-8')
        for path, layer in renamed_layers:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entity_rules_toml(layer), encoding='utf-8')

    def delete_kind(self, name: str, fallback_kind: str, rules: EntityRules) -> None:
        """Persist a kind deletion across every rule layer this store owns."""
        if self._kinds_path is None:
            self.save(rules)
            return
        paths_and_layers: list[tuple[Path, EntityRuleLayer]] = [
            (self._shared_path, EntityRuleLayer.model_validate(_load_toml(self._shared_path))),
        ]
        if self._local_path is not None and self._local_path.is_file():
            paths_and_layers.append(
                (self._local_path, EntityRuleLayer.model_validate(_load_toml(self._local_path)))
            )
        updated_layers = [
            (path, layer.without_kind(name, fallback_kind)) for path, layer in paths_and_layers
        ]
        kind_layer = EntityRuleLayer(kinds=rules.kinds)
        EntityRules.from_layers(kind_layer, *(layer for _, layer in updated_layers))

        self._kinds_path.parent.mkdir(parents=True, exist_ok=True)
        self._kinds_path.write_text(entity_rules_toml(kind_layer), encoding='utf-8')
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
    return tuple(sorted(rules, key=lambda rule: len(rule.when) + len(rule.meta), reverse=True))


def entity_rules_toml(rules: EntityRuleLayer, *, baseline: EntityRules | None = None) -> str:
    """Serialize validated entity rules in pist's canonical TOML layout."""
    layer = rules if baseline is None else rules.local_overrides(baseline)
    return tomli_w.dumps(layer.model_dump(mode='json', exclude_none=True, exclude_defaults=True))


DEFAULT_ENTITY_RULES = load_entity_rule_layers()


def entity_kind(
    name: str,
    attrs: Mapping[str, AttrValue],
    *,
    meta: Mapping[str, AttrValue] | None = None,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> str | None:
    """Return the first configured outcome matching entity attributes and map metadata."""
    entity_rules = rules.entities.get(name)
    if entity_rules is None:
        return None
    for rule in entity_rules.rules:
        if _attrs_match(attrs, rule.when) and _attrs_match({} if meta is None else meta, rule.meta):
            return rule.kind
    return None


def stat_owner(
    kind: str,
    *,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> tuple[str, EntityStat, EntityTableField | None] | None:
    """Return the closest summary-owning ancestor of a kind, if any."""
    while True:
        definition = rules.kinds.get(kind)
        if definition is None:
            return None
        if definition.stat is not EntityStat.NONE:
            return kind, definition.stat, definition.table_field
        if definition.parent is None:
            return None
        kind = definition.parent


def kind_select_value(
    kind: str,
    *,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
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
    rules: EntityRules = DEFAULT_ENTITY_RULES,
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
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> bool:
    """Return whether a kind is equal to or descends from another configured kind."""
    while True:
        if kind == parent:
            return True
        definition = rules.kinds.get(kind)
        if definition is None or definition.parent is None:
            return False
        kind = definition.parent


def analyze_map_entities(
    map_data: BinMap,
    *,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> MapEntities:
    """Classify configured entities in all rooms of a decoded map."""
    metadata = map_mode_metadata(map_data.root)
    entities: list[ClassifiedEntity] = []
    counted: dict[str, list[ClassifiedEntity]] = {}
    existing: dict[str, list[ClassifiedEntity]] = {}
    selected: dict[str, list[ClassifiedEntity]] = {}
    select_values: dict[str, set[str]] = {}
    table_fields = {
        name: kind.table_field for name, kind in rules.kinds.items() if kind.table_field is not None
    }
    stat_types = {
        name: kind.stat for name, kind in rules.kinds.items() if kind.table_field is not None
    }
    special: dict[str, list[ClassifiedEntity]] = {}
    for room in _map_rooms(map_data.root):
        for entity in _room_entities(room):
            kind = entity_kind(entity.name, entity.attrs, meta=metadata, rules=rules)
            if kind is None:
                continue
            occurrence = _classified_entity(room, entity, kind, kind_sprite(kind, rules=rules))
            entities.append(occurrence)
            match stat_owner(kind, rules=rules):
                case stat_kind, EntityStat.COUNT, _:
                    counted.setdefault(stat_kind, []).append(occurrence)
                case stat_kind, EntityStat.EXIST, _:
                    existing.setdefault(stat_kind, []).append(occurrence)
                case stat_kind, EntityStat.SELECT, _:
                    selected.setdefault(stat_kind, []).append(occurrence)
                    if (value := kind_select_value(kind, rules=rules)) is not None:
                        select_values.setdefault(stat_kind, set()).add(value)
            if kind_is_a(kind, 'special', rules=rules):
                special.setdefault(kind, []).append(occurrence)
    return MapEntities(
        entities=tuple(entities),
        stats=MapEntityStats(
            counted={kind: tuple(values) for kind, values in counted.items()},
            existing={kind: tuple(values) for kind, values in existing.items()},
            selected={kind: tuple(values) for kind, values in selected.items()},
            table_fields=table_fields,
            stat_types=stat_types,
            select_values={kind: frozenset(values) for kind, values in select_values.items()},
        ),
        special={kind: tuple(entities) for kind, entities in special.items()},
        heart_status=_heart_status(selected.get('heart', existing.get('heart', []))),
    )


def analyze_map_bin(
    data: bytes,
    *,
    rules: EntityRules = DEFAULT_ENTITY_RULES,
) -> MapEntities:
    """Decode and classify configured entities in one map BIN file."""
    return analyze_map_entities(parse_map_bin(data), rules=rules)


def _map_rooms(root: BinElement) -> tuple[BinElement, ...]:
    levels = next((child for child in root.children if child.name == 'levels'), None)
    return (
        () if levels is None else tuple(child for child in levels.children if child.name == 'level')
    )


def map_mode_metadata(root: BinElement) -> Mapping[str, AttrValue]:
    """Return the active mode metadata used by Game entity behavior.

    The root ``meta`` element configures the area's presentation. Everest's
    ``Session.MapData.Meta`` instead comes from its nested singular ``mode``
    element, whose fields include behavior switches such as ``HeartIsEnd``.
    Rules must use the latter so static classification agrees with runtime.
    """
    metadata = next((child for child in root.children if child.name == 'meta'), None)
    if metadata is None:
        return {}
    mode = next((child for child in metadata.children if child.name == 'mode'), None)
    return {} if mode is None else mode.attrs


def _room_entities(room: BinElement) -> tuple[BinElement, ...]:
    entities = next((child for child in room.children if child.name == 'entities'), None)
    return () if entities is None else entities.children


def _classified_entity(
    room: BinElement, entity: BinElement, kind: str, sprite: str | None
) -> ClassifiedEntity:
    entity_id = entity.attrs.get('id')
    x = entity.attrs.get('x')
    y = entity.attrs.get('y')
    return ClassifiedEntity(
        room=_str_attr(room.attrs.get('name')),
        name=entity.name,
        entity_id=_int_attr(entity_id),
        x=_number_attr(x),
        y=_number_attr(y),
        attrs=entity.attrs.copy(),
        kind=kind,
        sprite=sprite,
    )


def _heart_status(hearts: list[ClassifiedEntity]) -> HeartStatus:
    if not hearts:
        return HeartStatus.NONE
    if all(heart.kind == 'end_level_heart' for heart in hearts):
        return HeartStatus.COMPLETES_LEVEL
    if all(heart.kind == 'keep_going_heart' for heart in hearts):
        return HeartStatus.EXTRA
    return HeartStatus.NEEDS_REVIEW


def _str_attr(value: AttrValue | None) -> str:
    return value if isinstance(value, str) else ''


def _int_attr(value: AttrValue | None) -> int | None:
    return cast(int, value) if type(value) is int else None


def _number_attr(value: AttrValue | None) -> int | float | None:
    return cast(int | float, value) if type(value) in {int, float} else None


def _attrs_match(actual: Mapping[str, AttrValue], expected: Mapping[str, AttrValue]) -> bool:
    for attr, expected_value in expected.items():
        if attr not in actual:
            return False
        actual_value = actual[attr]
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            return False
    return True
