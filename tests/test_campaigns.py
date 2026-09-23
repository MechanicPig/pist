"""Tests for Everest-ordered campaign assembly."""

from pathlib import Path

from pist.game.campaigns import (
    CollabLobby,
    LoadedCampaign,
    campaign_names,
    load_campaigns,
    load_vanilla_dialogs,
    load_vanilla_maps,
    resolve_lobby_side,
)
from pist.game.everest import Dependency, EverestModMetadata, Version
from pist.game.levels import Level, LevelSide, LoadedMap, LoadedModMap, map_dialog_texts, map_names
from pist.game.map_hiders import MapHiderRules
from pist.game.map_source import MapSource
from pist.game.maps import MapInfo
from pist.game.mods import ZipMod
from tests.mod_factory import make_installed_mod


def _map(path: str) -> MapInfo:
    return MapInfo(file_path=path)


def _loaded_maps(campaign: LoadedCampaign) -> tuple[LoadedMap, ...]:
    return tuple(level.maps_by_side[side] for level, side in campaign.iter_sides())


def _first_side(campaign: LoadedCampaign) -> tuple[Level, LevelSide]:
    return next(campaign.iter_sides())


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

    catalog = load_campaigns((delayed, helper))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, LoadedModMap)
    assert loaded_map.mod is delayed
    assert len(catalog.overrides) == 1
    assert isinstance(catalog.overrides[0].previous, LoadedModMap)
    assert isinstance(catalog.overrides[0].replacement, LoadedModMap)
    assert catalog.overrides[0].previous.mod is helper
    assert catalog.overrides[0].replacement.mod is delayed


def test_campaigns_omit_packages_with_unsatisfied_required_dependencies() -> None:
    unavailable = make_installed_mod(
        source='zip',
        filename='Unavailable.zip',
        path='Unavailable.zip',
        metadata_name='Unavailable',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Missing', version=Version.parse('1.0.0'))],
        maps=[_map('Maps/Pack/Unavailable.bin')],
    )

    catalog = load_campaigns((unavailable,))

    assert catalog.campaigns == ()
    assert catalog.unloaded_mods == (unavailable,)


def test_campaigns_use_globally_merged_dialog_entries_for_active_maps() -> None:
    map_info = MapInfo(file_path='Maps/MapModifier/Example.bin')
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
        dialogs={'en': {'MAPMODIFIER_EXAMPLE': 'Final name', 'MapModifier': 'Final campaign'}},
    )

    catalog = load_campaigns((map_mod, first_dialog, later_dialog))

    level, side = _first_side(catalog.campaigns[0])
    assert map_names(level, side, catalog.dialogs) == {'en': 'Final name'}
    assert map_dialog_texts(level, catalog.dialogs, 'author') == {'en': 'First author'}
    assert map_dialog_texts(level, catalog.dialogs, 'collabcreditstags') == {'en': 'Beginner'}
    assert campaign_names(catalog.campaigns[0], catalog.dialogs) == {'en': 'Final campaign'}


def test_campaigns_ignore_dialogs_from_unloaded_packages() -> None:
    map_info = MapInfo(file_path='Maps/MapModifier/Example.bin')
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
        dialogs={'en': {'Shared_Map': 'Unavailable name'}},
    )

    catalog = load_campaigns((map_mod, unavailable_dialog))

    assert map_names(*_first_side(catalog.campaigns[0]), catalog.dialogs) == {}


