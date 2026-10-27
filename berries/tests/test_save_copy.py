"""Save copies preserve domain sharing without rebinding another root's indexes."""

from copy import copy, deepcopy

import pytest

from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.save import Save
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.members import CampaignMembers


def test_domain_state_copies_without_file_api_and_keeps_live_indexes() -> None:
    data = MainSaveData()
    data.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    shallow = copy(data)
    deep = deepcopy(data)
    assert shallow.mods is not data.mods
    assert shallow.mods.campaigns is data.mods.campaigns
    assert deep == data
    assert deep.mods is not data.mods
    for name in ('read', 'fromstring', 'tostring', 'write'):
        assert not hasattr(data, name)
    assert shallow.flat_levels['Pack/Map'] is data.flat_levels['Pack/Map']
    assert deep.flat_levels['Pack/Map'] is deep.mods.campaigns['Pack'].levels['Pack/Map']
    assert deep.flat_levels['Pack/Map'] is not data.flat_levels['Pack/Map']


def test_main_domain_state_replacement_and_session_selection() -> None:
    data = MainSaveData()
    clone = copy(data)
    clone.install_campaigns((clone, CampaignProgress('Celeste')))
    assert len(data.vanilla.levels) == 11
    assert not clone.vanilla.levels
    assert clone.session is None and clone.current_map is None
    assert clone.needs_mod_recovery
    clone.install_mod_progress(ModSaveData())
    assert not clone.needs_mod_recovery
    assert data.needs_mod_recovery


@pytest.mark.parametrize('save_type', (MainSaveData, MainSave, ModBackupSave))
def test_shallow_copy_shares_members_but_not_root_replacement(
    save_type: type[MainSaveData | ModBackupSave],
) -> None:
    save = save_type()
    campaign = CampaignProgress('Pack', [LevelProgress('Pack/One')])
    save.mods.campaigns.add(campaign)
    clone = copy(save)
    assert clone.mods is not save.mods
    assert clone.mods.campaigns is save.mods.campaigns
    campaign.levels.add(LevelProgress('Pack/Two'))
    assert clone.flat_levels['Pack/Two'] is save.flat_levels['Pack/Two']

    clone.install_mod_progress(
        ModSaveData(
            campaigns=CampaignMembers([CampaignProgress('Other', [LevelProgress('Other/Map')])])
        )
    )
    assert 'Pack/Two' in save.flat_levels
    assert 'Other/Map' not in save.flat_levels
    assert 'Other/Map' in clone.flat_levels
    campaign.levels.add(LevelProgress('Pack/Three'))
    assert 'Pack/Three' in save.flat_levels
    assert 'Pack/Three' not in clone.flat_levels


def test_shallow_main_copy_can_replace_vanilla_independently() -> None:
    save = MainSave()
    clone = copy(save)
    assert clone.vanilla is not save.vanilla
    assert clone.vanilla.levels is save.vanilla.levels
    clone.install_campaigns((clone, CampaignProgress('Celeste')))
    assert not clone.flat_levels
    assert len(save.flat_levels) == 11
    assert len(save.vanilla.levels) == 11


@pytest.mark.parametrize('save_type', (MainSaveData, MainSave, ModBackupSave))
def test_deep_copy_rebuilds_active_and_recycled_indexes(
    save_type: type[MainSaveData | ModBackupSave],
) -> None:
    save = save_type()
    save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    save.mods.recycled_campaigns.add(CampaignProgress('Old', [LevelProgress('Old/Map')]))
    clone = deepcopy(save)
    if isinstance(save, MainSaveData) and not isinstance(save, MainSave):
        assert clone == save
    else:
        assert isinstance(clone, (MainSave, ModBackupSave))
        assert clone.tostring() == save.tostring()
    assert clone.mods is not save.mods
    for campaigns, index, mod_index, original in (
        (clone.mods.campaigns, clone.flat_levels, clone.mods.flat_levels, save.flat_levels),
        (
            clone.mods.recycled_campaigns,
            clone.recycled_flat_levels,
            clone.mods.recycled_flat_levels,
            save.recycled_flat_levels,
        ),
    ):
        for campaign in campaigns.values():
            for sid, level in campaign.levels.items():
                assert index[sid] is level
                assert mod_index[sid] is level
                assert level is not original[sid]
                progress = level.maps[0]
                original_progress = original[sid].maps[0]
                assert progress is not None and original_progress is not None
                progress.deaths = 7
                assert original_progress.deaths == 0
            campaign.levels.add(LevelProgress(f'{campaign.name}/New'))
            assert f'{campaign.name}/New' in index
            assert f'{campaign.name}/New' not in original


@pytest.mark.parametrize('save_type', (CollabUtils2Save, EndersBlenderSave, CommunalHelperSave))
def test_mod_saves_allow_standard_copy(save_type: type[Save]) -> None:
    save = save_type()
    for clone in (copy(save), deepcopy(save)):
        assert clone is not save
        assert clone.tostring() == save.tostring()


def test_mod_save_copy_shares_or_isolates_mutable_values() -> None:
    save = CommunalHelperSave.fromstring('SummitGems: [one]')
    shallow = copy(save)
    deep = deepcopy(save)
    assert shallow.summit_gems is save.summit_gems
    assert deep.summit_gems is not save.summit_gems
    assert shallow.summit_gems is not None
    shallow.summit_gems.add('two')
    assert save.summit_gems == {'one', 'two'}
    assert deep.summit_gems == {'one'}
