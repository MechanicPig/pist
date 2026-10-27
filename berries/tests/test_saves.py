"""Shared protocol values and independent save-file queries."""

from pathlib import Path

import pytest

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.files.main import MainSave
from berries.game.saves.paths import (
    mod_save_data_path,
    mod_save_path,
    mod_session_path,
    parse_mod_session_name,
    save_slot_path,
)
from berries.map_entity_id import MapEntityID
from test_support.save_docs import normal_save_xml, read_save, require_map


@pytest.mark.parametrize('sid', ('Celeste/1-ForsakenCity', 'Pack/Map-B'))
def test_file_queries_final_identity_without_reinterpreting_filename(sid: str) -> None:
    save = read_save(
        MainSave,
        f'<SaveData><Areas><AreaStats SID="{sid}"><Modes>'
        '<AreaModeStats TimePlayed="10000" Deaths="1" Completed="true" />'
        '</Modes></AreaStats></Areas></SaveData>',
    )
    progress = require_map(save.vanilla.levels[sid].maps[LevelSide.A])
    assert progress.time_played == Duration.from_dotnet_ticks(10000)
    assert progress.deaths == 1 and progress.completed


def test_file_queries_keep_sid_and_side_independent() -> None:
    save = read_save(
        MainSave,
        '<SaveData><Areas><AreaStats SID="Pack/Map-B"><Modes>'
        '<AreaModeStats TimePlayed="10000" Deaths="1"/>'
        '<AreaModeStats TimePlayed="20000" Deaths="2"/>'
        '</Modes></AreaStats></Areas><CurrentSession InArea="true" Level="room" '
        'Time="0" Deaths="0" DeathsInCurrentLevel="0">'
        '<Area SID="Pack/Map-B" Mode="BSide"/></CurrentSession></SaveData>',
    )
    level = save.vanilla.levels['Pack/Map-B']
    assert require_map(level.maps[LevelSide.A]).time_played == Duration.from_dotnet_ticks(10000)
    assert require_map(level.maps[LevelSide.A]).deaths == 1
    assert require_map(level.maps[LevelSide.B]).time_played == Duration.from_dotnet_ticks(20000)
    assert require_map(level.maps[LevelSide.B]).deaths == 2
    assert save.vanilla.levels.get('Pack/Map') is None
    assert save.current_map == ('Pack/Map-B', LevelSide.B)


@pytest.mark.parametrize(
    'value', (True, False, None, 1.0, '1.0', '--1', '-1.0', '+1', ' 1 ', '1_000')
)
def test_duration_rejects_non_decimal_save_ticks(value: object) -> None:
    with pytest.raises(ValueError):
        values_codec.parse_duration(value)


def test_save_paths_follow_everest_filename_conventions(tmp_path: Path) -> None:
    assert save_slot_path(tmp_path, 2) == tmp_path / '2.celeste'
    assert mod_save_data_path(tmp_path, 2) == tmp_path / '2-modsavedata.celeste'
    assert mod_save_path(tmp_path, 2, 'ExampleMod') == tmp_path / '2-modsave-ExampleMod.celeste'
    assert save_slot_path(tmp_path, -1) == tmp_path / 'debug.celeste'


@pytest.mark.parametrize('number', (0, 6, -1))
def test_mod_session_filename_roundtrip_and_other_files(number: int, tmp_path: Path) -> None:
    path = mod_session_path(tmp_path, number, 'Mod.with-hyphen')
    assert parse_mod_session_name(path.name, number) == 'Mod.with-hyphen'
    assert parse_mod_session_name(path.name + '.bak', number) is None
    assert parse_mod_session_name(path.name, number + 1) is None
    assert parse_mod_session_name(mod_save_path(tmp_path, number, 'Mod').name, number) is None
    assert parse_mod_session_name('0-modsession-.celeste', number) is None


@pytest.mark.parametrize('name', ('', '.', '..', '../other', 'folder/file', '/absolute', 'a\0b'))
def test_mod_path_builders_reject_invalid_names(name: str, tmp_path: Path) -> None:
    for build in (mod_save_path, mod_session_path):
        with pytest.raises(ValueError, match='single Mod name'):
            build(tmp_path, 0, name)


def test_main_file_reads_original_game_level_stats(tmp_path: Path) -> None:
    path = tmp_path / '0.celeste'
    path.write_text(
        normal_save_xml(
            '<SaveData><Areas><AreaStats SID="Celeste/1-ForsakenCity" Cassette="true"><Modes>'
            '<AreaModeStats TimePlayed="170000" Deaths="2" Completed="true"/>'
            '<AreaModeStats TimePlayed="340000" Deaths="3"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '</Modes></AreaStats></Areas></SaveData>'
        ),
        encoding='utf-8',
    )
    save = MainSave.read(path)
    level = save.flat_levels['Celeste/1-ForsakenCity']
    assert level.cassette
    assert require_map(level.maps[LevelSide.B]).time_played == Duration.from_dotnet_ticks(340000)
    assert require_map(level.maps[LevelSide.B]).deaths == 3


@pytest.mark.parametrize(
    ('value', 'expected'),
    (('room:1', MapEntityID('room', 1)), ('chapter_subroom:2', MapEntityID('chapter_subroom', 2))),
)
def test_map_entity_id_serializes_and_parses_native_save_keys(
    value: str, expected: MapEntityID
) -> None:
    assert MapEntityID.parse(value) == expected
    assert str(expected) == value


@pytest.mark.parametrize('value', ('room', ':1', 'room:one', 'room:extra:1', 'room:2147483648'))
def test_map_entity_id_rejects_invalid_native_save_keys(value: str) -> None:
    with pytest.raises(ValueError, match='Invalid map entity ID'):
        MapEntityID.parse(value)


@pytest.mark.parametrize('value', ('room', ':1', 'room:one'))
def test_file_rejects_invalid_collected_entity_keys(value: str) -> None:
    with pytest.raises(ValueError, match='Invalid map entity ID'):
        read_save(
            MainSave,
            '<SaveData><LevelSets><LevelSetStats><Areas><AreaStats SID="Map"><Modes>'
            f'<AreaModeStats TimePlayed="0" Deaths="0"><Strawberries><EntityID Key="{value}"/>'
            '</Strawberries></AreaModeStats></Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>',
        )
