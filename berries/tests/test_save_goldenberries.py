"""Explicit goldenberry IDs coordinate cumulative counts without entity classification."""

import pytest
from lxml import etree

from berries.game.levels import LevelSide
from berries.game.saves import operations as ops
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress import CampaignProgress, LevelProgress
from berries.map_entity_id import MapEntityID
from test_support.save_docs import require_map

_G1 = MapEntityID('r', 1)
_G2 = MapEntityID('r', 2)
_G3 = MapEntityID('r', 3)
_G4 = MapEntityID('r', 4)
_RED = MapEntityID('r', 5)
_OTHER_RED = MapEntityID('r', 6)
_IDS: ops.GoldenberryIDs = {
    ('Pack/Map', LevelSide.A): {_G1, _G2, _G3},
    ('Pack/Map', LevelSide.B): {_G1, _G3},
    ('Pack/Extra', LevelSide.A): {_G4},
}


def _save(*, source: bool, empty: bool = False) -> MainSave:
    save = MainSave()
    level = LevelProgress('Pack/Map')
    if not empty:
        require_map(level.maps[LevelSide.A]).strawberries = (
            {_G1, _G2, _RED} if source else {_G1, _G3, _OTHER_RED}
        )
        require_map(level.maps[LevelSide.B]).strawberries = {_G1} if source else {_G3}
    levels = [level]
    if not source and not empty:
        extra = LevelProgress('Pack/Extra')
        require_map(extra.maps[LevelSide.A]).strawberries = {_G4}
        levels.append(extra)
    save.mods.campaigns.add(
        CampaignProgress('Pack', levels, total_strawberries=0 if empty else 11 if source else 14)
    )
    save.total_goldenberries = 13 if source else 20 if empty else 24
    return save


def test_main_goldenberry_count_has_independent_storage_and_encoding() -> None:
    save = MainSave()
    assert save.total_goldenberries == 0
    save.total_goldenberries = 12
    save.vanilla.total_strawberries = 30
    save.mods.campaigns.add(CampaignProgress('Pack', total_strawberries=7))
    encoded = save.tostring()
    root = etree.fromstring(encoded)
    assert root.findtext('TotalGoldenStrawberries') == '12'
    assert root.findtext('TotalStrawberries') == '30'
    reread = MainSave.fromstring(encoded)
    assert reread.total_goldenberries == 12
    assert reread.vanilla.total_strawberries == 30
    assert reread.mods.campaigns['Pack'].total_strawberries == 7


def test_missing_goldenberry_count_is_only_allowed_in_lenient_input() -> None:
    root = etree.fromstring(MainSave().tostring())
    count = root.find('TotalGoldenStrawberries')
    assert count is not None
    root.remove(count)
    data = etree.tostring(root)
    with pytest.raises(BadProgressSave, match='TotalGoldenStrawberries'):
        MainSave.fromstring(data)
    assert MainSave.fromstring(data, strict=False).total_goldenberries == 0


@pytest.mark.parametrize('value', [-1, 2**31, True])
def test_goldenberry_count_is_validated_at_write_boundary(value: int) -> None:
    save = MainSave()
    save.total_goldenberries = value
    with pytest.raises(ValueError):
        save.tostring()


@pytest.mark.parametrize('scope', ['map', 'level', 'campaign'])
@pytest.mark.parametrize('mode', ['merge', 'overwrite', 'exclusive'])
@pytest.mark.parametrize('move', [False, True])
def test_transfer_updates_only_affected_goldenberry_counts(
    scope: str, mode: ops.TransferMode, move: bool
) -> None:
    source, target = _save(source=True), _save(source=False, empty=mode == 'exclusive')
    source_before = source.tostring()
    target_before = target.total_goldenberries
    if scope == 'map':
        action = ops.move_map_progress if move else ops.copy_map_progress
        action(
            source, target, 'Pack/Map', LevelSide.A, mode=mode, poem_id=None, goldenberry_ids=_IDS
        )
    elif scope == 'level':
        action = ops.move_level_progress if move else ops.copy_level_progress
        action(source, target, 'Pack/Map', mode=mode, poem_ids={}, goldenberry_ids=_IDS)
    else:
        action = ops.move_campaign_progress if move else ops.copy_campaign_progress
        action(source, target, 'Pack', mode=mode, goldenberry_ids=_IDS)
    source_goldenberries = 2 if scope == 'map' else 3
    if mode == 'exclusive':
        target_delta = source_goldenberries
    elif mode == 'merge':
        target_delta = 1 if scope == 'map' else 2
    else:
        target_delta = -1 if scope == 'campaign' else 0
    assert target.total_goldenberries == target_before + target_delta
    assert source.total_goldenberries == 13 - (source_goldenberries if move else 0)
    assert source.vanilla.total_strawberries == target.vanilla.total_strawberries == 0
    if not move:
        assert source.tostring() == source_before
    for save in (source, target):
        reread = MainSave.fromstring(save.tostring())
        assert reread.total_goldenberries == save.total_goldenberries
        assert (
            reread.mods.campaigns['Pack'].total_strawberries
            == save.mods.campaigns['Pack'].total_strawberries
        )


@pytest.mark.parametrize('scope', ['map', 'level', 'campaign'])
def test_clear_subtracts_goldenberries_and_preserves_historical_offset(scope: str) -> None:
    save = _save(source=True)
    if scope == 'map':
        ops.clear_map_progress(save, 'Pack/Map', LevelSide.A, poem_id=None, goldenberry_ids=_IDS)
    elif scope == 'level':
        ops.clear_level_progress(save, 'Pack/Map', poem_ids={}, goldenberry_ids=_IDS)
    else:
        ops.clear_campaign_progress(save, 'Pack', goldenberry_ids=_IDS)
    assert save.total_goldenberries == (11 if scope == 'map' else 10)
    assert save.mods.campaigns['Pack'].total_strawberries == (8 if scope == 'map' else 7)
    assert MainSave.fromstring(save.tostring()).total_goldenberries == save.total_goldenberries


@pytest.mark.parametrize('failure', ['source_underflow', 'target_underflow', 'target_overflow'])
def test_invalid_goldenberry_delta_does_not_modify_either_save(failure: str) -> None:
    source, target = _save(source=True), _save(source=False)
    mode: ops.TransferMode = 'merge'
    if failure == 'source_underflow':
        source.total_goldenberries = 1
    elif failure == 'target_underflow':
        require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A]).strawberries.clear()
        target.total_goldenberries = 0
        mode = 'overwrite'
    else:
        target.total_goldenberries = 2**31 - 1
    before = source.tostring(), target.tostring()
    with pytest.raises(ValueError):
        ops.move_map_progress(
            source, target, 'Pack/Map', LevelSide.A, mode=mode, poem_id=None, goldenberry_ids=_IDS
        )
    assert (source.tostring(), target.tostring()) == before


def test_classification_is_not_inferred_or_required_for_unlisted_ids() -> None:
    source, target = _save(source=True), _save(source=False, empty=True)
    ops.copy_map_progress(
        source, target, 'Pack/Map', LevelSide.A, mode='merge', poem_id=None, goldenberry_ids={}
    )
    assert target.total_goldenberries == 20
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).strawberries == {
        _G1,
        _G2,
        _RED,
    }
