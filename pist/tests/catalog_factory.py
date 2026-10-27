"""Synthetic campaign snapshots for application tests without installation scanning."""

from collections.abc import Sequence
from pathlib import Path

from berries.game import campaigns
from berries.game.mods import InstalledMod, ModScanReport


def make_campaign_catalog(
    mods: Sequence[InstalledMod] = (),
    *,
    game_dir: Path | None = None,
) -> campaigns.CampaignCatalog:
    """Assemble supplied Mod facts, optionally including synthetic original-game files."""
    game_dir = game_dir or Path('SyntheticCeleste')
    return campaigns.CampaignCatalog.from_game_dir(
        game_dir,
        scan_report=ModScanReport(
            mods_dir=str(game_dir / 'Mods'), disabled_filenames=[], mods=list(mods)
        ),
    )
