import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pist.game.mods import InstalledMod, LocalMap
from pist.game.saves import MapStats, SaveSlot
from pist.game.time import Time
from pist.gamebanana import GameBananaSubmission
from pist.local_data import LocalDataStore
from pist.records import create_map_record, merge_saved_record


def test_create_map_record_uses_local_map_and_native_save_data(tmp_path: Path) -> None:
    mod = InstalledMod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='ExampleMetadata',
        metadata_version='1.0.0',
    )
    map_info = LocalMap(
        file_path='Maps/Author/Pack/Map-B.bin',
        dialog_key='Author_Pack_Map',
        side='B',
        names={'zh-cn': '示例地图 B', 'en': 'Example Map B'},
    )
    save_slot = SaveSlot(0, {('Author/Pack/Map', 1): MapStats(Time(83_456), 12)})

    record = create_map_record(
        mod,
        map_info,
        save_slot=save_slot,
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
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
        record_values={'主表': {'红草莓数': 3, '磁带': True, '水晶之心': '通关收集'}},
    )

    assert record.record_values == {'主表': {'红草莓数': 3, '磁带': True, '水晶之心': '通关收集'}}


def test_create_map_record_marks_normal_map_as_a_side() -> None:
    record = create_map_record(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
    )

    assert record.side == 'A'


def test_create_map_record_uses_gamebanana_submission_metadata() -> None:
    record = create_map_record(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
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
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    record = create_map_record(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(), 12)}),
    )

    assert record.save_slot == 0
    assert record.time_played is None
    assert record.deaths is None


def test_local_data_store_round_trips_a_record(tmp_path: Path) -> None:
    record = create_map_record(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    record_id = store.save_record(record)

    assert record_id == 1
    assert store.load_record(record_id) == record


def test_local_data_store_renames_the_previous_records_table(tmp_path: Path) -> None:
    record = create_map_record(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
    )
    path = tmp_path / '.pist/local-data.sqlite3'
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE drafts (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                mod_metadata_name TEXT,
                map_file TEXT,
                save_slot INTEGER,
                payload TEXT NOT NULL
            );
            CREATE INDEX drafts_by_map_save
            ON drafts (mod_metadata_name, map_file, save_slot, id DESC);
            """
        )
        conn.execute(
            """
            INSERT INTO drafts (created_at, mod_metadata_name, map_file, save_slot, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.created_at.isoformat(),
                record.mod_metadata_name,
                record.map_file,
                record.save_slot,
                record.model_dump_json(),
            ),
        )

    store = LocalDataStore(path)

    assert store.load_record(1) == record
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'records'"
        ).fetchone()
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'drafts'"
            ).fetchone()
            is None
        )


def test_local_data_store_updates_the_same_map_and_save_slot_in_place(tmp_path: Path) -> None:
    mod = InstalledMod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='ExampleMetadata',
        metadata_version='1.0.0',
    )
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    store = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    initial = create_map_record(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 2)}),
    )
    current = create_map_record(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(2_000), 5)}),
    )

    record_id = store.save_record(initial)
    assert store.existing_record_id(current) == record_id
    assert store.save_record(current) == record_id

    saved = store.load_record(record_id)
    assert saved.time_played == '0:00:02'
    assert saved.deaths == 5


def test_merge_saved_record_retains_manual_values_but_refreshes_save_data() -> None:
    mod = InstalledMod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='ExampleMetadata',
        metadata_version='1.0.0',
    )
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    saved = create_map_record(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 2)}),
        authors=('Alice',),
        record_values={'主表': {'红草莓数': 2, '起始日期': '2026-09-01', '备注': '好图'}},
    )
    current = create_map_record(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(2_000), 5)}),
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
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {}),
    )

    with pytest.raises(ValueError, match='without native play time'):
        LocalDataStore(tmp_path / '.pist/local-data.sqlite3').save_record(record)
