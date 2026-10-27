"""Progress values use Python equality and remain distinct mutable instances."""

from collections.abc import Callable
from copy import deepcopy

import pytest

from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress, UnresolvedLevelProgress
from berries.game.saves.progress.map import MapProgress
from berries.game.saves.progress.session import Session
from test_support.save_docs import parsed_session

type Progress = MapProgress | LevelProgress | UnresolvedLevelProgress | CampaignProgress | Session


@pytest.mark.parametrize(
    'factory',
    [
        MapProgress,
        lambda: LevelProgress('Pack/Map'),
        UnresolvedLevelProgress,
        lambda: CampaignProgress('Pack', [LevelProgress('Pack/Map')]),
        lambda: parsed_session('Pack/Map', LevelSide.A),
    ],
    ids=['map', 'level', 'unresolved', 'campaign', 'session'],
)
def test_progress_values_compare_independently_and_are_unhashable(
    factory: Callable[[], Progress],
) -> None:
    value = factory()
    other = factory()
    assert value is not other and value == other == deepcopy(value)
    assert value.__eq__(object()) is NotImplemented
    assert value != object()
    assert not hasattr(value, 'same_progress')
    with pytest.raises(TypeError):
        hash(value)


def test_ordered_level_maps_use_value_comparison_including_missing_entries() -> None:
    value = LevelProgress('Pack/Map', [MapProgress(deaths=1), None])
    other = deepcopy(value)
    assert value == other
    other.maps.reverse()
    assert value != other
    other.maps.reverse()
    other.maps.append(None)
    assert value != other
    other.maps.pop()
    assert value == other


def test_campaign_equality_ignores_index_associations_but_preserves_member_order() -> None:
    value = CampaignProgress('Pack', [LevelProgress('Pack/One'), LevelProgress('Pack/Two')])
    other = deepcopy(value)
    save = MainSave()
    save.mods.campaigns.add(value)
    assert value == other
    first = other.levels.pop('Pack/One')
    other.levels.add(first)
    assert value != other


def test_session_edit_baseline_is_not_part_of_equality() -> None:
    value = parsed_session('Pack/Map', LevelSide.A)
    value.deaths = 1
    other = Session.from_xml(tree.parse(value.tostring()).getroot())
    assert any(value.iter_changed_fields()) and not any(other.iter_changed_fields())
    assert value == other
    other.deaths = 2
    assert value != other


def test_map_values_include_historical_strawberry_offset_and_bests() -> None:
    value = MapProgress(total_strawberries=2)
    other = MapProgress(total_strawberries=3)
    assert value != other
    other.total_strawberries = 2
    assert value == other
    other.bests.deaths = 1
    assert value != other
