"""Exercise the public catalog loader with isolated, pre-read content facts."""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pytest import MonkeyPatch

from berries.game import vanilla
from berries.game.campaigns import CampaignCatalog
from berries.game.map_hiders import MapHiderRules
from berries.game.maps import Map
from berries.game.mods import InstalledMod, ModScanReport


def make_catalog(
    mods: Sequence[InstalledMod],
    *,
    vanilla_maps: Iterable[Map] = (),
    base_dialogs: Mapping[str, Mapping[str, str]] | None = None,
    map_hider_rules: MapHiderRules | None = None,
) -> CampaignCatalog:
    """Bypass physical scanning, not the public loading or assembly workflow."""
    game_dir = Path('SyntheticCeleste')
    report = ModScanReport(mods_dir=str(game_dir / 'Mods'), disabled_filenames=[], mods=list(mods))
    maps = tuple(vanilla_maps)
    with MonkeyPatch.context() as patch:
        patch.setattr(vanilla, 'load_maps', lambda _: maps)
        patch.setattr(vanilla, 'load_dialogs', lambda *args, **kwargs: base_dialogs or {})
        return CampaignCatalog.from_game_dir(
            game_dir, scan_report=report, map_hider_rules=map_hider_rules
        )
