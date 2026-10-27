"""Root index consistency belongs to domain data, without requiring file adapters."""

from copy import copy, deepcopy

import pytest

from berries.game.saves.errors import BadProgressSave, ProgressConflictError
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.members import CampaignMembers


def test_domain_indexes_follow_both_write_paths_and_whole_snapshot_replacement() -> None:
    data = MainSaveData()
    active, recycled = data.flat_levels, data.recycled_flat_levels
    data.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    data.mods.recycled_campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    assert active['Pack/Map'] is data.mods.campaigns['Pack'].levels['Pack/Map']
    assert recycled['Pack/Map'] is data.mods.recycled_campaigns['Pack'].levels['Pack/Map']
    assert active['Pack/Map'] is not recycled['Pack/Map']
    level = LevelProgress('Pack/New')
    active.add(level)
    assert data.mods.campaigns['Pack'].levels['Pack/New'] is level
    previous = data.mods.campaigns
    data.install_mod_progress(
        ModSaveData(
            campaigns=CampaignMembers([CampaignProgress('Other', [LevelProgress('Other/Map')])])
        )
    )
    assert 'Other/Map' in active and 'Pack/Map' not in active and not recycled
    previous['Pack'].levels.add(LevelProgress('Pack/Detached'))
    assert 'Pack/Detached' not in active


def test_invalid_replacement_keeps_domain_values_and_held_index_unchanged() -> None:
    data = MainSaveData()
    index = data.flat_levels
    previous = data.vanilla
    previous_levels = previous.levels
    keys = tuple(index)
    with pytest.raises(BadProgressSave):
        data.vanilla.levels = [LevelProgress('Other/Map')]
    assert data.vanilla is previous and tuple(index) == keys
    data.install_campaigns((data, CampaignProgress('Celeste', [LevelProgress('Celeste/New')])))
    assert tuple(index) == ('Celeste/New',)
    previous_levels.add(LevelProgress('Celeste/Detached'))
    assert 'Celeste/Detached' not in index


def test_domain_batch_install_updates_every_held_view() -> None:
    first, second = MainSaveData(), MainSaveData()
    views = first.flat_levels, second.flat_levels
    campaigns = [
        CampaignProgress('Pack', [LevelProgress('Pack/First')]),
        CampaignProgress('Pack', [LevelProgress('Pack/Second')]),
    ]
    MainSaveData.install_campaigns((first, campaigns[0]), (second, campaigns[1]))
    assert views[0]['Pack/First'] is campaigns[0].levels['Pack/First']
    assert views[1]['Pack/Second'] is campaigns[1].levels['Pack/Second']
    second.install_mod_progress(first.mods)
    with pytest.raises(ProgressConflictError):
        MainSaveData.install_campaigns((first, campaigns[0]), (second, campaigns[1]))
    assert first.mods.campaigns['Pack'] is campaigns[0]


def test_data_copy_and_model_copy_rebuild_indexes_for_their_own_domain_members() -> None:
    data = MainSaveData()
    data.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    data.mods.recycled_campaigns.add(CampaignProgress('Old', [LevelProgress('Old/Map')]))
    for clone in (copy(data), deepcopy(data), data.model_copy(), data.model_copy(deep=True)):
        assert clone == data
        assert clone.flat_levels is not data.flat_levels
        assert clone.recycled_flat_levels is not data.recycled_flat_levels
        assert clone.flat_levels['Pack/Map'] is clone.mods.campaigns['Pack'].levels['Pack/Map']
        clone.install_mod_progress(ModSaveData())
        assert 'Pack/Map' not in clone.flat_levels and 'Pack/Map' in data.flat_levels
        assert not clone.recycled_flat_levels and 'Old/Map' in data.recycled_flat_levels
