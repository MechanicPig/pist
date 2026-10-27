"""Root-owned values, stable projections and explicit prepared installations."""

from copy import copy, deepcopy
from unittest.mock import patch

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.map_ref import MapRef
from berries.game.saves.progress.members import CampaignMembers, LevelMembers
from test_support.save_docs import parsed_session


@pytest.mark.parametrize('kind', (ModSaveData, ModBackupSave))
def test_mod_constructor_installs_supplied_members_before_building_indexes(
    kind: type[ModSaveData],
) -> None:
    active = CampaignMembers([CampaignProgress('Pack', [LevelProgress('Pack/Map')])])
    recycled = CampaignMembers(
        [CampaignProgress('Old', [LevelProgress('Old/Map')])], partition=ProgressPartition.RECYCLED
    )
    value = kind(campaigns=active, recycled_campaigns=recycled)
    assert value.campaigns is active and value.recycled_campaigns is recycled
    assert value.flat_levels['Pack/Map'] is active['Pack'].levels['Pack/Map']
    assert value.recycled_flat_levels['Old/Map'] is recycled['Old'].levels['Old/Map']
    active['Pack'].levels.add(LevelProgress('Pack/New'))
    assert value.mods.flat_levels['Pack/New'] is active['Pack'].levels['Pack/New']


@pytest.mark.parametrize('kind', (MainSaveData, MainSave))
def test_main_constructor_accepts_members_and_validated_fields_together(
    kind: type[MainSaveData],
) -> None:
    levels = MainSaveData().levels
    active = CampaignMembers([CampaignProgress('Pack', [LevelProgress('Pack/Map')])])
    value = kind(
        levels=levels,
        campaigns=active,
        name='Player',
        time_played=Duration.from_timer_frames(1),
        deaths=7,
    )
    assert value.levels is levels and value.campaigns is active
    assert value.name == 'Player' and value.deaths == 7
    assert value.flat_levels['Pack/Map'] is value.mods.flat_levels['Pack/Map']
    root = value.to_xml()
    assert root.findtext('HasModdedSaveData') == 'false'
    assert MainSaveData.from_xml(root).vanilla.snapshot() == value.vanilla.snapshot()
    active['Pack'].poem.append('changed')
    assert value.to_xml().findtext('HasModdedSaveData') == (
        'true' if isinstance(value, MainSave) else 'false'
    )


def test_vanilla_projection_reads_and_edits_the_actual_root_fields() -> None:
    save = MainSaveData()
    view = save.vanilla
    assert view.levels is save.levels and view.poem is save.poem
    save.unlocked_levels = 7
    view.total_strawberries = 9
    view.poem = ['poem']
    assert view.unlocked_levels == 7
    assert save.total_strawberries == 9 and save.poem == ['poem']
    assert {'poem', 'unlocked_levels', 'total_strawberries'} <= set(type(save).model_fields)
    assert 'vanilla' not in type(save).model_fields


@pytest.mark.parametrize('kind', (MainSave, ModBackupSave))
def test_mod_projection_is_bound_to_root_values(kind: type[MainSave | ModBackupSave]) -> None:
    save = kind()
    view = save.mods
    assert view.campaigns is save.campaigns
    assert view.recycled_campaigns is save.recycled_campaigns
    assert not hasattr(save, 'mod_flat_levels')
    view.session = parsed_session('Pack/Map', LevelSide.B)
    assert save.safe_session is view.session
    view.last_map = ('Pack/Map', LevelSide.B)
    assert view.last_map_ref is save.safe_last_map_ref
    save.safe_session = None
    assert view.session is None
    assert 'mods' not in type(save).model_fields


@pytest.mark.parametrize('name', ('vanilla', 'mods'))
def test_bound_views_cannot_be_replaced(name: str) -> None:
    save = MainSave()
    view = getattr(save, name)
    with pytest.raises(AttributeError):
        setattr(save, name, view)
    assert getattr(save, name) is view


def test_raw_container_binding_does_not_bypass_coordinated_writes() -> None:
    save = MainSave()
    for name, replacement in (
        ('levels', LevelMembers('Celeste')),
        ('campaigns', CampaignMembers()),
        ('recycled_campaigns', CampaignMembers()),
    ):
        with pytest.raises(AttributeError):
            setattr(save, name, replacement)
    with pytest.raises(BadProgressSave):
        save.vanilla.levels = [LevelProgress('Other/Map')]
    assert len(save.flat_levels) == 11


def test_constructor_checks_container_identity_without_rejecting_lenient_members() -> None:
    with pytest.raises(ValueError, match='Celeste'):
        MainSaveData(levels=LevelMembers('Other'))
    with pytest.raises(ValueError, match='partition'):
        ModSaveData(campaigns=CampaignMembers(partition=ProgressPartition.RECYCLED))
    with pytest.raises(ValueError, match='partition'):
        ModSaveData(recycled_campaigns=CampaignMembers())
    value = MainSaveData.from_xml(
        etree.fromstring('<SaveData><Areas><AreaStats SID="Other/Map"/></Areas></SaveData>'),
        strict=False,
    )
    assert 'Other/Map' in value.vanilla.levels


def test_root_value_equality_preserves_progress_member_order() -> None:
    original = MainSaveData()
    original.campaigns.update([CampaignProgress('First'), CampaignProgress('Second')])
    changed = deepcopy(original)
    changed.campaigns.add(changed.campaigns.pop('First'))
    assert original != changed
    assert original.campaigns == dict(changed.campaigns)
    changed = deepcopy(original)
    changed.vanilla.levels = list(reversed(tuple(changed.vanilla.levels.values())))
    assert original != changed
    assert original.levels == dict(changed.levels)


