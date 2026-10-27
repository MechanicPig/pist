"""Independent progress actions share preparation without partially editing saves."""

from copy import copy, deepcopy
from typing import Literal

import pytest

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves import operations as ops
from berries.game.saves.errors import BadProgressSave, ProgressConflictError
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress import CampaignProgress, LevelProgress, PersonalBests
from berries.game.saves.rooms import RoomStats, room_key
from berries.game.saves.unknown import UnknownSaveFieldError
from berries.map_entity_id import MapEntityID
from test_support.save_docs import (
    complete_vanilla,
    parsed_session,
    read_save,
    require_map,
    save_bytes,
)


def _save(*, empty: bool = False) -> MainSave:
    save = read_save(
        MainSave,
        complete_vanilla('<SaveData><Time>100</Time><TotalDeaths>100</TotalDeaths></SaveData>'),
    )
    level = LevelProgress('Pack/Map', cassette=not empty)
    campaign = CampaignProgress('Pack', [level], total_strawberries=0 if empty else 3)
    if not empty:
        campaign.poem = ['pack_map_a', 'pack_map_b']
        for progress in level.maps:
            assert progress is not None
            progress.time_played = Duration.from_dotnet_ticks(10)
            progress.deaths = 2
            progress.strawberries.add(MapEntityID('r', 1))
    save.mods.campaigns.add(campaign)
    return save


@pytest.mark.parametrize('size', [0, 1, 2])
def test_operations_reject_truncated_positions_without_changing_totals(size: int) -> None:
    source, target = _save(), _save(empty=True)
    level = source.mods.campaigns['Pack'].levels['Pack/Map']
    del level.maps[size:]
    before_target = save_bytes(target)
    totals = source.time_played, source.deaths
    with pytest.raises(BadProgressSave):
        ops.clear_level_progress(source, level.sid, poem_ids={}, goldenberry_ids={})
    with pytest.raises(BadProgressSave):
        ops.copy_level_progress(
            source, target, level.sid, poem_ids={}, mode='overwrite', goldenberry_ids={}
        )
    assert (source.time_played, source.deaths) == totals
    assert source.mods.campaigns['Pack'].levels[level.sid] is level
    assert save_bytes(target) == before_target


@pytest.mark.parametrize('remove_source', (False, True))
def test_transfer_rejects_shared_empty_level_collection(remove_source: bool) -> None:
    source, target = _save(), _save(empty=True)
    campaign = CampaignProgress('Empty')
    source.mods.campaigns.add(campaign)
    target.mods.campaigns.add(copy(campaign))
    before = save_bytes(source), save_bytes(target)
    operation = ops.move_campaign_progress if remove_source else ops.copy_campaign_progress
    with pytest.raises(ProgressConflictError, match='overlap'):
        operation(source, target, 'Empty', mode='overwrite', goldenberry_ids={})
    assert (save_bytes(source), save_bytes(target)) == before


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
@pytest.mark.parametrize('mode', ('overwrite', 'merge', 'exclusive'))
def test_copy_preserves_source_bytes_references_and_totals(
    scope: str,
    mode: Literal['overwrite', 'merge', 'exclusive'],
) -> None:
    source, target = _save(), _save(empty=True)
    held = require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A])
    before = save_bytes(source)
    if scope == 'map':
        ops.copy_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            mode=mode,
            poem_id=ops.DEFAULT_POEM_ID,
            goldenberry_ids={},
        )
    elif scope == 'level':
        ops.copy_level_progress(
            source, target, 'Pack/Map', mode=mode, poem_ids={}, goldenberry_ids={}
        )
    else:
        ops.copy_campaign_progress(source, target, 'Pack', mode=mode, goldenberry_ids={})
    assert require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A]) is held
    assert save_bytes(source) == before
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 2
    assert target.flat_levels['Pack/Map'].cassette
    assert target.deaths == (102 if scope == 'map' else 106)
    restored = read_save(MainSave, save_bytes(target))
    assert require_map(restored.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 2
    require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).strawberries.clear()
    assert held.strawberries == {MapEntityID('r', 1)}


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
def test_clear_queries_and_global_counters_are_scope_specific(scope: str) -> None:
    save = _save()
    if scope == 'map':
        assert not ops.is_empty_map_progress(save, 'Pack/Map', LevelSide.A, poem_id=None)
        ops.clear_map_progress(save, 'Pack/Map', LevelSide.A, poem_id=None, goldenberry_ids={})
        assert ops.is_empty_map_progress(save, 'Pack/Map', LevelSide.A, poem_id=None)
        assert not ops.is_empty_level_progress(save, 'Pack/Map', poem_ids={})
        assert save.deaths == 98 and not save.flat_levels['Pack/Map'].cassette
        assert require_map(save.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 2
    elif scope == 'level':
        ops.clear_level_progress(save, 'Pack/Map', poem_ids={}, goldenberry_ids={})
        assert ops.is_empty_level_progress(save, 'Pack/Map', poem_ids={})
        assert not ops.is_empty_campaign_progress(save, 'Pack')  # poem deliberately retained
        assert save.deaths == 94
    else:
        ops.clear_campaign_progress(save, 'Pack', goldenberry_ids={})
        assert ops.is_empty_campaign_progress(save, 'Pack')
        assert save.deaths == 94
    assert read_save(MainSave, save_bytes(save)).deaths == save.deaths


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
@pytest.mark.parametrize('merge', (False, True))
def test_named_merge_and_overwrite_leave_source_unchanged(scope: str, merge: bool) -> None:
    source, target = _save(), _save()
    require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths = 7
    before = save_bytes(source)
    if scope == 'map':
        action = ops.merge_map_progress if merge else ops.overwrite_map_progress
        action(source, target, 'Pack/Map', LevelSide.A, poem_id=None, goldenberry_ids={})
    elif scope == 'level':
        action = ops.merge_level_progress if merge else ops.overwrite_level_progress
        action(source, target, 'Pack/Map', poem_ids={}, goldenberry_ids={})
    else:
        action = ops.merge_campaign_progress if merge else ops.overwrite_campaign_progress
        action(source, target, 'Pack', goldenberry_ids={})
    assert save_bytes(source) == before
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == (
        9 if merge else 7
    )


def test_target_conflict_has_a_recoverable_error_type_without_partial_edits() -> None:
    source, target = _save(), _save()
    before = save_bytes(source), save_bytes(target)
    with pytest.raises(ProgressConflictError, match='not empty'):
        ops.copy_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            poem_id=None,
            mode='exclusive',
            goldenberry_ids={},
        )
    assert (save_bytes(source), save_bytes(target)) == before
    target.session = parsed_session('Pack/Map', LevelSide.A)
    before = save_bytes(source), save_bytes(target)
    with pytest.raises(ProgressConflictError, match='current session'):
        ops.copy_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            poem_id=None,
            mode='overwrite',
            goldenberry_ids={},
        )
    assert (save_bytes(source), save_bytes(target)) == before


