from pathlib import Path
from zipfile import ZipFile

import pytest

import berries
from berries.entities import config
from berries.entities.rules import EntityKindLayer, EntityRuleLayer, EntityRules
from berries.game.campaigns import CampaignCatalog
from berries.game.mods import ModScanError, ModScanStage


def test_default_entity_rules_ignore_neighboring_user_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / 'shared.toml'
    kinds = tmp_path / 'kinds.toml'
    shared.write_text('[entities.Test]\nrules=[{kind="berry"}]', encoding='utf-8')
    kinds.write_text('[kinds.berry]\nlabel="Berry"', encoding='utf-8')
    (tmp_path / 'entities.toml').write_text('invalid local override', encoding='utf-8')
    monkeypatch.setattr(config, 'SHARED_ENTITIES_PATH', shared)
    monkeypatch.setattr(config, 'SHARED_KINDS_PATH', kinds)
    assert config.load_default_entity_rules().entity_kind('Test', {}) == 'berry'


def test_explicit_entity_rule_layer_uses_selected_packaged_kinds(tmp_path: Path) -> None:
    path = tmp_path / 'overrides.toml'
    baseline = config.load_default_entity_rules()
    assert baseline.kinds
    path.write_text(
        '[entities."Example/Test"]\nrules = [{ kind = "strawberry" }]\n', encoding='utf-8'
    )
    custom = EntityRules.from_layers(
        EntityKindLayer(kinds=baseline.kinds),
        EntityRuleLayer(entities=baseline.entities),
        config.load_entity_rule_layer(path),
    )
    assert custom.entity_kind('Example/Test', {}) == 'strawberry'


def test_high_level_catalog_exposes_structured_scan_errors(tmp_path: Path) -> None:
    mods = tmp_path / 'Mods'
    mods.mkdir()
    with ZipFile(mods / 'Example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Dialog/English.txt', b'\xff')
        archive.writestr('Maps/Example/Test.bin', b'')
    game = berries.GameInstallation(tmp_path)
    with pytest.raises(ModScanError):
        game.load_catalog()
    errors: list[ModScanError] = []
    catalog = game.load_catalog(on_scan_error=errors.append)
    assert len(errors) == 1
    assert errors[0].stage is ModScanStage.DIALOG
    assert errors[0].path == mods / 'Example.zip'
    assert len(catalog.campaigns) == 1


def test_game_installation_builds_empty_catalog_without_personal_models(tmp_path: Path) -> None:
    (tmp_path / 'Mods').mkdir()

    catalog = berries.GameInstallation(tmp_path).load_catalog()

    assert isinstance(catalog, CampaignCatalog)
    assert catalog.campaigns == ()


def test_vanilla_installation_loads_without_a_mods_directory(tmp_path: Path) -> None:
    maps = tmp_path / 'Content' / 'Maps'
    maps.mkdir(parents=True)
    (maps / '1-ForsakenCity.bin').touch()
    game = berries.GameInstallation(tmp_path)
    catalog = game.load_catalog()
    assert catalog.campaigns[0].levels[0].sid == 'Celeste/1-ForsakenCity'
    assert not (tmp_path / 'Mods').exists()
    with pytest.raises(ValueError):
        game.scan_mods()
    with pytest.raises(ValueError, match='Game directory does not exist'):
        berries.GameInstallation(tmp_path / 'Missing').load_catalog()


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
        'Localization',
        'Map',
        'MapKey',
        'MapLayout',
        'MapPreview',
        'ModMap',
        'VanillaMap',
        'classify_map_entities',
    ]
