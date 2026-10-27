"""Load shared entity rules and persist user-local rule overrides."""

from pathlib import Path

from berries.entities.config import (
    SHARED_ENTITIES_PATH,
    SHARED_KINDS_PATH,
    load_entity_kinds,
    load_entity_rule_layer,
    load_entity_rules,
)
from berries.entities.rules import EntityRuleConflict, EntityRuleLayer, EntityRules
from berries.entities.serialization import entity_rules_toml
from pist.paths import PIST_DIR

LOCAL_ENTITIES_PATH = PIST_DIR / 'entities.toml'


def load_entity_rule_layers(
    shared_path: Path = SHARED_ENTITIES_PATH,
    local_path: Path = LOCAL_ENTITIES_PATH,
    *,
    kinds_path: Path | None = None,
) -> EntityRules:
    """Load entity rules with local rules taking precedence over shared rules.

    Defaults read packaged categories/rules and the user's writable local rules.
    The local file may be absent; when present, it is read even without explicit
    opt-in. Berries itself loads packaged rules only.
    Custom layer paths require a separate shared ``kinds_path``. Categories come
    only from that shared file; local category definitions are not supported.
    """
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
        shared = load_entity_rule_layer(shared_path)
        kinds = load_entity_kinds(kinds_path)
        local = load_entity_rule_layer(local_path) if local_path.is_file() else EntityRuleLayer()
        return EntityRules.from_layers(kinds, shared, local), shared.conflicts(local)
    except (OSError, ValueError) as error:
        raise ValueError(
            f'Invalid entity rules config layers: {kinds_path!r}, {shared_path!r}, {local_path!r}'
        ) from error


class EntityConfigStore:
    """Read merged shared/local rules and write only user-local overrides.

    Category definitions are owned by the shared library and cannot be changed
    through this store. Publishing requires an explicit EntityRuleLibrary.
    """

    def __init__(
        self,
        path: Path = LOCAL_ENTITIES_PATH,
        *,
        shared_path: Path = SHARED_ENTITIES_PATH,
        kinds_path: Path | None = None,
    ) -> None:
        if kinds_path is None and shared_path != SHARED_ENTITIES_PATH:
            raise ValueError('Specify kinds_path for custom entity rule layers.')
        self.path = path
        self.shared_path = shared_path
        self.kinds_path = SHARED_KINDS_PATH if kinds_path is None else kinds_path
        if self.path.resolve() in (self.shared_path.resolve(), self.kinds_path.resolve()):
            raise ValueError('Local rule overrides must use a separate file from shared data.')
        self._conflicts: tuple[EntityRuleConflict, ...] = ()

    @property
    def conflicts(self) -> tuple[EntityRuleConflict, ...]:
        """Return local-over-shared overrides observed during the last load."""
        return self._conflicts

    def load(self) -> EntityRules:
        """Read the shared library and optional local rule overrides."""
        rules, self._conflicts = _load_entity_rule_layers(
            self.shared_path, self.kinds_path, self.path
        )
        return rules

    def save(self, rules: EntityRules) -> None:
        """Write local overrides without rollback, rejecting shared category changes."""
        baseline = load_entity_rules(self.shared_path, kinds_path=self.kinds_path)
        if baseline.kinds != rules.kinds:
            raise ValueError('Local kinds are disabled; category edits require shared maintenance.')
        text = entity_rules_toml(rules, baseline=baseline)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding='utf-8')  # pyright: ignore[reportUnusedCallResult]
