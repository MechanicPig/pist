"""Application policy for packaged and user-local map entrance rules."""

from pathlib import Path

from berries.map_entrances import (
    SHARED_MAP_ENTRANCES_PATH,
    MapEntranceRules,
    load_map_entrance_rules,
)

from .paths import PIST_DIR

LOCAL_MAP_ENTRANCES_PATH = PIST_DIR / 'map_entrances.toml'


def load_map_entrance_rule_layers(
    shared_path: Path = SHARED_MAP_ENTRANCES_PATH,
    local_path: Path = LOCAL_MAP_ENTRANCES_PATH,
) -> MapEntranceRules:
    """Load packaged rules and optional local overrides; reject invalid local rules."""
    shared = load_map_entrance_rules(shared_path)
    if not local_path.is_file():
        return shared
    return shared.merged(load_map_entrance_rules(local_path))
