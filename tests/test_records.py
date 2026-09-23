from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from pist.entities.classification import MapEntityStats
from pist.entities.map_entity_id import MapEntityID
from pist.game.collab import JournalReferences
from pist.game.duration import Duration
from pist.game.levels import Level, LevelSide
from pist.game.saves import MapStats, SaveSlot
from pist.gamebanana import GameBananaSubmission
from pist.local_data import LocalDataStore
from pist.records import (
    MapRecord,
    MapRecordProgress,
    create_map_record,
    map_record_progress,
    merge_saved_record,
)
from tests.map_factory import make_level_side
from tests.mod_factory import make_installed_mod


def _example_map(
    file_path: str,
    *,
    dialog_key: str | None = None,
    side: Literal['B', 'C'] | None = None,
) -> tuple[Level, LevelSide]:
    return make_level_side(
        file_path=file_path,
        dialog_key=dialog_key,
        side=side,
        mod=make_installed_mod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
    )


@pytest.mark.parametrize(
    ('save_slot', 'deaths'),
    ((-1, None), (0, -1)),
)
def test_map_record_rejects_negative_save_stats(save_slot: int, deaths: int | None) -> None:
    with pytest.raises(ValueError):
        MapRecord(
            created_at=datetime(2026, 9, 19, tzinfo=UTC),
            mod_metadata_name='Example Mod',
            map_name='Example Map',
            map_file='Maps/Example/Map.bin',
            sid='Example/Map',
            side='A',
            save_slot=save_slot,
            deaths=deaths,
        )


def test_map_record_progress_distinguishes_completion_and_saved_sessions() -> None:
    stats = MapEntityStats(
        counts={'strawberry': 1},
        existing_kinds=frozenset({'cassette'}),
        selected_kinds=frozenset({'heart'}),
    )

    assert (
        map_record_progress(
            MapStats(
                Duration.from_milliseconds(1),
                1,
                single_run_completed=True,
                cassette_collected=True,
                heart_collected=True,
                collected_strawberries=frozenset(),
            ),
            stats,
            is_in_progress=False,
        )
        is MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES
    )
    assert (
        map_record_progress(
            MapStats(
                Duration.from_milliseconds(1),
                1,
                single_run_completed=True,
                cassette_collected=True,
                heart_collected=True,
                collected_strawberries=frozenset({MapEntityID('room', 1)}),
            ),
            stats,
            is_in_progress=False,
        )
        is MapRecordProgress.COMPLETED_ALL_COLLECTIBLES
    )
    assert (
        map_record_progress(
            MapStats(Duration.from_milliseconds(1), 1, completed=True), stats, is_in_progress=False
        )
        is MapRecordProgress.PARTIALLY_COMPLETED
    )
    assert (
        map_record_progress(MapStats(Duration.from_milliseconds(1), 1), stats, is_in_progress=True)
        is MapRecordProgress.IN_PROGRESS
    )
    assert (
        map_record_progress(MapStats(Duration.from_milliseconds(1), 1), stats, is_in_progress=False)
        is MapRecordProgress.RAN_BEFORE
    )
    assert map_record_progress(None, stats, is_in_progress=False) is MapRecordProgress.NOT_STARTED


def test_map_record_progress_requires_each_existing_collectible() -> None:
    completed = MapStats(Duration.from_milliseconds(1), 1, single_run_completed=True)

    cassette = MapEntityStats(existing_kinds=frozenset({'cassette'}))
    assert (
        map_record_progress(completed, cassette, is_in_progress=False)
        is MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES
    )
    assert (
        map_record_progress(
            MapStats(
                Duration.from_milliseconds(1), 1, single_run_completed=True, cassette_collected=True
            ),
            cassette,
            is_in_progress=False,
        )
        is MapRecordProgress.COMPLETED_ALL_COLLECTIBLES
    )

    heart = MapEntityStats(existing_kinds=frozenset({'heart'}))
    assert (
        map_record_progress(completed, heart, is_in_progress=False)
        is MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES
    )
    assert (
        map_record_progress(
            MapStats(
                Duration.from_milliseconds(1), 1, single_run_completed=True, heart_collected=True
            ),
            heart,
            is_in_progress=False,
        )
        is MapRecordProgress.COMPLETED_ALL_COLLECTIBLES
    )

    assert (
        map_record_progress(completed, MapEntityStats(), is_in_progress=False)
        is MapRecordProgress.COMPLETED_ALL_COLLECTIBLES
    )


def test_create_map_record_uses_local_map_and_native_save_data(tmp_path: Path) -> None:
    map_info = _example_map(
        file_path='Maps/Author/Pack/Map-B.bin',
        dialog_key='Author_Pack_Map',
        side='B',
    )
    save_slot = SaveSlot(
        0, {('Author/Pack/Map', 1): MapStats(Duration.from_milliseconds(83_456), 12)}
    )

    record = create_map_record(
        *map_info,
        save_slot=save_slot,
        dialogs={
            'zh-cn': {'Author_Pack_Map': '示例地图'},
            'en': {'Author_Pack_Map': 'Example Map'},
        },
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert record.mod_metadata_name == 'ExampleMetadata'
    assert record.map_name == '示例地图 B'
    assert record.map_english_name == 'Example Map B'
    assert record.sid == 'Author/Pack/Map'
    assert record.side == 'B'
    assert record.save_slot == 0
    assert record.time_played == '0:01:23'
    assert record.deaths == 12
    assert not record.completed
    assert record.mod_name is None
    assert record.mod_url is None
    assert record.record_values == {}


def test_create_map_record_preserves_configured_record_values() -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}),
        record_values={'主表': {'红草莓数': 3, '磁带': True, '水晶之心': '通关收集'}},
    )

    assert record.record_values == {'主表': {'红草莓数': 3, '磁带': True, '水晶之心': '通关收集'}}