def test_merge_constructs_independent_values_without_editing_held_inputs() -> None:
    source, target = _save(), _save()
    incoming = require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A])
    previous = require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A])
    incoming.checkpoints = {'new'}
    previous.checkpoints = {'old'}
    incoming.single_run_completed = previous.single_run_completed = True
    incoming.bests = PersonalBests(Duration.from_dotnet_ticks(5), Duration(), 0, 4)
    previous.bests = PersonalBests(Duration.from_dotnet_ticks(8), Duration(), 3, 0)
    before = save_bytes(source)
    ops.merge_map_progress(
        source, target, 'Pack/Map', LevelSide.A, poem_id=None, goldenberry_ids={}
    )
    merged = require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A])
    assert merged.bests == PersonalBests(Duration.from_dotnet_ticks(5), Duration(), 0, 0)
    assert merged.bests is not incoming.bests and merged.bests is not previous.bests
    assert merged.checkpoints == {'new', 'old'}
    merged.strawberries.clear()
    merged.checkpoints.clear()
    merged.bests.deaths = 9
    assert save_bytes(source) == before
    assert previous.strawberries == {MapEntityID('r', 1)}
    assert previous.checkpoints == {'old'} and previous.bests.deaths == 3


def test_failed_merge_after_value_construction_keeps_both_histories_intact() -> None:
    source, target = _save(), _save()
    incoming = require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A])
    incoming.strawberries.add(MapEntityID('new', 2))
    incoming.checkpoints.add('new')
    previous = require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A])
    target.deaths = 2**31 - 1
    before = save_bytes(source), save_bytes(target)
    with pytest.raises(ValueError):
        ops.merge_map_progress(
            source, target, 'Pack/Map', LevelSide.A, poem_id=None, goldenberry_ids={}
        )
    assert (save_bytes(source), save_bytes(target)) == before
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]) is previous


def test_exclusive_b_allows_existing_a_progress_cassette_and_session() -> None:
    source, target = _save(), _save(empty=True)
    require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths = 1
    target.flat_levels['Pack/Map'].cassette = True
    target.session = parsed_session('Pack/Map', LevelSide.A)
    ops.move_map_progress(
        source, target, 'Pack/Map', LevelSide.B, mode='exclusive', poem_id=None, goldenberry_ids={}
    )
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 1
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 2
    assert target.flat_levels['Pack/Map'].cassette