def test_copy_rebinds_projections_while_preserving_normal_sharing() -> None:
    save = MainSaveData()
    for clone in (copy(save), deepcopy(save), save.model_copy(), save.model_copy(deep=True)):
        assert clone.vanilla is not save.vanilla and clone.mods is not save.mods
        assert clone.vanilla.levels is clone.levels
        assert clone.mods.campaigns is clone.campaigns
        clone.vanilla.total_strawberries = 8
        assert save.total_strawberries == 0 and clone.total_strawberries == 8
        clone.mods.last_map = ('Pack/Map', LevelSide.A)
        assert save.mods.last_map is None
    shallow = copy(save)
    assert shallow.levels is save.levels and shallow.campaigns is save.campaigns
    assert shallow.poem is save.poem


@pytest.mark.parametrize('kind', (MainSave, ModBackupSave))
def test_explicit_install_keeps_held_mod_projection_and_all_indexes_live(
    kind: type[MainSave | ModBackupSave],
) -> None:
    save = kind()
    view, flat, mod_flat = save.mods, save.flat_levels, save.mods.flat_levels
    assert (flat is mod_flat) is isinstance(save, ModBackupSave)
    value = ModSaveData(
        campaigns=CampaignMembers([CampaignProgress('Pack', [LevelProgress('Pack/Map')])])
    )
    save.install_mod_progress(value)
    assert save.mods is view and view.campaigns is value.campaigns
    assert mod_flat['Pack/Map'] is flat['Pack/Map']
    assert not any(sid.startswith('Celeste/') for sid in mod_flat)
    value.campaigns['Pack'].levels.add(LevelProgress('Pack/New'))
    assert mod_flat['Pack/New'] is flat['Pack/New']


def test_combined_index_conflict_leaves_both_mod_partitions_unmodified() -> None:
    save = MainSaveData.from_xml(
        etree.fromstring('<SaveData><Areas><AreaStats SID="Pack/Map"/></Areas></SaveData>'),
        strict=False,
    )
    view, mod_flat, recycled = save.mods, save.mods.flat_levels, save.recycled_flat_levels
    old_campaigns, old_recycled = save.campaigns, save.recycled_campaigns
    old_level = save.flat_levels['Pack/Map']
    value = ModSaveData(
        safe_last_map_ref=MapRef(level_sid='Pack/Map', side=LevelSide.A),
        safe_session=parsed_session('Pack/Map', LevelSide.A),
        campaigns=CampaignMembers([CampaignProgress('Pack', [LevelProgress('Pack/Map')])]),
        recycled_campaigns=CampaignMembers(
            [CampaignProgress('Old', [LevelProgress('Old/Map')])],
            partition=ProgressPartition.RECYCLED,
        ),
    )
    with pytest.raises(BadProgressSave, match='Duplicate'):
        save.install_mod_progress(value)
    assert save.mods is view and save.campaigns is old_campaigns
    assert save.recycled_campaigns is old_recycled
    assert save.flat_levels['Pack/Map'] is old_level
    assert not mod_flat and not recycled
    assert save.safe_session is None and save.safe_last_map_ref is None
    assert not save.has_modded_save_data
    value.campaigns['Pack'].levels.add(LevelProgress('Pack/Detached'))
    assert 'Pack/Detached' not in mod_flat and 'Old/Map' not in recycled


def test_batch_install_snapshots_live_scalar_values_before_modifying_any_root() -> None:
    first, second = MainSaveData(), MainSaveData()
    first.vanilla.total_strawberries = 3
    second.vanilla.total_strawberries = 7
    first_view, second_view = first.vanilla, second.vanilla
    MainSaveData.install_campaigns((first, second_view), (second, first_view))
    assert first.vanilla is first_view and second.vanilla is second_view
    assert first.total_strawberries == 7 and second.total_strawberries == 3


def test_writing_bound_fields_does_not_construct_progress_snapshots_or_indexes() -> None:
    main, backup = MainSave(), ModBackupSave()
    with (
        patch.object(CampaignProgress, '__init__', side_effect=AssertionError('campaign snapshot')),
        patch.object(ModSaveData, '__init__', side_effect=AssertionError('mod snapshot')),
    ):
        main_xml = main.tostring()
        backup_xml = backup.tostring()
    assert etree.fromstring(main_xml).find('Areas') is not None
    assert etree.fromstring(backup_xml).find('LevelSets') is not None
    assert MainSave.fromstring(main_xml) == main
    assert ModBackupSave.fromstring(backup_xml) == backup


def test_backup_data_factory_and_writer_cover_complete_progress_without_file_adapter() -> None:
    data = ModSaveData.from_xml(
        etree.fromstring(
            '<ModSaveData><LevelSets><LevelSetStats Name="Pack"><Areas>'
            '<AreaStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/>'
            '<AreaModeStats/></Modes></AreaStats></Areas></LevelSetStats></LevelSets>'
            '</ModSaveData>'
        ),
        strict=False,
    )
    assert data.mods.campaigns['Pack'].levels['Pack/Map'] is data.flat_levels['Pack/Map']
    root = data.to_xml()
    assert root.tag == 'ModSaveData'
    assert root.find('LevelSets/LevelSetStats/Areas/AreaStats') is not None
    assert ModSaveData.from_xml(root) == data
    for name in ('read', 'write', 'fromstring', 'tostring'):
        assert not hasattr(data, name)
