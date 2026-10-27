"""The optional active Celeste entry is separate from the original-game view."""

from copy import copy, deepcopy
from io import BytesIO

import pytest
from lxml import etree

from berries.game.saves.conflicts import CampaignConflictContext, LevelConflictContext
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from test_support.save_docs import normal_save_xml


@pytest.mark.parametrize('save_type,root', ((MainSave, 'SaveData'), (ModBackupSave, 'ModSaveData')))
@pytest.mark.parametrize(
    'field,contents',
    (
        (
            'Areas',
            (
                '<Areas><AreaStats SID="Celeste/1-ForsakenCity"><Modes>'
                '<AreaModeStats Deaths="7"/><AreaModeStats/><AreaModeStats/>'
                '</Modes></AreaStats></Areas>'
            ),
        ),
        ('Poem', '<Poem><string>kept</string></Poem>'),
    ),
)
def test_active_entry_strict_empty_lists_and_lenient_repair(
    save_type: type[MainSave | ModBackupSave], root: str, field: str, contents: str
) -> None:
    data = normal_save_xml(
        f'<{root}><LevelSets><LevelSetStats Name="Celeste">{contents}'
        f'</LevelSetStats></LevelSets></{root}>'
    )
    with pytest.raises(BadProgressSave, match=field):
        save_type.fromstring(data)
    save = save_type.fromstring(data, strict=False)
    entry = save.mods.vanilla_entry
    assert entry is not None
    assert 'Celeste' not in save.mods.campaigns
    with pytest.raises(BadProgressSave, match=field):
        save.tostring()
    if field == 'Areas':
        level = entry.levels['Celeste/1-ForsakenCity']
        progress = level.maps[0]
        assert progress is not None and progress.deaths == 7
        entry.levels.clear()
    else:
        assert entry.poem == ['kept']
        entry.poem.clear()
    save_type.fromstring(save.tostring())


@pytest.mark.parametrize('save_type,root', ((MainSave, 'SaveData'), (ModBackupSave, 'ModSaveData')))
def test_empty_entry_keeps_nonzero_counters(
    save_type: type[MainSave | ModBackupSave], root: str
) -> None:
    save = save_type.fromstring(
        normal_save_xml(
            f'<{root}><LevelSets><LevelSetStats Name="Celeste"><Areas/><Poem/>'
            '<UnlockedAreas>7</UnlockedAreas><TotalStrawberries>15</TotalStrawberries>'
            f'</LevelSetStats></LevelSets></{root}>'
        )
    )
    entry = save.mods.vanilla_entry
    assert entry is not None
    assert entry.unlocked_levels == 7 and entry.total_strawberries == 15
    restored = save_type.fromstring(save.tostring())
    assert restored.mods.vanilla_entry is not None
    if isinstance(save, ModBackupSave):
        assert restored.mods.vanilla_entry.unlocked_levels == 7
        assert restored.mods.vanilla_entry.total_strawberries == 15
    else:
        assert restored.mods.vanilla_entry.unlocked_levels == save.unlocked_levels
        assert restored.mods.vanilla_entry.total_strawberries == save.total_strawberries
    assert entry.unlocked_levels == 7 and entry.total_strawberries == 15


@pytest.mark.parametrize('save_type', (MainSave, ModBackupSave))
def test_missing_entry_is_not_created_by_view_access_or_output(
    save_type: type[MainSave | ModBackupSave],
) -> None:
    save = save_type()
    assert save.mods.vanilla_entry is None
    if isinstance(save, MainSave):
        save.vanilla.total_strawberries = 7
        save.vanilla.poem.append('root_poem')
    root = etree.fromstring(save.tostring())
    assert root.find("LevelSets/LevelSetStats[@Name='Celeste']") is None
    assert save.mods.vanilla_entry is None
    assert save_type.fromstring(save.tostring()).mods.vanilla_entry is None


@pytest.mark.parametrize('save_type', (MainSave, ModBackupSave))
def test_nonempty_entry_poem_rejects_write_before_modifying_destination(
    save_type: type[MainSave | ModBackupSave],
) -> None:
    save = save_type()
    entry = CampaignProgress('Celeste', poem=['retained for repair'])
    save.mods.vanilla_entry = entry
    stream = BytesIO(b'unchanged')
    with pytest.raises(BadProgressSave, match='Poem'):
        save.write(stream)
    assert stream.getvalue() == b'unchanged'
    assert save.mods.vanilla_entry is entry and entry.poem == ['retained for repair']