def test_campaigns_use_base_dialog_entries_before_loaded_mod_entries() -> None:
    map_info = MapInfo(file_path='Maps/MapModifier/Example.bin')
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

    base_catalog = load_campaigns(
        (map_mod,),
        base_dialogs={'en': {'MapModifier_Example': 'Base name'}},
    )
    catalog = load_campaigns(
        (map_mod, override_mod),
        base_dialogs={'en': {'MapModifier_Example': 'Base name'}},
    )

    assert map_names(*_first_side(base_catalog.campaigns[0]), base_catalog.dialogs) == {
        'en': 'Base name'
    }
    assert map_names(*_first_side(catalog.campaigns[0]), catalog.dialogs) == {'en': 'Mod name'}


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

    catalog = load_campaigns(
        (helper_map, hider),
        map_hider_rules=MapHiderRules(tmp_path),
    )

    assert catalog.campaigns == ()
    assert [campaign.directory.as_posix() for campaign in catalog.hidden_campaigns] == [
        'Maps/Hidden'
    ]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[0])] == [hidden_map]


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

    catalog = load_campaigns(
        (helper_map,),
        map_hider_rules=MapHiderRules(tmp_path),
    )

    assert catalog.hidden_campaigns == ()
    assert [item.map_info for item in _loaded_maps(catalog.campaigns[0])] == [hidden_map]


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

    catalog = load_campaigns((collab, collab_utils))

    campaign = catalog.campaigns[0]
    assert campaign.directory.as_posix() == 'Maps/Example/0-Lobbies'
    assert [item.map_info for item in _loaded_maps(campaign)] == [lobby]
    assert [
        hidden_campaign.directory.as_posix() for hidden_campaign in catalog.hidden_campaigns
    ] == [
        'Maps/Example/0-Lobbies/1-Maps',
        'Maps/Example/1-Maps',
        'Maps/Example/1-Maps/Nested',
        'Maps/Example/1-Submissions',
    ]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[0])] == [nested_lobby]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[1])] == [visible_map]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[2])] == [
        nested_visible_map
    ]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[3])] == [hidden_map]


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

    catalog = load_campaigns((collab, collab_utils))

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

    catalog = load_campaigns((collab,))

    assert catalog.hidden_campaigns == ()
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

    catalog = load_campaigns(
        (collab, collab_utils, helper, hider), map_hider_rules=MapHiderRules(tmp_path)
    )

    assert [campaign.directory.as_posix() for campaign in catalog.hidden_campaigns] == [
        'Maps/Example/1-Submissions',
        'Maps/ZHelper',
    ]


def test_campaigns_merge_active_maps_from_multiple_packages() -> None:
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

    catalog = load_campaigns((first, second))

    campaign = catalog.campaigns[0]
    assert campaign.directory.as_posix() == 'Maps/Pack/1-Easy'
    assert [loaded_map.source_name for loaded_map in _loaded_maps(campaign)] == ['First', 'Second']


def test_a_later_manifest_entry_can_activate_its_physical_package() -> None:
    bundle = ZipMod(
        filename='Bundle.zip',
        path=Path('Bundle.zip'),
        manifest=(
            EverestModMetadata(
                name='DelayedEntry',
                version=Version.parse('1.0.0'),
                dependencies=[Dependency(name='Helper', version=Version.parse('1.0.0'))],
            ),
            EverestModMetadata(name='AvailableEntry', version=Version.parse('1.0.0')),
        ),
        maps=[_map('Maps/Pack/Bundle.bin')],
    )

    catalog = load_campaigns((bundle,))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, LoadedModMap)
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

    catalog = load_campaigns((delayed, optional))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, LoadedModMap)
    assert isinstance(catalog.overrides[0].previous, LoadedModMap)
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

    catalog = load_campaigns((standalone,))

    loaded_map = _loaded_maps(catalog.campaigns[0])[0]
    assert isinstance(loaded_map, LoadedModMap)
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

    catalog = load_campaigns((first_delayed, first_helper, second_delayed, second_helper))

    assert [override.replacement.source_name for override in catalog.overrides] == [
        'FirstDelayed',
        'SecondHelper',
        'SecondDelayed',
    ]


