"""Ender's Blender file queries use explicit raw protocol keys."""

from pathlib import Path

import pytest
import yaml

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.rooms import RoomStats, room_key
from berries.game.saves.unknown import UnknownSaveFieldWarning
from test_support.save_docs import read_save, save_bytes


def test_first_clear_stats_keep_explicit_final_and_local_keys_distinct() -> None:
    save = read_save(
        EndersBlenderSave,
        'mapDict_roomStat_firstClear_roomOrder:\n'
        '  Final/Map_B: [start, end]\n  Local/Map_B: [raw]\n'
        'mapDict_roomStat_firstClear_death:\n  Final/Map_B: {start: 0, end: 2}\n'
        'mapDict_roomStat_firstClear_timer:\n  Final/Map_B: {start: 25000000}\n',
    )
    stats = save.first_clear.get(room_key('Final/Map', LevelSide.B))
    assert stats is not None
    assert stats.room_order == ['start', 'end']
    assert stats.room_deaths == {'start': 0, 'end': 2}
    assert stats.room_times == {'start': Duration.from_dotnet_ticks(25000000)}
    raw = save.first_clear.get('Local/Map_B')
    assert raw is not None and raw.room_order == ['raw']
    assert raw.room_deaths == {} and raw.room_times == {}
    assert save.first_clear.get(room_key('Final/Map', LevelSide.A)) is None


def test_blender_file_loads_first_clear_records_and_preserves_numeric_room_names(
    tmp_path: Path,
) -> None:
    path = tmp_path / '2-modsave-EndersBlender.celeste'
    path.write_text(
        'mapDict_roomStat_firstClear_roomOrder:\n'
        '  Author/Pack/Map_B: [start, middle, goal]\n'
        '  Author/Pack/Numbers: [0, 1]\n'
        'mapDict_roomStat_firstClear_death:\n  Author/Pack/Map_B: {start: 4}\n'
        'mapDict_roomStat_firstClear_timer:\n  Author/Pack/Map_B: {start: 25500000}\n',
        encoding='utf-8',
    )
    save = EndersBlenderSave.read(path)
    stats = save.first_clear.get(room_key('Author/Pack/Map', LevelSide.B))
    assert stats is not None and stats.room_order == ['start', 'middle', 'goal']
    assert stats.room_deaths['start'] == 4
    assert stats.room_times['start'] == Duration.from_dotnet_ticks(25500000)
    assert save.first_clear.get(room_key('Author/Pack/Map', LevelSide.A)) is None
    numbers = save.first_clear.get('Author/Pack/Numbers')
    assert numbers is not None and numbers.room_order == ['0', '1']


def test_final_sid_suffix_does_not_select_the_side() -> None:
    save = read_save(
        EndersBlenderSave,
        'mapDict_roomStat_firstClear_roomOrder:\n  Pack/Map-B: [a-room]\n  Pack/Map-B_B: [b-room]\n',
    )
    a = save.first_clear.get(room_key('Pack/Map-B', LevelSide.A))
    b = save.first_clear.get(room_key('Pack/Map-B', LevelSide.B))
    assert a is not None and a.room_order == ['a-room']
    assert b is not None and b.room_order == ['b-room']


def test_blender_file_rejects_invalid_record_mapping_on_load() -> None:
    with pytest.raises(ValueError):
        read_save(EndersBlenderSave, 'mapDict_roomStat_firstClear_roomOrder: invalid\n')


@pytest.mark.parametrize(
    ('field', 'value'),
    (('mapDict_roomStat_firstClear_death', '1.0'), ('mapDict_roomStat_firstClear_timer', '-1.0')),
)
def test_blender_file_preserves_invalid_value_locations(field: str, value: str) -> None:
    with pytest.raises(ValueError) as caught:
        read_save(EndersBlenderSave, f'{field}:\n  Author/Pack/Map_B:\n    start: {value}\n')
    message = str(caught.value)
    assert field in message and 'Author/Pack/Map_B' in message and 'start' in message
    assert 'decimal integer' in message


