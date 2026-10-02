from pathlib import Path

import pytest
from pydantic import ValidationError

from berries.game.content import ContentPath, GameContent
from berries.game.duration import Duration
from berries.game.enders_blender import EndersBlenderReader, EndersBlenderSave
from berries.game.levels import Level, LevelSide, Map
from berries.game.maps import MapInfo
from test_support.map_factory import make_level_side


def test_first_clear_stats_distinguishes_final_and_inferred_map_identities() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Local/Map-B.bin'))
    map_file = Map(map_info, GameContent(Path('Content')))
    level = Level('Final/Map', 'Final_Map', (map_file, map_file))
    time = Duration.from_milliseconds(2500)
    save = EndersBlenderSave(
        0,
        {'Final/Map_B': ('start', 'end'), 'Local/Map_B': ('raw',)},
        {'Final/Map_B': {'start': 0, 'end': 2}},
        {'Final/Map_B': {'start': time}},
    )

    stats = save.first_clear_stats(level, LevelSide.B)
    assert stats.room_order == ('start', 'end')
    assert stats.room_deaths == {'start': 0, 'end': 2}
    assert stats.room_times == {'start': time}
    assert stats.room_times['start'] == save.first_clear_room_time(level, LevelSide.B, 'start')
    assert stats.room_deaths['start'] == save.first_clear_room_death(level, LevelSide.B, 'start')
    raw_stats = save.first_clear_stats_for_file(map_info)
    assert raw_stats.room_order == ('raw',)
    assert raw_stats.room_deaths == {}
    assert raw_stats.room_times == {}

    missing = save.first_clear_stats(level, LevelSide.A)
    assert missing.room_order == ()
    assert missing.room_deaths == {}
    assert missing.room_times == {}


def test_enders_blender_reader_loads_first_clear_room_order(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '2-modsave-EndersBlender.celeste').write_text(
        """mapDict_roomStat_firstClear_roomOrder:
  Author/Pack/Map_B:
  - start
  - middle
  - goal
  Author/Pack/Numbers:
  - 0
  - 1
mapDict_roomStat_firstClear_death:
  Author/Pack/Map_B:
    start: 4
mapDict_roomStat_firstClear_timer:
  Author/Pack/Map_B:
    start: 25500000
""",
        encoding='utf-8',
    )

    reader = EndersBlenderReader(tmp_path / 'Celeste')
    save = reader.load(2)

    assert reader.available_numbers() == [2]
    assert save.first_clear_room_order(
        *make_level_side(file_path='Maps/Author/Pack/Map-B.bin')
    ) == ('start', 'middle', 'goal')
    assert save.first_clear_room_order(*make_level_side(file_path='Maps/Author/Pack/Map.bin')) == ()
    assert save.first_clear_room_order(
        *make_level_side(file_path='Maps/Author/Pack/Numbers.bin')
    ) == ('0', '1')
    level, side = make_level_side(file_path='Maps/Author/Pack/Map-B.bin')
    assert save.first_clear_room_death(level, side, 'start') == 4
    assert save.first_clear_room_time(level, side, 'start') == Duration.from_milliseconds(2550)


def test_enders_blender_reader_rejects_invalid_room_orders(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0-modsave-EndersBlender.celeste').write_text(
        'mapDict_roomStat_firstClear_roomOrder: invalid\n', encoding='utf-8'
    )

    with pytest.raises(ValueError, match='room orders'):
        EndersBlenderReader(tmp_path / 'Celeste').load(0)


@pytest.mark.parametrize(
    ('field', 'value', 'label', 'reason'),
    (
        ('mapDict_roomStat_firstClear_death', '1.0', 'room deaths', 'decimal integer'),
        ('mapDict_roomStat_firstClear_timer', '1', 'room timers', 'whole milliseconds'),
    ),
)
def test_enders_blender_reader_preserves_invalid_value_locations(
    tmp_path: Path,
    field: str,
    value: str,
    label: str,
    reason: str,
) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0-modsave-EndersBlender.celeste').write_text(
        f"""{field}:
  Author/Pack/Map_B:
    start: {value}
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError) as info:
        EndersBlenderReader(tmp_path / 'Celeste').load(0)

    message = str(info.value)
    assert label in message
    assert 'Author/Pack/Map_B' in message
    assert 'start' in message
    assert reason in message
    assert isinstance(info.value.__cause__, ValidationError)
