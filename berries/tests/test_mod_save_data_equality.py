"""Mod data compares modeled values, not its member/index identities."""

from copy import deepcopy

import pytest

from berries.game.levels import LevelSide
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress import CampaignProgress, LevelProgress, UnresolvedLevelProgress
from berries.game.saves.progress.map_ref import MapRef
from test_support.save_docs import parsed_session, require_map


def _data() -> ModSaveData:
    result = ModSaveData(safe_last_map_ref=MapRef(level_sid='Pack/One', side=LevelSide.A))
    result.campaigns.add(
        CampaignProgress('Pack', [LevelProgress('Pack/One'), LevelProgress('Pack/Two')])
    )
    result.recycled_campaigns.add(CampaignProgress('Old', [LevelProgress('Old/Map')]))
    result.vanilla_entry = CampaignProgress('Celeste', poem=['a', 'b'])
    result.recycled_campaigns.add(CampaignProgress('Celeste', poem=['old']))
    result.safe_session = parsed_session('Pack/One', LevelSide.A)
    return result


def test_independent_data_compare_by_value_and_are_unhashable() -> None:
    snapshot = _data()
    assert snapshot == _data() == deepcopy(snapshot)
    assert snapshot != object()
    with pytest.raises(TypeError):
        hash(snapshot)


@pytest.mark.parametrize('recycled', [False, True])
def test_nested_progress_changes_are_detected_and_can_be_reverted(recycled: bool) -> None:
    snapshot = _data()
    baseline = deepcopy(snapshot)
    levels = snapshot.recycled_flat_levels if recycled else snapshot.flat_levels
    sid = 'Old/Map' if recycled else 'Pack/One'
    progress = require_map(levels[sid].maps[0])
    progress.deaths += 1
    assert snapshot != baseline
    progress.deaths -= 1
    assert snapshot == baseline


def test_member_order_and_reference_values_are_part_of_data() -> None:
    snapshot = _data()
    baseline = deepcopy(snapshot)
    levels = snapshot.campaigns['Pack'].levels
    first = levels.pop('Pack/One')
    levels.add(first)
    assert snapshot != baseline
    snapshot = deepcopy(baseline)
    snapshot.safe_last_map_ref = MapRef(level_id=1, side=LevelSide.A, level_sid='Pack/One')
    assert snapshot != baseline
    snapshot = deepcopy(baseline)
    assert snapshot.vanilla_entry is not None
    snapshot.vanilla_entry.poem.reverse()
    assert snapshot != baseline
    snapshot = deepcopy(baseline)
    snapshot.recycled_campaigns['Celeste'].poem.append('new')
    assert snapshot != baseline


def test_session_values_compare_editable_fields() -> None:
    snapshot = _data()
    other = _data()
    assert snapshot.safe_session is not None and other.safe_session is not None
    snapshot.safe_session.dark_room_alpha = 0.5
    other.safe_session.dark_room_alpha = 0.5
    snapshot.safe_session.sliders['test'] = 2.0
    other.safe_session.sliders['test'] = 2.0
    assert snapshot == other
    other.safe_session.deaths += 1
    assert snapshot != other


def test_unresolved_progress_compares_values_without_serializing() -> None:
    snapshot = _data()
    unresolved = UnresolvedLevelProgress(99)
    unresolved.maps.append(None)
    snapshot.campaigns['Pack'].unresolved_levels.append(unresolved)
    other = deepcopy(snapshot)
    assert snapshot == other
    require_map(other.campaigns['Pack'].unresolved_levels[0].maps[0]).deaths += 1
    assert snapshot != other
    other = deepcopy(snapshot)
    other.campaigns['Pack'].unresolved_levels[0].maps.pop()
    assert snapshot != other
