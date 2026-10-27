"""Resolve progress duplicates incrementally, without silently retaining raw duplicates."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Never, cast

import pytest
from lxml import etree

from berries.game.saves.conflicts import (
    CampaignConflictContext,
    CampaignConflictResolver,
    LevelConflictContext,
    LevelConflictResolver,
)
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.progress import CampaignProgress, LevelProgress, MapProgress
from berries.game.saves.slot import SaveSlot
from test_support.save_docs import complete_vanilla, normal_save_xml, require_map


def _area(deaths: int, sid: str = 'Pack/Map') -> str:
    return (
        f'<AreaStats SID="{sid}" ID="{deaths}" Cassette="false"><Modes>'
        + f'<AreaModeStats Deaths="{deaths}" TimePlayed="0"/>' * 3
        + '</Modes></AreaStats>'
    )


def _campaign(value: int, areas: str = '') -> str:
    return (
        '<LevelSetStats Name="Pack">'
        f'<TotalStrawberries>{value}</TotalStrawberries><Areas>{areas}</Areas>'
        f'<UnlockedAreas>{value}</UnlockedAreas></LevelSetStats>'
    )


def _document(
    save_type: type[MainSave | ModBackupSave], contents: str, *, complete: bool = True
) -> bytes:
    tag = 'SaveData' if save_type is MainSave else 'ModSaveData'
    xml = f'<{tag}>{contents}</{tag}>'
    return (complete_vanilla(xml) if save_type is MainSave and complete else xml).encode()


@pytest.mark.parametrize('save_type', [MainSave, ModBackupSave])
@pytest.mark.parametrize('group', ['LevelSets', 'LevelSetRecycleBin'])
def test_campaign_duplicates_use_current_result_and_write_only_resolved_values(
    save_type: type[MainSave | ModBackupSave],
    group: str,
) -> None:
    calls: list[tuple[int, int]] = []

    def merge(old: CampaignConflictContext, new: CampaignConflictContext) -> CampaignProgress:
        assert not hasattr(old, 'campaign')
        assert old.partition is (
            ProgressPartition.MODS if group == 'LevelSets' else ProgressPartition.RECYCLED
        )
        calls.append((old.progress.total_strawberries, new.progress.total_strawberries))
        old.progress.total_strawberries += new.progress.total_strawberries
        return old.progress

    content = f'<{group}>' + ''.join(_campaign(i) for i in (1, 2, 3)) + f'</{group}>'
    buffer = BytesIO(_document(save_type, content))
    save = save_type.read(buffer, strict=False, on_campaign_conflict=merge)
    collection = save.mods.campaigns if group == 'LevelSets' else save.mods.recycled_campaigns
    assert len(collection) == 1 and collection['Pack'].total_strawberries == 6
    assert calls == [(1, 2), (3, 3)]
    buffer.seek(0)
    save.write(buffer)
    buffer.truncate()
    root = etree.fromstring(buffer.getvalue())
    assert len(root.findall(f'{group}/LevelSetStats')) == 1
    assert root.findtext(f'{group}/LevelSetStats/UnlockedAreas') == '1'
    buffer.seek(0)
    restored = save_type.read(buffer)
    collection = (
        restored.mods.campaigns if group == 'LevelSets' else restored.mods.recycled_campaigns
    )
    assert collection['Pack'].total_strawberries == 6


@pytest.mark.parametrize('save_type', [MainSave, ModBackupSave])
@pytest.mark.parametrize('group', ['LevelSets', 'LevelSetRecycleBin'])
def test_level_duplicates_can_select_incoming_and_preserve_its_numeric_identity(
    save_type: type[MainSave | ModBackupSave],
    group: str,
) -> None:
    calls: list[tuple[int, int]] = []

    def latest(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        assert isinstance(old, LevelConflictContext)
        assert old.campaign.name == 'Pack'
        assert new.campaign is old.campaign
        assert new.partition is old.partition
        assert old.partition is (
            ProgressPartition.MODS if group == 'LevelSets' else ProgressPartition.RECYCLED
        )
        calls.append(
            (
                require_map(old.progress.maps[0]).deaths,
                require_map(new.progress.maps[0]).deaths,
            )
        )
        return new.progress

    areas = ''.join(_area(i) for i in (1, 2, 3))
    content = f'<{group}>{_campaign(0, areas)}</{group}>'
    buffer = BytesIO(_document(save_type, content))
    save = save_type.read(buffer, strict=False, on_level_conflict=latest)
    buffer.seek(0)
    save.write(buffer)
    buffer.truncate()
    assert calls == [(1, 2), (2, 3)]
    nodes = etree.fromstring(buffer.getvalue()).findall(f'{group}/LevelSetStats/Areas/AreaStats')
    assert len(nodes) == 1
    assert nodes[0].get('ID') == '3'
    buffer.seek(0)
    restored = save_type.read(buffer)
    collection = (
        restored.mods.campaigns if group == 'LevelSets' else restored.mods.recycled_campaigns
    )
    assert require_map(collection['Pack'].levels['Pack/Map'].maps[0]).deaths == 3


@pytest.mark.parametrize('keep_new', (False, True))
def test_combined_level_conflict_retains_each_candidates_actual_partition(keep_new: bool) -> None:
    sid = 'Celeste/1-ForsakenCity'
    calls = []

    def choose(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        assert old.partition is ProgressPartition.VANILLA
        assert new.partition is ProgressPartition.MODS
        assert old.campaign.name == 'Celeste' and new.campaign.name == 'Wrong'
        assert old.progress.sid == new.progress.sid == sid
        calls.append((old.progress, new.progress))
        return new.progress if keep_new else old.progress

    data = _document(
        MainSave,
        f'<Areas>{_area(1, sid)}</Areas><LevelSets><LevelSetStats Name="Wrong"><Areas>'
        f'{_area(2, sid)}</Areas></LevelSetStats></LevelSets>',
    )
    save = MainSave.fromstring(data, strict=False, on_level_conflict=choose)
    assert len(calls) == 1
    assert save.vanilla.levels[sid] is calls[0][int(keep_new)]
    assert sid not in save.mods.campaigns['Wrong'].levels


@pytest.mark.parametrize('strict', [True, False])
@pytest.mark.parametrize('levels', [True, False])
def test_duplicates_default_to_bad_progress_save(strict: bool, levels: bool) -> None:
    content = _campaign(0, _area(1) + _area(2)) if levels else _campaign(1) + _campaign(2)
    data = _document(ModBackupSave, f'<LevelSets>{content}</LevelSets>')
    if strict:
        data = normal_save_xml(data).encode()
    with pytest.raises(BadProgressSave, match=r'Duplicate.*Pack'):
        ModBackupSave.read(BytesIO(data), strict=strict)


@pytest.mark.parametrize('levels', [True, False])
def test_only_the_matching_conflict_callback_handles_a_duplicate(levels: bool) -> None:
    def unexpected(
        old: CampaignConflictContext | LevelConflictContext,
        new: CampaignConflictContext | LevelConflictContext,
    ) -> Never:
        pytest.fail('the callback for the other progress type must not run')

    content = _campaign(0, _area(1) + _area(2)) if levels else _campaign(1) + _campaign(2)
    data = _document(ModBackupSave, f'<LevelSets>{content}</LevelSets>')

    def load() -> None:
        if levels:
            ModBackupSave.fromstring(data, strict=False, on_campaign_conflict=unexpected)
        else:
            ModBackupSave.fromstring(data, strict=False, on_level_conflict=unexpected)

    with pytest.raises(BadProgressSave, match='Duplicate'):
        load()


@pytest.mark.parametrize('save_type', [MainSave, ModBackupSave])
@pytest.mark.parametrize('group', ['LevelSets', 'LevelSetRecycleBin'])
def test_campaign_and_level_callbacks_are_independent_within_one_load(
    save_type: type[MainSave | ModBackupSave], group: str
) -> None:
    calls = []

    def choose_campaign(
        old: CampaignConflictContext, new: CampaignConflictContext
    ) -> CampaignProgress:
        calls.append('campaign')
        return old.progress

    def choose_level(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        calls.append('level')
        return new.progress

    data = _document(
        save_type,
        f'<{group}>{_campaign(0, _area(1) + _area(2))}{_campaign(3)}</{group}>',
    )
    save = save_type.fromstring(
        data,
        strict=False,
        on_campaign_conflict=choose_campaign,
        on_level_conflict=choose_level,
    )
    collection = save.mods.campaigns if group == 'LevelSets' else save.mods.recycled_campaigns
    assert calls == ['level', 'campaign']
    assert require_map(collection['Pack'].levels['Pack/Map'].maps[0]).deaths == 2


@pytest.mark.parametrize('kind', ['campaign', 'level'])
@pytest.mark.parametrize('invalid', ['short', 'nil'])
def test_resolved_progress_can_be_repaired_after_loading(kind: str, invalid: str) -> None:
    level = LevelProgress('Pack/Map')
    if invalid == 'short':
        level.maps.pop()
    else:
        level.maps[1] = None

    def choose_campaign(
        old: CampaignConflictContext, new: CampaignConflictContext
    ) -> CampaignProgress:
        return CampaignProgress('Pack', [level])

    def choose_level(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        return level

    content = (
        _campaign(1) + _campaign(2) if kind == 'campaign' else _campaign(0, _area(1) + _area(2))
    )
    data = _document(ModBackupSave, f'<LevelSets>{content}</LevelSets>')
    save = ModBackupSave.fromstring(
        data,
        strict=False,
        on_campaign_conflict=choose_campaign,
        on_level_conflict=choose_level,
    )
    assert save.flat_levels['Pack/Map'] is level
    buffer = BytesIO(b'unchanged')
    with pytest.raises(BadProgressSave):
        save.write(buffer)
    assert buffer.getvalue() == b'unchanged'
    if invalid == 'short':
        level.maps.append(MapProgress())
    else:
        level.maps[1] = MapProgress()
    restored = ModBackupSave.fromstring(save.tostring())
    assert len(restored.flat_levels['Pack/Map'].maps) == 3


@pytest.mark.parametrize('save_type', [MainSave, ModBackupSave])
@pytest.mark.parametrize('kind', ['campaign', 'level', 'cross_campaign'])
@pytest.mark.parametrize('invalid', ['short', 'nil'])
def test_selecting_loaded_candidate_preserves_incomplete_maps(
    save_type: type[MainSave | ModBackupSave], kind: str, invalid: str
) -> None:
    modes = '<AreaModeStats Deaths="7"/>'
    if invalid == 'nil':
        modes += (
            '<AreaModeStats xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:nil="true"/><AreaModeStats/>'
        )
    area = f'<AreaStats SID="Pack/Map"><Modes>{modes}</Modes></AreaStats>'
    if kind == 'campaign':
        campaigns = _campaign(1) + _campaign(2, area)
    elif kind == 'level':
        campaigns = _campaign(0, _area(1) + area)
    else:
        campaigns = (
            f'<LevelSetStats Name="Wrong"><Areas>{_area(1)}</Areas></LevelSetStats>'
            + _campaign(0, area)
        )

    def latest_campaign(
        old: CampaignConflictContext, new: CampaignConflictContext
    ) -> CampaignProgress:
        return new.progress

    def latest_level(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        return new.progress

    save = save_type.fromstring(
        _document(save_type, f'<LevelSets>{campaigns}</LevelSets>'),
        strict=False,
        on_campaign_conflict=latest_campaign,
        on_level_conflict=latest_level,
    )
    level = save.flat_levels['Pack/Map']
    assert require_map(level.maps[0]).deaths == 7
    if invalid == 'short':
        assert len(level.maps) == 1
    else:
        assert level.maps[1] is None
    with pytest.raises(BadProgressSave):
        save.tostring()
    if invalid == 'short':
        level.maps.extend([MapProgress(), MapProgress()])
    else:
        level.maps[1] = MapProgress()
    restored = save_type.fromstring(save.tostring())
    assert require_map(restored.flat_levels['Pack/Map'].maps[0]).deaths == 7


def test_campaign_resolution_preserves_unrelated_misplaced_level_for_repair() -> None:
    def latest(old: CampaignConflictContext, new: CampaignConflictContext) -> CampaignProgress:
        return new.progress

    data = _document(
        ModBackupSave,
        f'<LevelSets>{_campaign(1)}{_campaign(2, _area(7, "Other/Only"))}</LevelSets>',
    )
    save = ModBackupSave.fromstring(data, strict=False, on_campaign_conflict=latest)
    level = save.mods.campaigns['Pack'].levels['Other/Only']
    assert save.flat_levels[level.sid] is level
    with pytest.raises(BadProgressSave):
        save.tostring()
    del save.mods.campaigns['Pack'].levels[level.sid]
    save.mods.campaigns.add(CampaignProgress('Other', [level]))
    restored = ModBackupSave.fromstring(save.tostring())
    assert require_map(restored.mods.campaigns['Other'].levels[level.sid].maps[0]).deaths == 7


def test_strict_rejects_duplicates_without_invoking_resolver() -> None:
    def unexpected(old: CampaignConflictContext, new: CampaignConflictContext) -> Never:
        pytest.fail('strict loading must not invoke a recovery callback')

    data = _document(ModBackupSave, f'<LevelSets>{_campaign(1)}{_campaign(2)}</LevelSets>')
    with pytest.raises(BadProgressSave, match='Duplicate'):
        ModBackupSave.read(BytesIO(data), on_campaign_conflict=unexpected)


def test_active_and_recycled_names_are_independent() -> None:
    data = _document(
        ModBackupSave,
        f'<LevelSets>{_campaign(1)}</LevelSets>'
        f'<LevelSetRecycleBin>{_campaign(2)}</LevelSetRecycleBin>',
    )
    save = ModBackupSave.read(BytesIO(data))
    assert save.mods.campaigns['Pack'].total_strawberries == 1
    assert save.mods.recycled_campaigns['Pack'].total_strawberries == 2


@pytest.mark.parametrize('invalid', [CampaignProgress('Other'), LevelProgress('Pack/Map'), None])
def test_resolver_cannot_change_identity_or_type(
    invalid: CampaignProgress | LevelProgress | None,
) -> None:
    def wrong(
        old: CampaignConflictContext, new: CampaignConflictContext
    ) -> CampaignProgress | LevelProgress | None:
        return invalid

    data = _document(ModBackupSave, f'<LevelSets>{_campaign(1)}{_campaign(2)}</LevelSets>')
    with pytest.raises(BadProgressSave, match='Invalid duplicate result'):
        ModBackupSave.read(
            BytesIO(data), strict=False, on_campaign_conflict=cast(CampaignConflictResolver, wrong)
        )


@pytest.mark.parametrize('scope', ['campaign', 'level', 'cross_campaign'])
@pytest.mark.parametrize('entry', ['read', 'fromstring', 'domain'])
@pytest.mark.parametrize('error_type', [ValueError, BadProgressSave, RuntimeError])
def test_callback_exception_propagates_without_writing(
    scope: str,
    entry: str,
    error_type: type[Exception],
) -> None:
    error = error_type('caller chose to stop')

    def stop(
        old: CampaignConflictContext | LevelConflictContext,
        new: CampaignConflictContext | LevelConflictContext,
    ) -> Never:
        raise error

    if scope == 'campaign':
        campaigns = _campaign(1) + _campaign(2)
    elif scope == 'level':
        campaigns = _campaign(0, _area(1) + _area(2))
    else:
        campaigns = (
            f'<LevelSetStats Name="Wrong"><Areas>{_area(1)}</Areas></LevelSetStats>'
            + _campaign(0, _area(2))
        )
    data = _document(ModBackupSave, f'<LevelSets>{campaigns}</LevelSets>')
    buffer = BytesIO(data)

    def load() -> None:
        if entry == 'read':
            ModBackupSave.read(
                buffer, strict=False, on_campaign_conflict=stop, on_level_conflict=stop
            )
        elif entry == 'fromstring':
            ModBackupSave.fromstring(
                data, strict=False, on_campaign_conflict=stop, on_level_conflict=stop
            )
        else:
            ModSaveData.from_xml(
                etree.fromstring(data),
                strict=False,
                on_campaign_conflict=stop,
                on_level_conflict=stop,
            )

    with pytest.raises(error_type) as caught:
        load()
    assert caught.value is error
    assert buffer.getvalue() == data and not buffer.closed


def test_cross_campaign_resolution_does_not_reinstall_unique_misplaced_or_short_levels() -> None:
    unique = (
        '<AreaStats SID="Elsewhere/Only"><Modes><AreaModeStats Deaths="7"/></Modes></AreaStats>'
    )
    data = _document(
        ModBackupSave,
        f'<LevelSets><LevelSetStats Name="Wrong"><Areas>{unique}{_area(1)}</Areas>'
        f'</LevelSetStats>{_campaign(0, _area(2))}</LevelSets>',
    )

    def latest(
        old: LevelConflictContext,
        new: LevelConflictContext,
    ) -> LevelProgress:
        assert old.progress.key == new.progress.key == 'Pack/Map'
        return new.progress

    save = ModBackupSave.fromstring(data, strict=False, on_level_conflict=latest)
    wrong = save.mods.campaigns['Wrong']
    assert list(wrong.levels) == ['Elsewhere/Only']
    level = wrong.levels['Elsewhere/Only']
    assert len(level.maps) == 1 and require_map(level.maps[0]).deaths == 7
    assert save.flat_levels[level.sid] is level
    assert require_map(save.mods.campaigns['Pack'].levels['Pack/Map'].maps[0]).deaths == 2


@pytest.mark.parametrize('invalid', [CampaignProgress('Pack'), LevelProgress('Other/Map'), None])
def test_cross_campaign_callback_return_value_is_checked_without_exception_wrapping(
    invalid: CampaignProgress | LevelProgress | None,
) -> None:
    def wrong(
        old: LevelConflictContext,
        new: LevelConflictContext,
    ) -> CampaignProgress | LevelProgress | None:
        return invalid

    data = _document(
        ModBackupSave,
        '<LevelSets>'
        f'<LevelSetStats Name="Wrong"><Areas>{_area(1)}</Areas></LevelSetStats>'
        f'{_campaign(0, _area(2))}</LevelSets>',
    )
    with pytest.raises(BadProgressSave, match='Invalid duplicate result'):
        ModSaveData.from_xml(
            etree.fromstring(data),
            strict=False,
            on_level_conflict=cast(LevelConflictResolver, wrong),
        )


def test_slots_forward_callback_for_main_backup_and_vanilla(tmp_path: Path) -> None:
    def latest_campaign(
        old: CampaignConflictContext, new: CampaignConflictContext
    ) -> CampaignProgress:
        assert new.partition is ProgressPartition.MODS
        return new.progress

    def latest_level(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        assert new.partition is ProgressPartition.VANILLA
        return new.progress

    main = _document(
        MainSave,
        '<Areas>' + _area(1, 'Celeste/Map') + _area(2, 'Celeste/Map') + '</Areas>',
        complete=False,
    )
    backup = _document(ModBackupSave, f'<LevelSets>{_campaign(1)}{_campaign(2)}</LevelSets>')
    (tmp_path / '0.celeste').write_bytes(main)
    (tmp_path / '0-modsavedata.celeste').write_bytes(backup)
    slot = SaveSlot(tmp_path, 0)
    save = slot.main(strict=False, on_level_conflict=latest_level)
    assert require_map(save.vanilla.levels['Celeste/Map'].maps[0]).deaths == 2
    save = slot.backup(strict=False, on_campaign_conflict=latest_campaign)
    assert save.mods.campaigns['Pack'].total_strawberries == 2


def test_mapped_vanilla_duplicate_can_return_an_independent_merged_level() -> None:
    def merge(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
        assert new.partition is ProgressPartition.VANILLA
        result = deepcopy(new.progress)
        require_map(result.maps[0]).deaths += require_map(old.progress.maps[0]).deaths
        return result

    area = _area(2, 'Celeste/Map').replace('SID="Celeste/Map" ID="2"', 'ID="1"')
    data = _document(
        MainSave, '<Areas>' + area + _area(3, 'Celeste/1-ForsakenCity') + '</Areas>', complete=False
    )
    buffer = BytesIO(data)
    save = MainSave.read(buffer, strict=False, on_level_conflict=merge)
    assert require_map(save.vanilla.levels['Celeste/1-ForsakenCity'].maps[0]).deaths == 5
    # Duplicate resolution does not implicitly repair a partial vanilla layout.
    levels = MainSave().vanilla.levels
    levels.replace(save.vanilla.levels['Celeste/1-ForsakenCity'])
    save.vanilla.levels = levels
    buffer.seek(0)
    save.write(buffer)
    buffer.truncate()
    root = etree.fromstring(buffer.getvalue())
    assert len(root.findall('Areas/AreaStats')) == 11
    buffer.seek(0)
    save = MainSave.read(buffer)
    assert require_map(save.vanilla.levels['Celeste/1-ForsakenCity'].maps[0]).deaths == 5
