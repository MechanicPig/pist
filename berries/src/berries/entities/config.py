"""Parse rule configurations and read explicitly chosen or packaged rule files."""

import tomllib
from pathlib import Path

from .rules import EntityKindLayer, EntityRuleLayer, EntityRules

SHARED_ENTITIES_PATH = Path(__file__).parents[1] / 'data' / 'entities.toml'
SHARED_KINDS_PATH = Path(__file__).parents[1] / 'data' / 'kinds.toml'


def parse_entity_rules(data: str, *, kinds: str) -> EntityRules:
    """Parse explicit rule and category TOML texts, without accessing any files."""
    return EntityRules.from_layers(
        EntityKindLayer.model_validate(tomllib.loads(kinds)),
        EntityRuleLayer.model_validate(tomllib.loads(data)),
    )


def load_entity_rule_layer(path: Path) -> EntityRuleLayer:
    """Read exactly one entity-rule layer; do not discover other files."""
    try:
        return EntityRuleLayer.model_validate(tomllib.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError) as error:
        raise ValueError(f'Invalid entity rules config: {path!r}') from error


def load_entity_kinds(path: Path) -> EntityKindLayer:
    """Read exactly one category configuration."""
    try:
        return EntityKindLayer.model_validate(tomllib.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError) as error:
        raise ValueError(f'Invalid entity kinds config: {path!r}') from error


def load_entity_rules(path: Path, *, kinds_path: Path) -> EntityRules:
    """Combine explicitly selected category and rule files, without local overrides."""
    try:
        return parse_entity_rules(
            path.read_text(encoding='utf-8'), kinds=kinds_path.read_text(encoding='utf-8')
        )
    except (OSError, ValueError) as error:
        raise ValueError(f'Invalid entity rules config: {path!r}, kinds: {kinds_path!r}') from error


def load_default_entity_rules() -> EntityRules:
    """Load only the rule and category files bundled with Berries."""
    return load_entity_rules(SHARED_ENTITIES_PATH, kinds_path=SHARED_KINDS_PATH)
