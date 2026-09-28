from pathlib import Path

import berries
from berries.game.campaigns import CampaignCatalog


def test_game_installation_builds_empty_catalog_without_personal_models(tmp_path: Path) -> None:
    (tmp_path / 'Mods').mkdir()

    catalog = berries.GameInstallation(tmp_path).load_catalog()

    assert isinstance(catalog, CampaignCatalog)
    assert catalog.campaigns == ()


def test_public_root_exports_only_deliberate_library_entry_points() -> None:
    assert berries.__all__ == [
        'Campaign',
        'CampaignCatalog',
        'ClassifiedEntity',
        'EntityRules',
        'GameBananaClient',
        'GameBananaSubmission',
        'GameInstallation',
        'Level',
        'LevelSide',
        'Map',
        'MapLayout',
        'MapPreview',
        'classify_map_entities',
        'load_map_layout',
    ]
