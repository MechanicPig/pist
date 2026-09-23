from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from pist.entities.map_entity_id import MapEntityID
from pist.game.content import ContentPath
from pist.game.duration import Duration
from pist.game.saves import MapProgress, MapStats, SaveReader, sid_for_level, sid_for_map_file
from pist.types import NonNegativeDecimalInt
from tests.map_factory import make_level_side


def test_duration_converts_filetime_and_formats_seconds() -> None:
    duration = Duration(3_723_456_0000)

    assert duration.total_milliseconds == 3_723_456
    assert duration.ingame_format() == '1:02:03.456'
    assert duration.ingame_format('seconds') == '1:02:03'
    assert duration.filetime == 3_723_456_0000

    assert Duration.from_milliseconds(83_456).ingame_format('seconds') == '0:01:23'


def test_duration_rejects_submillisecond_filetime() -> None:
    with pytest.raises(ValueError, match='whole milliseconds'):
        Duration(1)


def test_duration_converts_whole_millisecond_timedelta() -> None:
    assert Duration.from_timedelta(timedelta(milliseconds=2_550)) == Duration(25_500_000)


@pytest.mark.parametrize(
    'value',
    (timedelta(microseconds=-1), timedelta(microseconds=1)),
)
def test_duration_rejects_negative_or_submillisecond_timedelta(value: timedelta) -> None:
    with pytest.raises(ValueError, match='milliseconds|non-negative'):
        Duration.from_timedelta(value)


def test_duration_uses_filetime_for_pydantic_boundaries() -> None:
    adapter = TypeAdapter(Duration)

    duration = adapter.validate_python('25500000')

    assert duration == Duration(25_500_000)
    assert adapter.dump_python(duration) == 25_500_000


def test_non_negative_decimal_int_accepts_only_exact_decimal_input() -> None:
    adapter = TypeAdapter(NonNegativeDecimalInt)

    assert adapter.validate_python(1) == 1
    assert adapter.validate_python('01') == 1


