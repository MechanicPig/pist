"""Personal ranking is separate from native save reading."""

from pathlib import Path

import pytest

from berries.game.content import ContentPath
from berries.game.duration import Duration
from berries.game.levels import LevelSide
from pist.save_progress import MapProgress, SaveProgressIndex, is_recorded, map_progress
from pist.saves import MapStats, SaveProgress, SaveProgressReader
from test_support.save_docs import normal_save_xml


@pytest.mark.parametrize('sid', ('Celeste/1-ForsakenCity', 'Pack/Map-B'))
def test_progress_queries_final_identity_without_reinterpreting_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sid: str
) -> None:
    monkeypatch.setenv('EVEREST_SAVEPATH', str(tmp_path))
    saves = tmp_path / 'Saves'
    saves.mkdir()
    parent = sid.rpartition('/')[0]
    start = (
        '<Areas>' if parent == 'Celeste' else f'<LevelSets><LevelSetStats Name="{parent}"><Areas>'
    )
    end = '</Areas>' if parent == 'Celeste' else '</Areas></LevelSetStats></LevelSets>'
    (saves / '0.celeste').write_text(
        normal_save_xml(
            f'<SaveData>{start}<AreaStats SID="{sid}"><Modes>'
            '<AreaModeStats TimePlayed="170000" Deaths="1" Completed="true" />'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            f'</Modes></AreaStats>{end}</SaveData>'
        ),
        encoding='utf-8',
    )
    reader = SaveProgressReader.from_game_dir(tmp_path)
    assert map_progress(reader.load(0).get_map_stats(sid, LevelSide.A)) is MapProgress.COMPLETED
    assert SaveProgressIndex(reader).map_progress(sid, LevelSide.A) is MapProgress.COMPLETED


@pytest.mark.parametrize(
    ('stats', 'expected'),
    (
        (MapStats(Duration.from_dotnet_ticks(0), 0), MapProgress.UNRECORDED),
        (MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 0), MapProgress.ENTERED),
        (
            MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 0, completed=True),
            MapProgress.COMPLETED,
        ),
        (
            MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 0, single_run_completed=True),
            MapProgress.SINGLE_RUN_COMPLETED,
        ),
    ),
)
def test_map_stats_rank_for_personal_lists(stats: MapStats, expected: MapProgress) -> None:
    assert map_progress(stats) is expected


@pytest.mark.parametrize('ticks', (0, 1, -1, -170_001))
def test_recorded_means_positive_time_even_with_completion_flags(ticks: int) -> None:
    stats = MapStats(
        Duration.from_dotnet_ticks(ticks), 1, completed=True, single_run_completed=True
    )
    assert is_recorded(stats) == (ticks > 0)
    assert map_progress(stats) is (
        MapProgress.SINGLE_RUN_COMPLETED if ticks > 0 else MapProgress.UNRECORDED
    )
    assert map_progress(None) is MapProgress.UNRECORDED


def test_cross_slot_progress_cache_requires_explicit_refresh(tmp_path: Path) -> None:
    path = tmp_path / '0.celeste'
    path.write_text(normal_save_xml('<SaveData />'), encoding='utf-8')
    reader = SaveProgressReader(tmp_path)
    index = SaveProgressIndex(reader)
    assert index.map_progress('Pack/Map', LevelSide.A) is MapProgress.UNRECORDED
    path.write_text(
        normal_save_xml(
            '<SaveData><LevelSets><LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map"><Modes>'
            '<AreaModeStats TimePlayed="170000" Deaths="1" Completed="true" />'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '</Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>'
        ),
        encoding='utf-8',
    )
    assert reader.load(0).get_map_stats('Pack/Map', LevelSide.A) is not None
    assert index.map_progress('Pack/Map', LevelSide.A) is MapProgress.UNRECORDED
    index.refresh()
    assert index.map_progress('Pack/Map', LevelSide.A) is MapProgress.COMPLETED


def test_cross_slot_progress_uses_best_rank_and_keeps_side_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number, single_run in ((0, 'false'), (1, 'true')):
        (tmp_path / f'{number}.celeste').write_text(
            normal_save_xml(
                '<SaveData><LevelSets><LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map"><Modes>'
                f'<AreaModeStats TimePlayed="170000" Deaths="1" Completed="true" '
                f'SingleRunCompleted="{single_run}" />'
                '<AreaModeStats TimePlayed="170000" Deaths="2" />'
                '<AreaModeStats TimePlayed="0" Deaths="0"/>'
                '</Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>'
            ),
            encoding='utf-8',
        )
    reader = SaveProgressReader(tmp_path)
    loaded_numbers: list[int] = []
    load = reader.load

    def counted_load(number: int) -> SaveProgress:
        loaded_numbers.append(number)
        return load(number)

    monkeypatch.setattr(reader, 'load', counted_load)
    index = SaveProgressIndex(reader)
    assert loaded_numbers == []
    assert index.map_progress('Pack/Map', LevelSide.A) is MapProgress.SINGLE_RUN_COMPLETED
    assert index.map_progress('Pack/Map', LevelSide.B) is MapProgress.ENTERED
    assert index.map_progress('Pack/Map', LevelSide.C) is MapProgress.UNRECORDED
    assert index.map_progress('Missing', LevelSide.A) is MapProgress.UNRECORDED
    assert index.local_map_progress(ContentPath('Maps/Pack/Map-B.bin')) is MapProgress.ENTERED
    assert loaded_numbers == [0, 1]
