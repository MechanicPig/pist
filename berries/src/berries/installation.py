"""High-level, read-only access to one Celeste installation."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .game.campaigns import CampaignCatalog
from .game.dialog import DialogReadError
from .game.mods import ModScanError, ModScanner, ModScanProgress, ModScanReport
from .game.saves.directory import SavesDir


@dataclass(frozen=True, slots=True)
class GameInstallation:
    """Read game, Mod, and save files without modifying the installation.

    Catalog loads return new results, not live views or recursively immutable
    objects. Save-directory loads return new editable file groups without writeback.
    """

    path: Path

    def scan_mods(
        self,
        *,
        include_disabled: bool = False,
        read_dialogs: bool = True,
        on_error: Callable[[ModScanError], None] | None = None,
        on_progress: Callable[[ModScanProgress], None] | None = None,
    ) -> ModScanReport:
        """Scan game packages with explicit content scope and caller-controlled callbacks."""
        return ModScanner(self.path, on_error=on_error).scan(
            include_disabled=include_disabled,
            read_dialogs=read_dialogs,
            on_progress=on_progress,
        )

    def load_catalog(
        self,
        report: ModScanReport | None = None,
        *,
        on_scan_error: Callable[[ModScanError], None] | None = None,
        on_error: Callable[[DialogReadError], None] | None = None,
    ) -> CampaignCatalog:
        """Load campaigns; callbacks may collect read failures and permit partial content.

        Without callbacks, scan and original-Dialog failures propagate. A supplied
        scan report retains its caller-owned scan history and is not scanned again.
        """
        return CampaignCatalog.from_game_dir(
            self.path, scan_report=report, on_scan_error=on_scan_error, on_error=on_error
        )

    def saves(self) -> SavesDir:
        """Locate the save directory without loading or restoring any files."""
        return SavesDir.from_game_dir(self.path)