@pytest.mark.parametrize('value', (True, 1.0, '1.0', '-1', '+1', ' 1 ', '1_000'))
def test_non_negative_decimal_int_rejects_other_input(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(NonNegativeDecimalInt).validate_python(value)


@pytest.mark.parametrize('value', (True, 1.0, '1.0', '-1', '+1', ' 1 ', '1_000'))
def test_duration_rejects_non_decimal_pydantic_filetime(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Duration).validate_python(value)


def test_sid_for_map_file_removes_b_and_c_side_suffixes() -> None:
    assert sid_for_map_file(ContentPath('Maps/Author/Pack/Map.bin')) == 'Author/Pack/Map'
    assert sid_for_map_file(ContentPath('Maps/Author/Pack/Map-B.bin')) == 'Author/Pack/Map'
    assert sid_for_map_file(ContentPath('Maps/Author/Pack/Map-C.bin')) == 'Author/Pack/Map'
    with pytest.raises(ValueError, match='Not a map file path'):
        sid_for_map_file(ContentPath('Content/Maps/0-Intro.bin'))
    with pytest.raises(ValueError, match='Not a map file path'):
        sid_for_map_file(ContentPath('maps/Author/Pack/Map.bin'))


def test_sid_for_level_prefers_an_explicit_original_game_sid() -> None:
    map_info = make_level_side(
        file_path='Maps/1H-ForsakenCity.bin',
        dialog_key='AREA_1',
        sid='Celeste/1-ForsakenCity',
        side='B',
    )

    assert sid_for_level(map_info[0]) == 'Celeste/1-ForsakenCity'


def test_save_reader_reads_original_game_level_stats(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Saves'
    saves_dir.mkdir()
    (saves_dir / '0.celeste').write_text(
        """<SaveData><Areas><AreaStats SID="Celeste/1-ForsakenCity" Cassette="true"><Modes><AreaModeStats TimePlayed="10000" Deaths="2" Completed="true" SingleRunCompleted="true" HeartGem="false" /><AreaModeStats TimePlayed="20000" Deaths="3" /></Modes></AreaStats></Areas></SaveData>""",
        encoding='utf-8',
    )
    map_info = make_level_side(
        file_path='Maps/1H-ForsakenCity.bin',
        dialog_key='AREA_1',
        sid='Celeste/1-ForsakenCity',
        side='B',
    )

    stats = SaveReader(tmp_path).load(0).get_map_stats(*map_info)

    assert stats == MapStats(Duration(20_000), 3, cassette_collected=True)


@pytest.mark.parametrize(
    ('value', 'expected'),
    (('room:1', MapEntityID('room', 1)), ('chapter:subroom:2', MapEntityID('chapter:subroom', 2))),
)
def test_map_entity_id_serializes_and_parses_native_save_keys(
    value: str, expected: MapEntityID
) -> None:
    assert MapEntityID.parse(value) == expected
    assert str(expected) == value


@pytest.mark.parametrize('value', ('room', ':1', 'room:one'))
def test_map_entity_id_rejects_invalid_native_save_keys(value: str) -> None:
    with pytest.raises(ValueError, match='Invalid map entity ID'):
        MapEntityID.parse(value)


@pytest.mark.parametrize(
    ('stats', 'expected'),
    (
        (MapStats(Duration(), 0), MapProgress.UNRECORDED),
        (MapStats(Duration.from_milliseconds(1_000), 0), MapProgress.ENTERED),
        (MapStats(Duration.from_milliseconds(1_000), 0, completed=True), MapProgress.COMPLETED),
        (
            MapStats(Duration.from_milliseconds(1_000), 0, single_run_completed=True),
            MapProgress.SINGLE_RUN_COMPLETED,
        ),
    ),
)
def test_map_stats_exposes_progress_rank(stats: MapStats, expected: MapProgress) -> None:
    assert stats.progress is expected


def test_save_reader_uses_native_stats_and_falls_back_to_mod_save_data(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text(
        """<SaveData><CurrentSession InArea="true"><Area SID="Author/Pack/Map" Mode="Normal" />
        </CurrentSession><LevelSets><LevelSetStats><Areas>
        <AreaStats SID="Author/Pack/Map" Cassette="true"><Modes>
        <AreaModeStats TimePlayed="10000000" Deaths="1" Completed="true" SingleRunCompleted="true" HeartGem="true">
        <Strawberries><EntityID Key="first:3" /><EntityID Key="side-room:9" /></Strawberries>
        </AreaModeStats>
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

    map_stats = save.get_map_stats(
        *make_level_side(file_path='Maps/Author/Pack/Map.bin', dialog_key='Map')
    )
    assert map_stats == MapStats(
        time_played=Duration.from_milliseconds(1_000),
        deaths=1,
        completed=True,
        single_run_completed=True,
        cassette_collected=True,
        heart_collected=True,
        collected_strawberries=frozenset({MapEntityID('first', 3), MapEntityID('side-room', 9)}),
    )
    assert save.get_map_stats(
        *make_level_side(file_path='Maps/Author/Pack/Map-B.bin', dialog_key='Map', side='B')
    ) == MapStats(time_played=Duration.from_milliseconds(2_000), deaths=2, cassette_collected=True)
    assert save.get_map_stats(
        *make_level_side(file_path='Maps/Author/Pack/Map-C.bin', dialog_key='Map', side='C')
    ) == MapStats(time_played=Duration.from_milliseconds(3_000), deaths=3, cassette_collected=True)
    assert save.get_map_stats(
        *make_level_side(file_path='Maps/Author/Pack/Other.bin', dialog_key='Other')
    ) == MapStats(time_played=Duration.from_milliseconds(4_000), deaths=4)
    reader = SaveReader(tmp_path / 'Celeste')
    assert (
        reader.map_progress(ContentPath('Maps/Author/Pack/Map.bin'))
        is MapProgress.SINGLE_RUN_COMPLETED
    )
    assert reader.map_progress(ContentPath('Maps/Author/Pack/Map-B.bin')) is MapProgress.ENTERED
    assert reader.map_progress(ContentPath('Maps/Author/Pack/Other.bin')) is MapProgress.ENTERED
    assert (
        reader.map_progress(ContentPath('Maps/Author/Pack/Missing.bin')) is MapProgress.UNRECORDED
    )
    assert map_stats is not None
    assert map_stats.collected_strawberries == frozenset(
        {MapEntityID('first', 3), MapEntityID('side-room', 9)}
    )
    assert save.is_in_progress(
        *make_level_side(file_path='Maps/Author/Pack/Map.bin', dialog_key='Map')
    )


def test_save_reader_reads_collab_return_to_lobby_sessions(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text('<SaveData />', encoding='utf-8')
    (saves_dir / '0-modsave-CollabUtils2.celeste').write_text(
        'SessionsPerLevel:\n'
        '  Collab/1-Maps/Unfinished: <Session><Area Mode="Normal" /></Session>\n',
        encoding='utf-8',
    )

    save = SaveReader(tmp_path / 'Celeste').load(0)

    assert save.is_in_progress(
        *make_level_side(file_path='Maps/Collab/1-Maps/Unfinished.bin', dialog_key='Unfinished')
    )
    assert not save.is_in_progress(
        *make_level_side(file_path='Maps/Collab/1-Maps/Finished.bin', dialog_key='Finished')
    )


def test_save_reader_preserves_numeric_like_collab_session_sid(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text('<SaveData />', encoding='utf-8')
    (saves_dir / '0-modsave-CollabUtils2.celeste').write_text(
        'SessionsPerLevel:\n  01: <Session><Area Mode="Normal" /></Session>\n', encoding='utf-8'
    )

    save = SaveReader(tmp_path / 'Celeste').load(0)

    assert save.is_in_progress(*make_level_side(file_path='Maps/01.bin', dialog_key='Numeric_SID'))


def test_save_reader_reports_invalid_collab_utils_data(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text('<SaveData />', encoding='utf-8')
    (saves_dir / '0-modsave-CollabUtils2.celeste').write_text(
        'SessionsPerLevel: null\n', encoding='utf-8'
    )

    with pytest.raises(ValueError, match='Invalid CollabUtils2 save file'):
        SaveReader(tmp_path / 'Celeste').load(0)


def test_save_reader_prefers_everest_current_session(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text(
        '<SaveData><CurrentSession InArea="true"><Area SID="Vanilla/Map" Mode="Normal" />'
        '</CurrentSession></SaveData>',
        encoding='utf-8',
    )
    (saves_dir / '0-modsavedata.celeste').write_text(
        '<ModSaveData><CurrentSession_Safe InArea="true"><Area SID="Mod/Map" Mode="BSide" />'
        '</CurrentSession_Safe></ModSaveData>',
        encoding='utf-8',
    )

    save = SaveReader(tmp_path / 'Celeste').load(0)

    assert not save.is_in_progress(
        *make_level_side(file_path='Maps/Vanilla/Map.bin', dialog_key='Vanilla_Map')
    )
    assert save.is_in_progress(
        *make_level_side(file_path='Maps/Mod/Map-B.bin', dialog_key='Mod_Map', side='B')
    )


@pytest.mark.parametrize('value', ('room', ':1', 'room:one'))
def test_save_reader_rejects_invalid_native_entity_keys(tmp_path: Path, value: str) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0.celeste').write_text(
        f'''<SaveData><LevelSets><LevelSetStats><Areas><AreaStats SID="Map"><Modes>
        <AreaModeStats TimePlayed="0" Deaths="0"><Strawberries><EntityID Key="{value}" />
        </Strawberries></AreaModeStats></Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>''',
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid collected entity ID'):
        SaveReader(tmp_path / 'Celeste').load(0)


def test_save_reader_lists_standard_and_mod_save_data_slots(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for filename in ('0.celeste', '2-modsavedata.celeste', 'not-a-save.celeste'):
        (saves_dir / filename).write_text('<SaveData />', encoding='utf-8')

    assert SaveReader(tmp_path / 'Celeste').available_numbers() == [0, 2]