def test_create_map_record_marks_normal_map_as_a_side() -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}),
    )

    assert record.side == 'A'


def test_create_map_record_rejects_vanilla_map() -> None:
    with pytest.raises(TypeError, match='vanilla map'):
        create_map_record(
            *make_level_side(file_path='Maps/0-Intro.bin', dialog_key='AREA_0'),
            save_slot=SaveSlot(0, {}),
        )


def test_create_map_record_uses_gamebanana_submission_metadata() -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}),
        gamebanana=GameBananaSubmission.model_validate(
            {
                'name': 'Example Mod',
                'submitter': 'Submitter',
                'pageUrl': 'https://gamebanana.com/mods/123',
                'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
                'credits': [
                    {'groupName': 'Creator', 'authors': [{'name': 'Alice'}, {'name': 'Bob'}]}
                ],
            }
        ),
        authors=('Alice', 'Bob'),
    )

    assert record.mod_name == 'Example Mod'
    assert record.mod_url == 'https://gamebanana.com/mods/123'
    assert record.authors == ('Alice', 'Bob')
    assert record.credits[0].group_name == 'Creator'
    assert tuple(author.name for author in record.credits[0].authors) == ('Alice', 'Bob')
    assert record.mod_updated_at is not None
    assert record.mod_updated_at.isoformat() == '2026-09-04T12:00:00+00:00'


def test_create_map_record_omits_zero_time_stats() -> None:
    map_info = _example_map('Maps/Example/Map.bin', dialog_key='Example_Map')
    record = create_map_record(
        *map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(), 12)}),
    )

    assert record.save_slot == 0
    assert record.time_played is None
    assert record.deaths is None


def test_local_data_store_round_trips_a_record(tmp_path: Path) -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}),
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    record_id = store.save_record(record)

    assert record_id == 1
    assert store.load_record(record_id) == record


def test_local_data_store_caches_collab_journal_icons(tmp_path: Path) -> None:
    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    map_files = ('Maps/Collab/1-Easy.bin', 'Maps/Collab/2-Medium.bin')
    icons = {
        map_files[0]: 'areas/Collab/meters/1-easy',
        map_files[1]: None,
    }

    store.save_collab_journal_icons('C:/Celeste/Mods/Collab.zip', map_files, 'first', icons)

    assert (
        store.load_collab_journal_icons('C:/Celeste/Mods/Collab.zip', map_files, 'first') == icons
    )
    assert (
        store.load_collab_journal_icons('C:/Celeste/Mods/Collab.zip', map_files, 'changed') is None
    )


def test_local_data_store_discards_invalid_cached_collab_journal_icons(tmp_path: Path) -> None:
    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO collab_journal_icons (mod_path, map_files, fingerprint, icons)
            VALUES (?, ?, ?, ?)
            """,
            ('C:/Celeste/Mods/Collab.zip', '[]', 'first', '{"Maps/Collab.bin": 1}'),
        )

    assert store.load_collab_journal_icons('C:/Celeste/Mods/Collab.zip', (), 'first') is None


def test_local_data_store_caches_collab_journal_refs(tmp_path: Path) -> None:
    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    references = JournalReferences(('Collab/1-Easy', 'Addon/Maps'), 2)

    store.save_collab_journal_refs(
        'C:/Celeste/Mods/Collab.zip',
        'Maps/Collab/0-Lobbies/1-Easy.bin',
        'first',
        references,
    )

    assert (
        store.load_collab_journal_refs(
            'C:/Celeste/Mods/Collab.zip',
            'Maps/Collab/0-Lobbies/1-Easy.bin',
            'first',
        )
        == references
    )
    assert (
        store.load_collab_journal_refs(
            'C:/Celeste/Mods/Collab.zip',
            'Maps/Collab/0-Lobbies/1-Easy.bin',
            'changed',
        )
        is None
    )


def test_local_data_store_updates_the_same_map_and_save_slot_in_place(tmp_path: Path) -> None:
    map_info = _example_map('Maps/Example/Map.bin', dialog_key='Example_Map')
    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    initial = create_map_record(
        *map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 2)}),
    )
    current = create_map_record(
        *map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(2_000), 5)}),
    )

    record_id = store.save_record(initial)
    assert store.existing_record_id(current) == record_id
    assert store.save_record(current) == record_id

    saved = store.load_record(record_id)
    assert saved.time_played == '0:00:02'
    assert saved.deaths == 5


def test_merge_saved_record_retains_manual_values_but_refreshes_save_data() -> None:
    map_info = _example_map('Maps/Example/Map.bin', dialog_key='Example_Map')
    saved = create_map_record(
        *map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 2)}),
        authors=('Alice',),
        record_values={'主表': {'红草莓数': 2, '起始日期': '2026-09-01', '备注': '好图'}},
    )
    current = create_map_record(
        *map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(2_000), 5)}),
        record_values={'主表': {'红草莓数': 4, '主房间数': 8}},
    )

    merged = merge_saved_record(current, saved)

    assert merged.authors == ('Alice',)
    assert merged.time_played == '0:00:02'
    assert merged.deaths == 5
    assert merged.record_values == {
        '主表': {'红草莓数': 4, '主房间数': 8, '起始日期': '2026-09-01', '备注': '好图'}
    }


def test_local_data_store_rejects_record_without_native_play_time(tmp_path: Path) -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {}),
    )

    with pytest.raises(ValueError, match='without native play time'):
        LocalDataStore(tmp_path / '.pist/local-data.sqlite3').save_record(record)
