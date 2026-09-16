from pathlib import Path

import pytest

from pist.game.mods import LocalMap
from pist.game.saves import MapProgress, MapStats, SaveReader, sid_for_map_file
from pist.game.time import Time


def test_time_converts_celeste_filetime_and_formats_seconds() -> None:
    time = Time.from_filetime(3_723_456_0000)

    assert time.total_milliseconds == 3_723_456
    assert time.ingame_format() == '1:02:03.456'
    assert time.ingame_format('seconds') == '1:02:03'
    assert time.filetime == 3_723_456_0000

    assert Time(83_456).ingame_format('seconds') == '0:01:23'


def test_time_rejects_submillisecond_filetime() -> None:
    with pytest.raises(ValueError, match='whole number'):
        Time.from_filetime(1)


def test_sid_for_map_file_removes_b_and_c_side_suffixes() -> None:
    assert sid_for_map_file('Maps/Author/Pack/Map.bin') == 'Author/Pack/Map'
    assert sid_for_map_file('Maps/Author/Pack/Map-B.bin') == 'Author/Pack/Map'
    assert sid_for_map_file('Maps/Author/Pack/Map-C.bin') == 'Author/Pack/Map'


def test_save_reader_uses_native_stats_and_falls_back_to_mod_save_data(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text(
        """<SaveData><LevelSets><LevelSetStats><Areas>
        <AreaStats SID="Author/Pack/Map"><Modes>
        <AreaModeStats TimePlayed="10000000" Deaths="1" Completed="true" SingleRunCompleted="true" />
        <AreaModeStats TimePlayed="20000000" Deaths="2" />
        <AreaModeStats TimePlayed="30000000" Deaths="3" />
        </Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>""",
        encoding='utf-8',
    )
    (saves_dir / '0-modsavedata.celeste').write_text(
        """<ModSaveData><LevelSets><LevelSetStats><Areas>
        <AreaStats SID="Author/Pack/Map"><Modes>
        <AreaModeStats TimePlayed="990000000" Deaths="99" />
        </Modes></AreaStats>
        <AreaStats SID="Author/Pack/Other"><Modes>
        <AreaModeStats TimePlayed="40000000" Deaths="4" />
        </Modes></AreaStats></Areas></LevelSetStats></LevelSets></ModSaveData>""",
        encoding='utf-8',
    )

    save = SaveReader(tmp_path / 'Celeste').load(0)

    assert save.get_map_stats(
        LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Map')
    ) == MapStats(
        time_played=Time(1_000),
        deaths=1,
        completed=True,
        single_run_completed=True,
    )
    assert save.get_map_stats(
        LocalMap(file_path='Maps/Author/Pack/Map-B.bin', dialog_key='Map', side='B')
    ) == MapStats(time_played=Time(2_000), deaths=2)
    assert save.get_map_stats(
        LocalMap(file_path='Maps/Author/Pack/Map-C.bin', dialog_key='Map', side='C')
    ) == MapStats(time_played=Time(3_000), deaths=3)
    assert save.get_map_stats(
        LocalMap(file_path='Maps/Author/Pack/Other.bin', dialog_key='Other')
    ) == MapStats(time_played=Time(4_000), deaths=4)
    reader = SaveReader(tmp_path / 'Celeste')
    assert reader.map_progress('Maps/Author/Pack/Map.bin') is MapProgress.SINGLE_RUN_COMPLETED
    assert reader.map_progress('Maps/Author/Pack/Map-B.bin') is MapProgress.ENTERED
    assert reader.map_progress('Maps/Author/Pack/Other.bin') is MapProgress.ENTERED
    assert reader.map_progress('Maps/Author/Pack/Missing.bin') is MapProgress.UNRECORDED


def test_save_reader_lists_standard_and_mod_save_data_slots(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for filename in ('0.celeste', '2-modsavedata.celeste', 'not-a-save.celeste'):
        (saves_dir / filename).write_text('<SaveData />', encoding='utf-8')

    assert SaveReader(tmp_path / 'Celeste').available_numbers() == [0, 2]
