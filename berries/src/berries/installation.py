"""High-level, read-only access to one Celeste installation."""

from dataclasses import dataclass
from pathlib import Path

from berries.entities.rules import LOCAL_ENTITIES_PATH, EntityRules, load_entity_rule_layers
from berries.game.campaigns import (
    CampaignCatalog,
    load_campaigns,
    load_vanilla_dialogs,
    load_vanilla_maps,
)
from berries.game.enders_blender import EndersBlenderReader
from berries.game.map_hiders import MapHiderRules
from berries.game.mods import ModScanner, ModScanReport
from berries.game.saves import SaveReader
from berries.map_entrances import (
    LOCAL_MAP_ENTRANCES_PATH,
    MapEntranceRules,
    load_map_entrance_rule_layers,
)


@dataclass(frozen=True, slots=True)
class GameInstallation:
    """Convenient public entry point for immutable game, Mod, and save reads."""

    path: Path

    def scan_mods(self) -> ModScanReport:
        """Scan the Mods directory using Everest's enabled-package rules."""
        return ModScanner(self.path).scan()

    def load_catalog(self, report: ModScanReport | None = None) -> CampaignCatalog:
        """Build the active campaign catalog from original and enabled Mod content."""
        report = self.scan_mods() if report is None else report
        return load_campaigns(
            report.mods,
            vanilla_maps=load_vanilla_maps(self.path),
            base_dialogs=load_vanilla_dialogs(self.path),
            map_hider_rules=MapHiderRules(self.path),
        )

    def saves(self) -> SaveReader:
        """Return a reader for native and Everest save slots."""
        return SaveReader(self.path)

    def enders_blender_saves(self) -> EndersBlenderReader:
        """Return a reader for Ender's Blender first-clear data."""
        return EndersBlenderReader(self.path)

    @staticmethod
    def entity_rules(*, local_path: Path | None = None) -> EntityRules:
        """Load packaged entity rules plus an explicitly requested local override.

        Local overrides are validated against the installed package version and are
        intentionally not a cross-version compatibility format.
        """
        return load_entity_rule_layers(
            local_path=LOCAL_ENTITIES_PATH if local_path is None else local_path
        )

    @staticmethod
    def map_entrance_rules(*, local_path: Path | None = None) -> MapEntranceRules:
        """Load packaged map-entrance rules plus an optional local override."""
        return load_map_entrance_rule_layers(
            local_path=LOCAL_MAP_ENTRANCES_PATH if local_path is None else local_path
        )