def test_incomplete_room_statistics_can_be_loaded_and_repaired_before_output() -> None:
    save = EndersBlenderSave.fromstring('mapDict_roomStat_firstClear_roomOrder: {Map: [start]}\n')
    stats = save.first_clear['Map']
    assert stats.room_order == ['start']
    assert not stats.room_deaths and not stats.room_times
    with pytest.raises(ValueError, match='Missing Blender room deaths'):
        save.tostring()
    stats.room_deaths['start'] = 3
    stats.room_times['start'] = Duration.from_seconds(2)
    restored = EndersBlenderSave.fromstring(save.tostring(), strict=False)
    assert restored.first_clear['Map'] == stats


def test_all_blender_fields_round_trip_independently() -> None:
    save = EndersBlenderSave()
    first = RoomStats(
        room_order=['01', 'true'],
        room_deaths={'01': 0, 'true': 1, 'death_only': 0},
        room_times={
            '01': Duration(),
            'true': Duration(),
            'time_only': Duration.from_dotnet_ticks(-1),
        },
        room_rta_times={'rta_only': Duration.from_dotnet_ticks(13)},
        room_strawberries={'berry_only': 2},
        pause_flags={'Level_Invalid': True, '01': False},
    )
    last = RoomStats(room_order=['last'], room_deaths={'last': 3}, room_times={'last': Duration()})
    save.first_clear[room_key('Pack/Map', LevelSide.B)] = first
    save.last_saved['Pack/Map_B'] = last
    save.room_names['Second_B'] = {'01': 'first room'}
    save.room_names['First'] = {'true': 'second room'}
    save.room_names['Pack/Map_B'] = {}
    save.room_colors['Pack/Map_B'] = {'01': -1}
    encoded = save_bytes(save)
    restored = read_save(EndersBlenderSave, encoded)
    assert restored.first_clear[room_key('Pack/Map', LevelSide.B)] == first
    assert restored.last_saved['Pack/Map_B'] == last
    assert list(restored.room_names) == ['Second_B', 'First', 'Pack/Map_B']
    assert restored.room_names == save.room_names
    assert restored.room_colors == save.room_colors
    restored.first_clear['Pack/Map_B'].reset()
    assert restored.last_saved['Pack/Map_B'] == last
    assert restored.room_names == save.room_names


def test_room_edits_do_not_follow_unknown_yaml_aliases() -> None:
    with pytest.warns(UnknownSaveFieldWarning, match='EndersBlenderSave.Unknown'):
        save = EndersBlenderSave.fromstring(
            'mapDict_roomStat_firstClear_death: &deaths\n  Map: {room: 2}\n'
            'mapDict_roomStat_latestSession_death: *deaths\n'
            'Unknown: *deaths\n',
            unknown='warn',
        )
    stats = save.first_clear['Map']
    assert save.first_clear['Map'] is stats
    stats.room_deaths['room'] = 7
    result = yaml.safe_load(save_bytes(save))
    assert result['mapDict_roomStat_firstClear_death']['Map']['room'] == 7
    assert result['mapDict_roomStat_latestSession_death']['Map']['room'] == 2
    assert 'Unknown' not in result


def test_room_access_keeps_record_order_and_serialization_is_repeatable() -> None:
    data = (
        'mapDict_roomStat_firstClear_roomOrder: {One: [start], Two: [goal]}\n'
        'mapDict_roomStat_firstClear_death: {One: {start: 0}, Two: {goal: 0}}\n'
        'mapDict_roomStat_firstClear_timer: {One: {start: 0}, Two: {goal: 0}}\n'
        'mapDict_roomStat_latestSession_rtatimer: {OnlyRta: {room: 17}}\n'
    )
    save = read_save(EndersBlenderSave, data)
    original = save_bytes(save)
    assert save.first_clear['One'].room_order == ['start']
    assert list(save.first_clear.keys()) == ['One', 'Two']
    assert list(save.last_saved.keys()) == ['OnlyRta']
    assert save.last_saved['OnlyRta'].room_rta_times == {'room': Duration.from_dotnet_ticks(17)}
    assert save_bytes(save) == original
    assert save_bytes(save) == original
    del save.first_clear['Two']
    assert list(read_save(EndersBlenderSave, save_bytes(save)).first_clear.keys()) == ['One']