def test_copy_does_not_require_source_global_subtraction_to_be_possible() -> None:
    source, target = _save(), _save(empty=True)
    source.deaths = 0
    source.mods.campaigns['Pack'].total_strawberries = 0
    before = save_bytes(source)
    ops.copy_level_progress(
        source, target, 'Pack/Map', mode='overwrite', poem_ids={}, goldenberry_ids={}
    )
    assert save_bytes(source) == before
    assert target.deaths == 106
    unchanged = save_bytes(target)
    with pytest.raises(ValueError):
        ops.move_level_progress(
            source, target, 'Pack/Map', mode='overwrite', poem_ids={}, goldenberry_ids={}
        )
    assert (save_bytes(source), save_bytes(target)) == (before, unchanged)


def test_copy_blender_preserves_all_source_collections_and_detaches_target_records() -> None:
    source, target = _save(), _save(empty=True)
    a, b = read_save(EndersBlenderSave, '{}'), read_save(EndersBlenderSave, '{}')
    a.first_clear[room_key('Pack/Map', LevelSide.A)] = RoomStats(['r'], {'r': 1}, {'r': Duration()})
    a.last_saved[room_key('Pack/Map', LevelSide.B)] = RoomStats(['b'], {'b': 0}, {'b': Duration()})
    a.room_names['Pack/Map'] = {'r': 'Room'}
    a.room_colors['Pack/Map'] = {'r': 2}
    before = save_bytes(source), save_bytes(a)
    ops.copy_level_progress(
        source,
        target,
        'Pack/Map',
        mode='overwrite',
        poem_ids={},
        source_blender=a,
        target_blender=b,
        goldenberry_ids={},
    )
    assert (save_bytes(source), save_bytes(a)) == before
    assert b.last_saved[room_key('Pack/Map', LevelSide.B)].room_order == ['b']
    b.first_clear['Pack/Map'].room_deaths['r'] = 5
    b.room_names['Pack/Map']['r'] = 'Changed'
    assert a.first_clear['Pack/Map'].room_deaths == {'r': 1}
    assert a.room_names['Pack/Map'] == {'r': 'Room'}


def test_queries_report_missing_as_empty_but_unknown_input_is_rejected() -> None:
    save = _save(empty=True)
    assert ops.is_empty_map_progress(save, 'Pack/Missing', LevelSide.A, poem_id=None)
    assert ops.is_empty_campaign_progress(save, 'Missing')
    before, separator, mods = save_bytes(save).partition(b'<LevelSets>')
    with pytest.raises(UnknownSaveFieldError):
        read_save(
            MainSave,
            before + separator + mods.replace(b'Deaths="0"', b'Deaths="0" Unknown="keep"', 1),
        )


def test_clear_failure_leaves_all_supported_progress_and_counters_unchanged() -> None:
    save = _save()
    save.deaths = 1
    before = save_bytes(save)
    with pytest.raises(ValueError):
        ops.clear_campaign_progress(save, 'Pack', goldenberry_ids={})
    assert save_bytes(save) == before


@pytest.mark.parametrize('action', ('copy', 'move', 'clear'))
def test_single_map_actions_preserve_unmodified_member_references(action: str) -> None:
    source, target = _save(), _save(empty=True)
    save = source if action == 'clear' else target
    campaign = save.mods.campaigns['Pack']
    unrelated = LevelProgress('Pack/Other')
    campaign.levels.add(unrelated)
    level = campaign.levels['Pack/Map']
    kept = level.maps[1:]
    poem = campaign.poem
    if action == 'clear':
        ops.clear_map_progress(source, level.sid, LevelSide.A, poem_id=None, goldenberry_ids={})
    else:
        operation = ops.copy_map_progress if action == 'copy' else ops.move_map_progress
        operation(
            source,
            target,
            level.sid,
            LevelSide.A,
            mode='overwrite',
            poem_id=None,
            goldenberry_ids={},
        )
    installed = save.mods.campaigns['Pack']
    assert installed.levels[unrelated.sid] is unrelated
    assert all(
        old is new for old, new in zip(kept, installed.levels[level.sid].maps[1:], strict=True)
    )
    assert installed.poem is poem


@pytest.mark.parametrize('invalid', ('poem', 'counter', 'unrelated_map'))
def test_map_copy_preflights_the_whole_candidate_campaign_before_installation(invalid: str) -> None:
    source, target = _save(), _save(empty=True)
    campaign = target.mods.campaigns['Pack']
    unrelated = LevelProgress('Pack/Other')
    campaign.levels.add(unrelated)
    if invalid == 'poem':
        campaign.poem.append('invalid\x01')
    elif invalid == 'counter':
        campaign.unlocked_levels = -1
    else:
        require_map(unrelated.maps[LevelSide.C]).bests.dashes = -1
    before = deepcopy(source), deepcopy(target)
    held = target.flat_levels['Pack/Map']
    with pytest.raises(ValueError):
        ops.copy_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            mode='overwrite',
            poem_id=None,
            goldenberry_ids={},
        )
    assert (source, target) == before
    assert target.mods.campaigns['Pack'] is campaign
    assert target.flat_levels['Pack/Map'] is held
    assert target.flat_levels[unrelated.sid] is unrelated