def test_campaigns_keep_root_maps_outside_campaign_groups() -> None:
    root_map_mod = make_installed_mod(
        source='zip',
        filename='Root.zip',
        path='Root.zip',
        metadata_name='Root',
        metadata_version='1.0.0',
        maps=[_map('Maps/Root.bin')],
    )

    catalog = load_campaigns((root_map_mod,))

    assert catalog.campaigns[0].is_ungrouped
    assert catalog.campaigns[0].fallback_name == '未归类地图'
    assert _loaded_maps(catalog.campaigns[0])[0].map_info.file_path.as_posix() == 'Maps/Root.bin'


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

    catalog = load_campaigns((collab, dialog_override, collab_utils))

    assert len(catalog.campaigns) == 1
    campaign = catalog.campaigns[0]
    assert campaign.directory.as_posix() == 'Maps/ExampleCollab/0-Lobbies'
    assert campaign_names(campaign, catalog.dialogs) == {'zh-cn': '更新合集'}
    assert [item.map_info for item in _loaded_maps(campaign)] == [prologue, lobby]
    assert isinstance(campaign.levels[1], CollabLobby)
    assert [item.directory.as_posix() for item in catalog.hidden_campaigns] == [
        'Maps/ExampleCollab/0-Gyms',
        'Maps/ExampleCollab/1-Easy',
    ]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[0])] == [gym]
    assert [item.map_info for item in _loaded_maps(catalog.hidden_campaigns[1])] == [playable]
    assert campaign_names(catalog.hidden_campaigns[1], catalog.dialogs) == {'zh-cn': '更新大厅'}


def test_lobby_journal_references_use_exact_campaign_identities() -> None:
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
    catalog = load_campaigns((collab, collab_utils))
    lobby = catalog.campaigns[0].levels[0]
    assert isinstance(lobby, CollabLobby)
    campaigns = {
        str(campaign.directory): campaign
        for campaign in (*catalog.campaigns, *catalog.hidden_campaigns)
    }

    resolved, diagnostics = resolve_lobby_side(
        lobby,
        LevelSide.A,
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
        '日志引用的地图集不存在：ExampleCollab/1-EASY',
        '日志引用的地图集不存在：ExampleCollab/./1-Easy',
    )
    assert lobby.campaigns_by_side[LevelSide.A] == resolved


def test_orphaned_collab_b_side_remains_a_distinct_lobby_map() -> None:
    lobby = MapInfo(
        file_path='Maps/ExampleCollab/0-Lobbies/1-Easy-B.bin',
    )
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

    campaign = load_campaigns((collab, collab_utils)).campaigns[0]

    level, side = _first_side(campaign)
    assert level.maps_by_side[side].map_info.file_path == lobby.file_path
    assert side is LevelSide.A
    assert isinstance(campaign.levels[0], CollabLobby)


def test_vanilla_maps_form_a_distinct_campaign_from_ungrouped_mod_maps(tmp_path: Path) -> None:
    content_dir = tmp_path / 'Content'
    maps_dir = content_dir / 'Maps'
    dialog_dir = content_dir / 'Dialog'
    maps_dir.mkdir(parents=True)
    dialog_dir.mkdir()
    (maps_dir / '0-Intro.bin').touch()
    (dialog_dir / 'English.txt').write_text('AREA_0=Prologue', encoding='utf-8')
    mod = make_installed_mod(
        source='zip',
        filename='Loose.zip',
        path='Loose.zip',
        metadata_name='Loose',
        metadata_version='1.0.0',
        maps=[_map('Maps/Loose.bin')],
    )

    catalog = load_campaigns(
        (
            mod.model_copy(
                update={'dialogs': {'en': {'AREA_0': 'Updated Prologue'}}},
            ),
        ),
        vanilla_maps=load_vanilla_maps(tmp_path),
        base_dialogs=load_vanilla_dialogs(tmp_path),
    )

    vanilla, ungrouped = catalog.campaigns
    assert vanilla.source is MapSource.VANILLA
    assert vanilla.directory.as_posix() == 'Maps'
    assert vanilla.fallback_name == '原版地图'
    assert vanilla.map_count == 1
    level, side = _first_side(vanilla)
    assert level.sid == 'Celeste/0-Intro'
    assert map_names(level, side, catalog.dialogs) == {'en': 'Updated Prologue'}
    assert ungrouped.source is MapSource.MOD
    assert ungrouped.directory.as_posix() == 'Maps'
    assert ungrouped.fallback_name == '未归类地图'
