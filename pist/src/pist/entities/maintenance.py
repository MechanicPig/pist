"""Explicit maintenance of the shared entity-rule library."""

from pathlib import Path

from berries.entities.config import (
    SHARED_ENTITIES_PATH,
    SHARED_KINDS_PATH,
    load_entity_kinds,
    load_entity_rule_layer,
    load_entity_rules,
)
from berries.entities.rules import EntityKindLayer, EntityRuleLayer, EntityRules
from berries.entities.serialization import entity_kinds_toml, entity_rules_toml


class EntityRuleLibrary:
    """Publish shared rules and coordinate category-definition changes.

    This explicitly writable library never reads or modifies user-local overrides.
    File writes are sequential and do not roll back on failure.
    """

    def __init__(
        self,
        path: Path = SHARED_ENTITIES_PATH,
        *,
        kinds_path: Path | None = None,
    ) -> None:
        if kinds_path is None and path != SHARED_ENTITIES_PATH:
            raise ValueError('Specify kinds_path for a custom entity rules file.')
        self.path = path
        self.kinds_path = SHARED_KINDS_PATH if kinds_path is None else kinds_path

    def load(self) -> EntityRules:
        """Load shared categories and rules without local overrides."""
        return load_entity_rules(self.path, kinds_path=self.kinds_path)

    def save(self, rules: EntityRules) -> None:
        """Write shared definitions and rules sequentially without rollback or retry."""
        updates = {
            self.kinds_path: entity_kinds_toml(EntityKindLayer(kinds=rules.kinds)),
            self.path: entity_rules_toml(rules),
        }
        for path, text in updates.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')  # pyright: ignore[reportUnusedCallResult]

    def rename_kind(self, old_name: str, new_name: str, rules: EntityRules) -> None:
        """Persist a kind-ID rename across every rule layer this store owns."""
        if old_name == new_name:
            self.save(rules)
            return

        layer = self.load_layer().with_replaced_kind(old_name, new_name)
        candidate = EntityRules.from_layers(EntityKindLayer(kinds=rules.kinds), layer)
        self.save(candidate)

    def delete_kind(self, name: str, fallback_kind: str, rules: EntityRules) -> None:
        """Persist a kind deletion across every rule layer this store owns."""
        self.rename_kind(name, fallback_kind, rules)

    def load_layer(self) -> EntityRuleLayer:
        """Load the shared entity rules without kinds or user-local overrides."""
        return load_entity_rule_layer(self.path)

    def save_layer(self, layer: EntityRuleLayer) -> None:
        """Write audited shared rules without rollback or retry."""
        rules = EntityRules.from_layers(load_entity_kinds(self.kinds_path), layer)
        text = entity_rules_toml(rules)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding='utf-8')  # pyright: ignore[reportUnusedCallResult]
