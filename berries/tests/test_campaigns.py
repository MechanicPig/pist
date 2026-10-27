"""Tests for Everest-ordered campaign assembly."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from berries.game import vanilla
from berries.game.campaigns import (
    Campaign,
    CampaignCatalog,
    CollabHubCampaign,
    CollabSubCampaign,
    ModCampaign,
    ModLoadingIssue,
    ReservedCampaignIssue,
    VanillaCampaign,
    resolve_campaign_refs,
)
from berries.game.content import MAPS_DIR, ContentPath
from berries.game.dialog import DialogReadError
from berries.game.everest import Dependency, ModMetadata, Version
from berries.game.levels import Level, LevelSide
from berries.game.localization import Localization
from berries.game.map_hiders import MapHiderRules
from berries.game.maps import Map, ModMap, VanillaMap
from berries.game.mods import ModScanError, ModScanner, ZipMod
from test_support.catalog import make_catalog
from test_support.mod_factory import make_installed_mod


def _map(path: str) -> ContentPath:
    return ContentPath(path)


def test_catalog_binds_map_paths_to_their_installed_mod() -> None:
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[_map('Maps/Example/Map.bin'), _map('Maps/Example/Map-B.bin')],
    )
    catalog = make_catalog((mod,))
    maps = tuple(catalog.campaigns[0].iter_maps())
    assert [map_file.file_path for map_file in maps] == mod.map_files
    assert all(isinstance(map_file, ModMap) and map_file.mod is mod for map_file in maps)


def test_campaign_uses_mod_matches_physical_source_without_a_catalog() -> None:
    source = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    other = source.model_copy()
    campaign = Campaign(
        _map('Maps/Example'),
        (Level('Example/Map', (ModMap(_map('Maps/Example/Map.bin'), source),)),),
    )
    assert campaign.uses_mod(source)
    assert not campaign.uses_mod(other)
    assert not Campaign(_map('Maps/Example'), ()).uses_mod(source)
    original = VanillaCampaign(
        _map('Maps'),
        (Level('1-ForsakenCity', (VanillaMap(_map('Maps/1-ForsakenCity.bin'), Path('Content')),)),),
    )
    assert not original.uses_mod(source)


def test_catalog_loads_game_directory_with_overrides_hidden_maps_and_diagnostics(
    tmp_path: Path,
) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    maps_dir = tmp_path / 'Content' / 'Maps'
    maps_dir.mkdir(parents=True)
    (maps_dir / '0-Intro.bin').touch()
    dialog_dir = tmp_path / 'Content' / 'Dialog'
    dialog_dir.mkdir()
    (dialog_dir / 'English.txt').write_text('LEVELSET_CELESTE=Original\n', encoding='utf-8')
    with ZipFile(mods_dir / 'A.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: AltSidesHelper\n  Version: 1.0.0\n')
        archive.writestr('Maps/Hidden/Test.bin', b'')
        archive.writestr('Maps/Pack/Map.bin', b'')
        archive.writestr('Dialog/English.txt', 'levelset_Pack=Earlier\n')
    with ZipFile(mods_dir / 'B.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Later\n  Version: 1.0.0\n')
        archive.writestr('Maps/Pack/Map.bin', b'')
        archive.writestr('Dialog/English.txt', 'levelset_Pack=Later\n')
    with ZipFile(mods_dir / 'Hider.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: HelperTestMapHider\n  Version: 1.0.0\n')
    with ZipFile(mods_dir / 'Disabled.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Disabled\n  Version: 1.0.0\n')
        archive.writestr('Maps/Disabled/Map.bin', b'')
    (mods_dir / 'blacklist.txt').write_text('Disabled.zip\n', encoding='utf-8')
    with ZipFile(mods_dir / 'Broken.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Broken\n  Version: invalid\n')
    with ZipFile(mods_dir / 'Unavailable.zip', 'w') as archive:
        archive.writestr(
            'everest.yaml',
            '- Name: Unavailable\n  Dependencies:\n    - Name: Missing\n      Version: 1.0.0\n',
        )

    failures: list[ModScanError] = []
    report = ModScanner(tmp_path, on_error=failures.append).scan(include_disabled=True)
    catalog = CampaignCatalog.from_game_dir(tmp_path, scan_report=report)

    original, pack = catalog.campaigns
    assert isinstance(original, VanillaCampaign)
    assert Localization(catalog.dialogs).campaign_names(original) == {'en': 'Original'}
    assert Localization(catalog.dialogs, 'en').campaign_name(pack) == 'Later'
    pack_map = next(pack.iter_maps())
    assert isinstance(pack_map, ModMap)
    assert pack_map.mod.metadata_name == 'Later'
    assert [camp.ref for camp in catalog.hidden_campaigns] == ['Hidden']
    assert len(catalog.overrides) == 1
    assert isinstance(catalog.overrides[0].previous, ModMap)
    assert catalog.overrides[0].previous.mod.path == mods_dir / 'A.zip'
    assert [mod.metadata_name for mod in catalog.unloaded_mods] == ['Unavailable']
    assert any(error.path.name == 'Broken.zip' for error in failures)
    assert len(catalog.issues) == 1
    issue = catalog.issues[0]
    assert isinstance(issue, ModLoadingIssue)
    assert issue.metadata.name == 'Unavailable'
    assert issue.required[0].dependency.name == 'Missing'
    # Loading the catalog discovers paths without parsing the intentionally empty .bin files.
    for name in ('A.zip', 'B.zip', 'Hider.zip', 'Disabled.zip', 'Broken.zip', 'Unavailable.zip'):
        (mods_dir / name).unlink()


def test_catalog_reuses_scan_report_and_explicit_hiding_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'Example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Maps/Example/Map.bin', b'')
    report = ModScanner(tmp_path).scan()

    def unexpected_scan(self: ModScanner) -> None:
        pytest.fail('Reusing a completed report must not scan Mod packages again.')

    monkeypatch.setattr(ModScanner, 'scan', unexpected_scan)
    rules_path = tmp_path / 'custom.toml'
    rules_path.write_text(
        '[[rules]]\nid="custom"\nhider="Example"\nreader="static"\n'
        'target="source"\nmatch="exact"\nvalues=["Example"]\n',
        encoding='utf-8',
    )
    rules = MapHiderRules(tmp_path, rules_path=rules_path)
    catalog = CampaignCatalog.from_game_dir(tmp_path, scan_report=report, map_hider_rules=rules)
    assert catalog.campaigns == ()
    assert [camp.ref for camp in catalog.hidden_campaigns] == ['Example']
    with pytest.raises(ValueError, match='different game directory'):
        CampaignCatalog.from_game_dir(tmp_path / 'Other', scan_report=report)


def test_catalog_reports_original_dialog_failures_through_callback(tmp_path: Path) -> None:
    (tmp_path / 'Mods').mkdir()
    directory = tmp_path / 'Content' / 'Dialog'
    directory.mkdir(parents=True)
    (directory / 'English.txt').write_bytes(b'\xff')
    (directory / 'Japanese.txt').write_text('TITLE=Kept', encoding='utf-8')
    errors: list[DialogReadError] = []
    catalog = CampaignCatalog.from_game_dir(tmp_path, on_error=errors.append)
    assert catalog.dialogs['ja']['TITLE'] == 'Kept'
    assert len(errors) == 1
    assert 'English.txt' in str(errors[0])
    assert str(tmp_path / 'Content') in str(errors[0])
    with pytest.raises(DialogReadError):
        CampaignCatalog.from_game_dir(tmp_path)

    def abort(error: DialogReadError) -> None:
        raise RuntimeError('Caller declined partial Dialog results') from error

    with pytest.raises(RuntimeError, match='Caller declined'):
        CampaignCatalog.from_game_dir(tmp_path, on_error=abort)


def _loaded_maps(campaign: Campaign) -> tuple[Map, ...]:
    return tuple(campaign.iter_maps())


def _first_side(campaign: Campaign) -> tuple[Level, LevelSide]:
    return next(campaign.iter_sides())


def test_campaign_iter_maps_preserves_level_and_side_order() -> None:
    source = Path('Content')
    map_files = tuple(
        VanillaMap(_map(path), source)
        for path in (
            'Maps/Pack/First.bin',
            'Maps/Pack/First-B.bin',
            'Maps/Pack/First-C.bin',
            'Maps/Pack/Second.bin',
        )
    )
    campaign = ModCampaign(
        directory=MAPS_DIR / 'Pack',
        levels=(
            Level(sid='Pack/First', maps=map_files[:3]),
            Level(sid='Pack/Second', maps=map_files[3:]),
        ),
    )
    assert tuple(campaign.iter_maps()) == map_files
    assert all(actual is expected for actual, expected in zip(campaign.iter_maps(), map_files))
    assert tuple(ModCampaign(directory=MAPS_DIR, levels=()).iter_maps()) == ()


def test_campaigns_follow_required_dependency_content_order() -> None:
    delayed = make_installed_mod(
        source='zip',
        filename='A-Delayed.zip',
        path='A-Delayed.zip',
        metadata_name='Delayed',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Helper', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Map.bin')],
    )
    helper = make_installed_mod(
        source='zip',
        filename='Z-Helper.zip',
        path='Z-Helper.zip',
        metadata_name='Helper',
        metadata_version='1.0.0',
        maps=[_map('Maps/Pack/Map.bin')],
    )

    catalog = make_catalog((delayed, helper))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, ModMap)
    assert loaded_map.mod is delayed
    assert len(catalog.overrides) == 1
    assert isinstance(catalog.overrides[0].previous, ModMap)
    assert isinstance(catalog.overrides[0].replacement, ModMap)
    assert catalog.overrides[0].previous.mod is helper
    assert catalog.overrides[0].replacement.mod is delayed


def test_campaigns_omit_pkgs_with_unsatisfied_required_dependencies() -> None:
    unavailable = make_installed_mod(
        source='zip',
        filename='Unavailable.zip',
        path='Unavailable.zip',
        metadata_name='Unavailable',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Missing', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Unavailable.bin')],
    )

    catalog = make_catalog((unavailable,))

    assert catalog.campaigns == ()
    assert catalog.unloaded_mods == (unavailable,)
    assert len(catalog.issues) == 1
    issue = catalog.issues[0]
    assert isinstance(issue, ModLoadingIssue)
    assert issue.mod is unavailable
    assert issue.required[0].dependency.name == 'Missing'
    assert issue.required[0].loaded is None


def test_campaigns_report_a_present_incompatible_optional_dependency() -> None:
    delayed = make_installed_mod(
        source='zip',
        filename='A-Delayed.zip',
        path='A-Delayed.zip',
        metadata_name='Delayed',
        metadata_version='1.0.0',
        optional_dependencies=[Dependency(name='Optional', version=Version.parse('2.0.0'))],
        maps=[_map('Maps/Pack/Map.bin')],
    )
    optional = make_installed_mod(
        source='zip',
        filename='Z-Optional.zip',
        path='Z-Optional.zip',
        metadata_name='Optional',
        metadata_version='1.0.0',
    )

    catalog = make_catalog((delayed, optional))

    assert catalog.campaigns == ()
    assert catalog.unloaded_mods == (delayed,)
    assert len(catalog.issues) == 1
    issue = catalog.issues[0]
    assert isinstance(issue, ModLoadingIssue)
    assert issue.mod is delayed
    assert issue.optional[0].dependency.version == Version.parse('2.0.0')
    assert issue.optional[0].loaded is optional.manifest[0]


def test_campaigns_use_globally_merged_dialog_entries_for_active_maps() -> None:
    map_info = ContentPath('Maps/MapModifier/Example.bin')
    map_mod = make_installed_mod(
        source='zip',
        filename='MapModifier.zip',
        path='MapModifier.zip',
        metadata_name='MapModifier',
        metadata_version='1.0.0',
        maps=[map_info],
    )
    first_dialog = make_installed_mod(
        source='zip',
        filename='FirstDialog.zip',
        path='FirstDialog.zip',
        metadata_name='FirstDialog',
        metadata_version='1.0.0',
        dialogs={
            'en': {
                'mapmodifier_example': 'First name',
                'mapmodifier_example_author': 'First author',
                'mapmodifier_example_collabcreditstags': 'Beginner',
                'mapmodifier': 'First campaign',
            }
        },
    )
    later_dialog = make_installed_mod(
        source='zip',
        filename='LaterDialog.zip',
        path='LaterDialog.zip',
        metadata_name='LaterDialog',
        metadata_version='1.0.0',
        dialogs={
            'en': {
                'MAPMODIFIER_EXAMPLE': 'Final name',
                'MapModifier': 'Final campaign',
                'levelset_MapModifier': 'Prefixed campaign',
            }
        },
    )

    catalog = make_catalog((map_mod, first_dialog, later_dialog))

    level, _ = _first_side(catalog.campaigns[0])
    assert Localization(catalog.dialogs).level_names(level) == {'en': 'Final name'}
    assert Localization(catalog.dialogs).translations(f'{level.dialog_key}_author') == {
        'en': 'First author'
    }
    assert Localization(catalog.dialogs).translations(f'{level.dialog_key}_collabcreditstags') == {
        'en': 'Beginner'
    }
    assert Localization(catalog.dialogs).campaign_names(catalog.campaigns[0]) == {
        'en': 'Prefixed campaign'
    }


def test_campaigns_ignore_dialogs_from_unloaded_pkgs() -> None:
    map_info = ContentPath('Maps/MapModifier/Example.bin')
    map_mod = make_installed_mod(
        source='zip',
        filename='MapModifier.zip',
        path='MapModifier.zip',
        metadata_name='MapModifier',
        metadata_version='1.0.0',
        maps=[map_info],
    )
    unavailable_dialog = make_installed_mod(
        source='zip',
        filename='UnavailableDialog.zip',
        path='UnavailableDialog.zip',
        metadata_name='UnavailableDialog',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Missing', version=Version.parse('1.0.0'))],
        dialogs={'en': {'MapModifier_Example': 'Unavailable name', 'Unavailable_Only': 'Hidden'}},
    )

    catalog = make_catalog((map_mod, unavailable_dialog))

    level, _ = _first_side(catalog.campaigns[0])
    assert Localization(catalog.dialogs).level_names(level) == {}
    assert 'MapModifier_Example' not in catalog.dialogs.get('en', {})
    assert 'Unavailable_Only' not in catalog.dialogs.get('en', {})
    base_catalog = make_catalog(
        (map_mod, unavailable_dialog), base_dialogs={'en': {'MapModifier_Example': 'Base name'}}
    )
    assert Localization(base_catalog.dialogs).level_names(level) == {'en': 'Base name'}


def test_campaigns_use_base_dialog_entries_before_loaded_mod_entries() -> None:
    map_info = ContentPath('Maps/MapModifier/Example.bin')
    map_mod = make_installed_mod(
        source='zip',
        filename='MapModifier.zip',
        path='MapModifier.zip',
        metadata_name='MapModifier',
        metadata_version='1.0.0',
        maps=[map_info],
    )
    override_mod = make_installed_mod(
        source='zip',
        filename='Override.zip',
        path='Override.zip',
        metadata_name='Override',
        metadata_version='1.0.0',
        dialogs={'en': {'MapModifier_Example': 'Mod name'}},
    )

    base_catalog = make_catalog(
        (map_mod,),
        base_dialogs={'en': {'MapModifier_Example': 'Base name'}},
    )
    catalog = make_catalog(
        (map_mod, override_mod),
        base_dialogs={'en': {'MapModifier_Example': 'Base name'}},
    )

    base_level, _ = _first_side(base_catalog.campaigns[0])
    assert Localization(base_catalog.dialogs).level_names(base_level) == {'en': 'Base name'}
    level, _ = _first_side(catalog.campaigns[0])
    assert Localization(catalog.dialogs).level_names(level) == {'en': 'Mod name'}


def test_campaigns_separate_maps_hidden_by_an_active_helper_mod(tmp_path: Path) -> None:
    hidden_map = _map('Maps/Hidden/Test.bin')
    helper_map = make_installed_mod(
        source='zip',
        filename='HelperMap.zip',
        path='HelperMap.zip',
        metadata_name='AltSidesHelper',
        metadata_version='1.0.0',
        maps=[hidden_map],
    )
    hider = make_installed_mod(
        source='zip',
        filename='HelperTestMapHider.zip',
        path='HelperTestMapHider.zip',
        metadata_name='HelperTestMapHider',
        metadata_version='1.0.0',
    )

    catalog = make_catalog(
        (helper_map, hider),
        map_hider_rules=MapHiderRules(tmp_path),
    )

    assert catalog.campaigns == ()
    assert [campaign.directory.as_posix() for campaign in catalog.hidden_campaigns] == [
        'Maps/Hidden'
    ]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[0])] == [hidden_map]


def test_campaigns_keep_helper_maps_visible_without_the_hiding_mod(tmp_path: Path) -> None:
    hidden_map = _map('Maps/Hidden/Test.bin')
    helper_map = make_installed_mod(
        source='zip',
        filename='HelperMap.zip',
        path='HelperMap.zip',
        metadata_name='AltSidesHelper',
        metadata_version='1.0.0',
        maps=[hidden_map],
    )

    catalog = make_catalog(
        (helper_map,),
        map_hider_rules=MapHiderRules(tmp_path),
    )

    assert catalog.hidden_campaigns == ()
    assert [item.file_path for item in _loaded_maps(catalog.campaigns[0])] == [hidden_map]


def test_campaigns_separate_unrepresented_top_level_collab_groups() -> None:
    lobby = _map('Maps/Example/0-Lobbies/1-Maps.bin')
    nested_lobby = _map('Maps/Example/0-Lobbies/1-Maps/Nested.bin')
    visible_map = _map('Maps/Example/1-Maps/Visible.bin')
    nested_visible_map = _map('Maps/Example/1-Maps/Nested/Visible.bin')
    hidden_map = _map('Maps/Example/1-Submissions/Hidden.bin')
    collab = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby, nested_lobby, visible_map, nested_visible_map, hidden_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )

    catalog = make_catalog((collab, collab_utils))

    campaign = catalog.campaigns[0]
    assert campaign.directory.as_posix() == 'Maps/Example/0-Lobbies'
    assert [item.file_path for item in _loaded_maps(campaign)] == [lobby]
    assert [
        hidden_campaign.directory.as_posix() for hidden_campaign in catalog.hidden_campaigns
    ] == [
        'Maps/Example/0-Lobbies/1-Maps',
        'Maps/Example/1-Maps',
        'Maps/Example/1-Maps/Nested',
        'Maps/Example/1-Submissions',
    ]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[0])] == [nested_lobby]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[1])] == [visible_map]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[2])] == [
        nested_visible_map
    ]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[3])] == [hidden_map]


def test_campaigns_do_not_casefold_collab_virtual_map_directories() -> None:
    map_info = _map('Maps/example/1-Easy/Map.bin')
    collab = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[map_info],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )

    catalog = make_catalog((collab, collab_utils))

    assert [campaign.directory.as_posix() for campaign in catalog.campaigns] == [
        'Maps/example/1-Easy'
    ]
    assert catalog.hidden_campaigns == ()


def test_collab_maps_follow_everest_directories_without_collab_utils() -> None:
    lobby = _map('Maps/Example/0-Lobbies/1-Maps.bin')
    visible_map = _map('Maps/Example/1-Maps/Visible.bin')
    unrepresented_map = _map('Maps/Example/1-Submissions/Hidden.bin')
    collab = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby, visible_map, unrepresented_map],
    )

    catalog = make_catalog((collab,))

    assert catalog.hidden_campaigns == ()
    assert not any(
        isinstance(campaign, (CollabHubCampaign, CollabSubCampaign))
        for campaign in catalog.campaigns
    )
    assert [campaign.directory.as_posix() for campaign in catalog.campaigns] == [
        'Maps/Example/0-Lobbies',
        'Maps/Example/1-Maps',
        'Maps/Example/1-Submissions',
    ]


def test_campaigns_sort_combined_hidden_map_collections(tmp_path: Path) -> None:
    collab = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[_map('Maps/Example/1-Submissions/Map.bin')],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    helper = make_installed_mod(
        source='zip',
        filename='Helper.zip',
        path='Helper.zip',
        metadata_name='AltSidesHelper',
        metadata_version='1.0.0',
        maps=[_map('Maps/ZHelper/Map.bin')],
    )
    hider = make_installed_mod(
        source='zip',
        filename='HelperTestMapHider.zip',
        path='HelperTestMapHider.zip',
        metadata_name='HelperTestMapHider',
        metadata_version='1.0.0',
    )

    catalog = make_catalog(
        (collab, collab_utils, helper, hider), map_hider_rules=MapHiderRules(tmp_path)
    )

    assert [campaign.directory.as_posix() for campaign in catalog.hidden_campaigns] == [
        'Maps/Example/1-Submissions',
        'Maps/ZHelper',
    ]


def test_campaigns_merge_active_maps_from_multiple_pkgs() -> None:
    first = make_installed_mod(
        source='zip',
        filename='First.zip',
        path='First.zip',
        metadata_name='First',
        metadata_version='1.0.0',
        maps=[_map('Maps/Pack/1-Easy/First.bin')],
    )
    second = make_installed_mod(
        source='zip',
        filename='Second.zip',
        path='Second.zip',
        metadata_name='Second',
        metadata_version='1.0.0',
        maps=[_map('Maps/Pack/1-Easy/Second.bin')],
    )

    catalog = make_catalog((first, second))

    campaign = catalog.campaigns[0]
    assert campaign.directory.as_posix() == 'Maps/Pack/1-Easy'
    loaded_maps = _loaded_maps(campaign)
    assert all(isinstance(map_file, ModMap) for map_file in loaded_maps)
    assert [map_file.mod for map_file in loaded_maps if isinstance(map_file, ModMap)] == [
        first,
        second,
    ]
    assert campaign.uses_mod(first)
    assert campaign.uses_mod(second)


def test_a_later_manifest_entry_can_activate_its_physical_pkg() -> None:
    bundle = ZipMod(
        filename='Bundle.zip',
        path=Path('Bundle.zip'),
        manifest=(
            ModMetadata(
                name='DelayedEntry',
                version=Version.parse('1.0.0'),
                dependencies=(Dependency(name='Helper', version=Version.parse('1.0.0')),),
            ),
            ModMetadata(name='AvailableEntry', version=Version.parse('1.0.0')),
        ),
        map_files=[_map('Maps/Pack/Bundle.bin')],
    )

    catalog = make_catalog((bundle,))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, ModMap)
    assert loaded_map.mod is bundle
    assert catalog.unloaded_mods == ()


def test_campaigns_delay_content_until_an_optional_dependency_loads() -> None:
    delayed = make_installed_mod(
        source='zip',
        filename='A-Delayed.zip',
        path='A-Delayed.zip',
        metadata_name='Delayed',
        metadata_version='1.0.0',
        optional_dependencies=[Dependency(name='Optional', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Map.bin')],
    )
    optional = make_installed_mod(
        source='zip',
        filename='Z-Optional.zip',
        path='Z-Optional.zip',
        metadata_name='Optional',
        metadata_version='1.0.0',
        maps=[_map('Maps/Pack/Map.bin')],
    )

    catalog = make_catalog((delayed, optional))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, ModMap)
    assert isinstance(catalog.overrides[0].previous, ModMap)
    assert loaded_map.mod is delayed
    assert catalog.overrides[0].previous.mod is optional


def test_campaigns_do_not_require_an_absent_optional_dependency() -> None:
    standalone = make_installed_mod(
        source='zip',
        filename='Standalone.zip',
        path='Standalone.zip',
        metadata_name='Standalone',
        metadata_version='1.0.0',
        optional_dependencies=[Dependency(name='AbsentOptional', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Standalone.bin')],
    )

    catalog = make_catalog((standalone,))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, ModMap)
    assert loaded_map.mod is standalone
    assert catalog.unloaded_mods == ()


def test_campaigns_retry_delayed_entries_immediately_after_each_load() -> None:
    first_delayed = make_installed_mod(
        source='zip',
        filename='A-First.zip',
        path='A-First.zip',
        metadata_name='FirstDelayed',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='FirstHelper', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Map.bin')],
    )
    first_helper = make_installed_mod(
        source='zip',
        filename='B-Helper.zip',
        path='B-Helper.zip',
        metadata_name='FirstHelper',
        metadata_version='1.0.0',
        maps=[_map('Maps/Pack/Map.bin')],
    )
    second_delayed = make_installed_mod(
        source='zip',
        filename='C-Second.zip',
        path='C-Second.zip',
        metadata_name='SecondDelayed',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='SecondHelper', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Map.bin')],
    )
    second_helper = make_installed_mod(
        source='zip',
        filename='D-Helper.zip',
        path='D-Helper.zip',
        metadata_name='SecondHelper',
        metadata_version='1.0.0',
        maps=[_map('Maps/Pack/Map.bin')],
    )

    catalog = make_catalog((first_delayed, first_helper, second_delayed, second_helper))

    replacements = [override.replacement for override in catalog.overrides]
    assert all(isinstance(map_file, ModMap) for map_file in replacements)
    assert [map_file.mod for map_file in replacements if isinstance(map_file, ModMap)] == [
        first_delayed,
        second_helper,
        second_delayed,
    ]


def test_root_maps_form_the_empty_campaign_directory() -> None:
    root_map_mod = make_installed_mod(
        source='zip',
        filename='Root.zip',
        path='Root.zip',
        metadata_name='Root',
        metadata_version='1.0.0',
        maps=[_map('Maps/Root.bin')],
        dialogs={'en': {'levelset_': 'Root maps'}},
    )

    catalog = make_catalog((root_map_mod,))

    assert catalog.campaigns[0].directory == MAPS_DIR
    assert catalog.campaigns[0].dialog_key == ''
    assert Localization(catalog.dialogs).campaign_names(catalog.campaigns[0]) == {'en': 'Root maps'}
    assert Localization.campaign_fallback(catalog.campaigns[0]) == 'Maps'
    assert _loaded_maps(catalog.campaigns[0])[0].file_path.as_posix() == 'Maps/Root.bin'


def test_collab_special_maps_are_separate_from_its_campaign_maps() -> None:
    prologue = _map('Maps/ExampleCollab/0-Lobbies/0-Prologue.bin')
    lobby = _map('Maps/ExampleCollab/0-Lobbies/1-Easy.bin')
    gym = _map('Maps/ExampleCollab/0-Gyms/1-Easy.bin')
    playable = _map('Maps/ExampleCollab/1-Easy/Map.bin')
    collab = make_installed_mod(
        source='zip',
        filename='ExampleCollab.zip',
        path='ExampleCollab.zip',
        metadata_name='ExampleCollab',
        metadata_version='1.0.0',
        collab_id='ExampleCollab',
        dialogs={
            'zh-cn': {
                'levelset_ExampleCollab_0_Lobbies': '示例合集',
                'ExampleCollab_1_Easy': '简单大厅',
            }
        },
        maps=[prologue, lobby, gym, playable],
    )
    dialog_override = make_installed_mod(
        source='zip',
        filename='DialogOverride.zip',
        path='DialogOverride.zip',
        metadata_name='DialogOverride',
        metadata_version='1.0.0',
        dialogs={
            'zh-cn': {
                'levelset_ExampleCollab_0_Lobbies': '更新合集',
                'ExampleCollab_1_Easy': '更新大厅',
            }
        },
    )

    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )

    catalog = make_catalog((collab, dialog_override, collab_utils))

    assert len(catalog.campaigns) == 1
    campaign = catalog.campaigns[0]
    assert campaign.directory.as_posix() == 'Maps/ExampleCollab/0-Lobbies'
    assert isinstance(campaign, CollabHubCampaign)
    assert isinstance(campaign, ModCampaign)
    assert not isinstance(campaign, CollabSubCampaign)
    assert campaign.collab_id == 'ExampleCollab'
    assert Localization(catalog.dialogs).campaign_names(campaign) == {'zh-cn': '更新合集'}
    assert [item.file_path for item in _loaded_maps(campaign)] == [prologue, lobby]
    assert campaign.levels[1].is_lobby
    assert [item.directory.as_posix() for item in catalog.hidden_campaigns] == [
        'Maps/ExampleCollab/0-Gyms',
        'Maps/ExampleCollab/1-Easy',
    ]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[0])] == [gym]
    assert [item.file_path for item in _loaded_maps(catalog.hidden_campaigns[1])] == [playable]
    assert all(isinstance(item, CollabSubCampaign) for item in catalog.hidden_campaigns)
    assert all(isinstance(item, ModCampaign) for item in catalog.hidden_campaigns)
    assert not any(isinstance(item, CollabHubCampaign) for item in catalog.hidden_campaigns)
    assert Localization(catalog.dialogs).campaign_names(catalog.hidden_campaigns[1]) == {
        'zh-cn': '更新大厅'
    }


def test_lobby_journal_refs_use_exact_campaign_ids() -> None:
    lobby_map = _map('Maps/ExampleCollab/0-Lobbies/1-Easy.bin')
    exact_map = _map('Maps/ExampleCollab/1-Easy/Map.bin')
    case_variant_map = _map('Maps/ExampleCollab/1-easy/Map.bin')
    collab = make_installed_mod(
        source='zip',
        filename='ExampleCollab.zip',
        path='ExampleCollab.zip',
        metadata_name='ExampleCollab',
        metadata_version='1.0.0',
        collab_id='ExampleCollab',
        maps=[lobby_map, exact_map, case_variant_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    catalog = make_catalog((collab, collab_utils))
    lobby = catalog.campaigns[0].levels[0]
    assert lobby.is_lobby
    campaigns = {
        str(campaign.directory): campaign
        for campaign in (*catalog.campaigns, *catalog.hidden_campaigns)
    }

    resolved, diagnostics = resolve_campaign_refs(
        campaigns,
        (
            'ExampleCollab/1-Easy',
            'ExampleCollab/1-Easy',
            'ExampleCollab/1-EASY',
            'ExampleCollab/./1-Easy',
        ),
    )

    assert [campaign.directory.as_posix() for campaign in resolved] == ['Maps/ExampleCollab/1-Easy']
    assert diagnostics == (
        'ExampleCollab/1-EASY',
        'ExampleCollab/./1-Easy',
    )


def test_orphaned_collab_b_side_remains_a_distinct_lobby_map() -> None:
    lobby = ContentPath('Maps/ExampleCollab/0-Lobbies/1-Easy-B.bin')
    playable = _map('Maps/ExampleCollab/1-Easy/Map.bin')
    collab = make_installed_mod(
        source='zip',
        filename='ExampleCollab.zip',
        path='ExampleCollab.zip',
        metadata_name='ExampleCollab',
        metadata_version='1.0.0',
        collab_id='ExampleCollab',
        maps=[lobby, playable],
    )

    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )

    campaign = make_catalog((collab, collab_utils)).campaigns[0]

    level, side = _first_side(campaign)
    assert level[side].file_path == lobby
    assert side is LevelSide.A
    assert campaign.levels[0].is_lobby


def test_vanilla_maps_form_a_distinct_campaign_from_ungrouped_mod_maps(tmp_path: Path) -> None:
    content_dir = tmp_path / 'Content'
    maps_dir = content_dir / 'Maps'
    dialog_dir = content_dir / 'Dialog'
    maps_dir.mkdir(parents=True)
    dialog_dir.mkdir()
    (maps_dir / '0-Intro.bin').touch()
    (dialog_dir / 'English.txt').write_text(
        'AREA_0=Prologue\nLEVELSET_CELESTE=Celeste', encoding='utf-8'
    )
    mod = make_installed_mod(
        source='zip',
        filename='Loose.zip',
        path='Loose.zip',
        metadata_name='Loose',
        metadata_version='1.0.0',
        maps=[_map('Maps/Loose.bin')],
    )

    catalog = make_catalog(
        (
            mod.model_copy(
                update={'dialogs': {'en': {'AREA_0': 'Updated Prologue'}}},
            ),
        ),
        vanilla_maps=vanilla.load_maps(tmp_path),
        base_dialogs=vanilla.load_dialogs(tmp_path),
    )

    vanilla_camp, ungrouped = catalog.campaigns
    assert isinstance(vanilla_camp, VanillaCampaign)
    assert not isinstance(vanilla_camp, ModCampaign)
    assert isinstance(ungrouped, ModCampaign)
    assert vanilla_camp.directory.as_posix() == 'Maps'
    assert vanilla_camp.dialog_key == 'Celeste'
    assert Localization(catalog.dialogs).campaign_names(vanilla_camp) == {'en': 'Celeste'}
    assert Localization.campaign_fallback(vanilla_camp) == 'Celeste'
    assert vanilla_camp.map_count == 1
    level, _ = _first_side(vanilla_camp)
    assert level.sid == 'Celeste/0-Intro'
    assert Localization(catalog.dialogs).level_names(level) == {'en': 'Updated Prologue'}
    assert ungrouped.directory.as_posix() == 'Maps'
    assert Localization.campaign_fallback(ungrouped) == 'Maps'


def test_mod_maps_claiming_the_vanilla_campaign_remain_distinct_and_warn() -> None:
    mod = make_installed_mod(
        source='zip',
        filename='VanillaCollision.zip',
        path='VanillaCollision.zip',
        metadata_name='VanillaCollision',
        metadata_version='1.0.0',
        maps=[
            _map('Maps/Celeste/1-ForsakenCity.bin'),
            _map('Maps/Celeste/Extra.bin'),
        ],
    )
    vanilla_map = VanillaMap(_map('Maps/1-ForsakenCity.bin'), Path('Content'))

    catalog = make_catalog(
        (mod,),
        vanilla_maps=(vanilla_map,),
        base_dialogs={'zh-cn': {'LEVELSET_CELESTE': '官图'}},
    )

    vanilla, collision = catalog.campaigns
    assert isinstance(vanilla, VanillaCampaign)
    assert isinstance(collision, ModCampaign)
    assert collision.directory == MAPS_DIR / 'Celeste'
    assert Localization(catalog.dialogs, 'zh-cn').campaign_name(collision) == '官图'
    assert len(catalog.issues) == 1
    issue = catalog.issues[0]
    assert isinstance(issue, ReservedCampaignIssue)
    assert issue.campaign is collision
    assert issue.conflicting_sids == ('Celeste/1-ForsakenCity',)