@pytest.mark.parametrize('save_type,root', ((MainSave, 'SaveData'), (ModBackupSave, 'ModSaveData')))
def test_recycled_celeste_is_an_independent_ordinary_campaign(
    save_type: type[MainSave | ModBackupSave], root: str
) -> None:
    data = normal_save_xml(
        f'<{root}><LevelSets><LevelSetStats Name="Celeste"><Areas/><Poem/>'
        '</LevelSetStats></LevelSets><LevelSetRecycleBin><LevelSetStats Name="Celeste">'
        '<Areas><AreaStats SID="Celeste/1-ForsakenCity"><Modes><AreaModeStats Deaths="7"/>'
        '<AreaModeStats/><AreaModeStats/></Modes></AreaStats></Areas><Poem><string>old</string>'
        '</Poem><TotalStrawberries>12</TotalStrawberries></LevelSetStats>'
        f'</LevelSetRecycleBin></{root}>'
    )
    save = save_type.fromstring(data)
    history = save.mods.recycled_campaigns['Celeste']
    assert (
        save.mods.recycled_flat_levels['Celeste/1-ForsakenCity']
        is history.levels['Celeste/1-ForsakenCity']
    )
    assert save.mods.partition_size(recycled=True) == 1
    restored = save_type.fromstring(save.tostring())
    assert restored.mods.recycled_campaigns['Celeste'] == history
    assert history.poem == ['old'] and history.total_strawberries == 12
    if isinstance(save, MainSave):
        assert save.vanilla is not history
        assert not save.vanilla.poem and save.vanilla.total_strawberries == 0
    for members in (copy(save.mods.recycled_campaigns), deepcopy(save.mods.recycled_campaigns)):
        members.add(CampaignProgress('Celeste'))
    with pytest.raises(BadProgressSave):
        save.mods.campaigns.add(history)


def test_recycled_celeste_conflicts_keep_recycled_context() -> None:
    def choose_campaign(
        old: CampaignConflictContext, new: CampaignConflictContext
    ) -> CampaignProgress:
        assert old.partition is new.partition is ProgressPartition.RECYCLED
        return new.progress

    def choose_level(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        assert old.partition is new.partition is ProgressPartition.RECYCLED
        return new.progress

    root = etree.fromstring(
        '<ModSaveData><LevelSetRecycleBin>'
        '<LevelSetStats Name="Celeste"/><LevelSetStats Name="Celeste"><Areas>'
        '<AreaStats SID="Celeste/1-ForsakenCity"/><AreaStats SID="Celeste/1-ForsakenCity"/>'
        '</Areas></LevelSetStats></LevelSetRecycleBin></ModSaveData>'
    )
    result = ModSaveData.from_xml(
        root, strict=False, on_campaign_conflict=choose_campaign, on_level_conflict=choose_level
    )
    assert len(result.recycled_campaigns['Celeste'].levels) == 1


@pytest.mark.parametrize('save_type,root', ((MainSave, 'SaveData'), (ModBackupSave, 'ModSaveData')))
def test_active_entry_level_conflicts_allow_explicit_lenient_repair(
    save_type: type[MainSave | ModBackupSave], root: str
) -> None:
    sid = 'Celeste/1-ForsakenCity'
    data = normal_save_xml(
        f'<{root}><LevelSets><LevelSetStats Name="Celeste"><Areas>'
        f'<AreaStats SID="{sid}"><Modes><AreaModeStats Deaths="7"/>'
        '<AreaModeStats/><AreaModeStats/></Modes></AreaStats>'
        f'<AreaStats SID="{sid}"><Modes><AreaModeStats Deaths="9"/>'
        '<AreaModeStats/><AreaModeStats/></Modes></AreaStats>'
        f'</Areas></LevelSetStats></LevelSets></{root}>'
    )
    conflicts = []

    def choose(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        assert old.partition is new.partition is ProgressPartition.MODS
        assert old.campaign.name == new.campaign.name == 'Celeste'
        assert old.progress.sid == new.progress.sid == sid
        conflicts.append((old.progress, new.progress))
        return new.progress

    with pytest.raises(BadProgressSave):
        save_type.fromstring(data, on_level_conflict=choose)
    assert not conflicts
    with pytest.raises(BadProgressSave):
        save_type.fromstring(data, strict=False)
    save = save_type.fromstring(data, strict=False, on_level_conflict=choose)
    entry = save.mods.vanilla_entry
    assert entry is not None
    assert len(conflicts) == 1
    assert list(entry.levels) == [sid]
    level = entry.levels[sid]
    assert level is conflicts[0][1]
    progress = level.maps[0]
    assert progress is not None and progress.deaths == 9
    with pytest.raises(BadProgressSave, match='Areas'):
        save.tostring()
    entry.levels.clear()
    save_type.fromstring(save.tostring())
