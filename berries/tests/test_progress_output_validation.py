"""Output preflights enforce the encoder contract without XML conversion."""

from copy import deepcopy
from fractions import Fraction
from typing import cast

import pytest

from berries.game.duration import Duration
from berries.game.saves.progress import LevelProgress, MapProgress, PersonalBests
from berries.map_entity_id import MapEntityID


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('deaths', -1),
        ('deaths', 2**31),
        ('completed', 'invalid'),
        ('total_strawberries', -1),
        ('time_played', Duration.from_seconds(Fraction(1, 3))),
        ('time_played', Duration.from_dotnet_ticks(2**63)),
        ('bests.time', Duration.from_seconds(Fraction(1, 3))),
        ('bests.full_clear_time', Duration.from_dotnet_ticks(-(2**63) - 1)),
        ('bests.deaths', -1),
        ('bests.dashes', 2**31),
        ('strawberries', {MapEntityID('bad:room', 1)}),
        ('strawberries', {MapEntityID('room', -1)}),
        ('strawberries', {MapEntityID('room', True)}),
        ('strawberries', {MapEntityID('room\x01', 1)}),
        ('checkpoints', {'room\x01'}),
    ),
)
def test_map_preflight_and_encoder_reject_unwritable_values_without_mutation(
    field: str, value: object
) -> None:
    progress = MapProgress()
    parent, _, name = field.rpartition('.')
    setattr(progress.bests if parent else progress, name, value)
    baseline = deepcopy(progress)
    with pytest.raises(ValueError):
        progress.validate_output()
    with pytest.raises(ValueError):
        progress.to_xml()
    assert progress == baseline


def test_output_preflight_allows_exact_signed_times_without_timer_frame_alignment() -> None:
    progress = MapProgress(
        time_played=Duration.from_dotnet_ticks(-1),
        bests=PersonalBests(
            time=Duration.from_dotnet_ticks(1), full_clear_time=Duration.from_dotnet_ticks(-3)
        ),
        strawberries=[MapEntityID('room', 1)],
        total_strawberries=6,
    )
    baseline = deepcopy(progress)
    progress.validate_output()
    assert MapProgress.from_xml(progress.to_xml(), strict=False) == baseline
    assert progress == baseline


@pytest.mark.parametrize('kind', ('short', 'long', 'null', 'id', 'cassette', 'sid'))
def test_level_preflight_and_encoder_agree_on_unwritable_structure(kind: str) -> None:
    level = LevelProgress('Pack/Map\x01' if kind == 'sid' else 'Pack/Map')
    if kind == 'short':
        level.maps.pop()
    elif kind == 'long':
        level.maps.append(MapProgress())
    elif kind == 'null':
        level.maps[1] = None
    elif kind == 'id':
        level.id = 2**31
    elif kind == 'cassette':
        level.cassette = cast(bool, 'invalid')
    baseline = deepcopy(level)
    with pytest.raises(ValueError):
        level.validate_output()
    with pytest.raises(ValueError):
        level.to_xml()
    assert level == baseline
