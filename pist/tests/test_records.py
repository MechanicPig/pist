from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from berries.game.duration import Duration
from berries.game.levels import Level, LevelSide
from berries.game.saves import MapStats, SaveSlot
from berries.gamebanana import GameBananaSubmission
from berries.map_entity_id import MapEntityID
from pist.entity_stats import MapEntityStats
from pist.records.models import (
    MapRecord,
    MapRecordProgress,
    create_map_record,
    map_record_progress,
    merge_saved_record,
)
from pist.records.store import RecordSheetLink, RecordStorageError, RecordStore
from test_support.map_factory import make_level_side
from test_support.mod_factory import make_installed_mod

EXAMPLE_MOD = make_installed_mod(
    source='zip',
    filename='Example.zip',
    path='C:/Celeste/Mods/Example.zip',
    metadata_name='ExampleMetadata',
    metadata_version='1.0.0',
)


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
        mod=EXAMPLE_MOD,
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
            side=LevelSide.A,
            save_slot=save_slot,
            deaths=deaths,
        )


def test_map_record_progress_distinguishes_completion_and_saved_sessions() -> None:
    stats = MapEntityStats(
        counts={'strawberry': 1},
        instance_ids={'strawberry': frozenset({MapEntityID('room', 1)})},
        existing_stats=frozenset({'cassette'}),
        selected_stats=frozenset({'heart'}),
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

    cassette = MapEntityStats(existing_stats=frozenset({'cassette'}))
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

    heart = MapEntityStats(existing_stats=frozenset({'heart'}))
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


@pytest.mark.parametrize(
    'collected',
    (
        frozenset({MapEntityID('room', 99)}),  # golden or historical ID
        frozenset({MapEntityID('room', 1), MapEntityID('room', 99)}),  # missing moonberry
        frozenset({MapEntityID('room', 2), MapEntityID('room', 99)}),  # missing red berry
        frozenset({MapEntityID('room', 1), MapEntityID('room', 2)}),
    ),
)
def test_completion_requires_current_red_and_moon_berry_ids(
    collected: frozenset[MapEntityID],
) -> None:
    stats = MapEntityStats(
        counts={'strawberry': 1, 'moonberry': 1},
        instance_ids={
            'strawberry': frozenset({MapEntityID('room', 1)}),
            'moonberry': frozenset({MapEntityID('room', 2)}),
        },
    )
    progress = map_record_progress(
        MapStats(
            Duration.from_milliseconds(1),
            1,
            single_run_completed=True,
            collected_strawberries=collected,
        ),
        stats,
        is_in_progress=False,
    )
    expected = (
        MapRecordProgress.COMPLETED_ALL_COLLECTIBLES
        if {MapEntityID('room', 1), MapEntityID('room', 2)} <= collected
        else MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES
    )
    assert progress is expected


@pytest.mark.parametrize('count', (1, 2))
def test_unidentified_or_duplicate_instances_cannot_prove_full_collection(count: int) -> None:
    entity_stats = MapEntityStats(
        counts={'strawberry': count},
        instance_ids={
            'strawberry': frozenset() if count == 1 else frozenset({MapEntityID('room', 1)})
        },
    )
    stats = MapStats(
        Duration.from_milliseconds(1),
        1,
        single_run_completed=True,
        collected_strawberries=frozenset({MapEntityID('room', 1), MapEntityID('room', 2)}),
    )
    assert map_record_progress(stats, entity_stats, is_in_progress=False) is (
        MapRecordProgress.COMPLETED_MISSING_COLLECTIBLES
    )


@pytest.mark.parametrize('side', ('A', 'B', 'C', None))
def test_record_side_validates_and_round_trips_existing_serialized_values(side: str | None) -> None:
    record = MapRecord.model_validate(
        {'created_at': '2026-10-10T00:00:00Z', 'map_name': 'Map', 'side': side}
    )
    assert record.side is (None if side is None else LevelSide(side))
    assert record.model_dump(mode='json')['side'] == side
    assert MapRecord.model_validate_json(record.model_dump_json()) == record


def test_record_side_rejects_invalid_serialized_value() -> None:
    with pytest.raises(ValueError):
        MapRecord.model_validate(
            {'created_at': '2026-10-10T00:00:00Z', 'map_name': 'Map', 'side': 'D'}
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
        mod=EXAMPLE_MOD,
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
    assert record.side is LevelSide.B
    assert record.save_slot == 0
    assert record.time_played == '0:01:23'
    assert record.deaths == 12
    assert not record.completed
    assert record.mod_name is None
    assert record.mod_url is None
    assert record.model_dump(include={'n_strawberries'}) == {'n_strawberries': None}


def test_create_map_record_preserves_configured_record_values() -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1000), 1)}),
        statistics={'n_strawberries': 3, 'cassette': True, 'heart': '通关收集'},
    )

    assert record.model_dump(include={'n_strawberries', 'cassette', 'heart'}) == {
        'n_strawberries': 3,
        'cassette': True,
        'heart': '通关收集',
    }


def test_create_map_record_marks_normal_map_as_a_side() -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}),
    )

    assert record.side is LevelSide.A


def test_create_map_record_rejects_vanilla_map() -> None:
    with pytest.raises(TypeError, match='vanilla map'):
        create_map_record(
            *make_level_side(file_path='Maps/0-Intro.bin', dialog_key='AREA_0'),
            mod=None,
            save_slot=SaveSlot(0, {}),
        )


def test_create_map_record_uses_gamebanana_submission_metadata() -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        mod=EXAMPLE_MOD,
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
    assert record.mod_updated_at == datetime(2026, 9, 4, 12, tzinfo=UTC).astimezone().date()


def test_create_map_record_omits_zero_time_stats() -> None:
    map_info = _example_map('Maps/Example/Map.bin', dialog_key='Example_Map')
    record = create_map_record(
        *map_info,
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(), 12)}),
    )

    assert record.save_slot == 0
    assert record.time_played is None
    assert record.deaths is None


def test_record_store_round_trips_a_record(tmp_path: Path) -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}),
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    store = RecordStore(tmp_path / '.pist/local-data.sqlite3')
    record_id = store.save(record)

    assert record_id == 1
    assert store.load(record_id) == record.model_copy(update={'local_id': record_id})


def test_record_store_updates_only_an_explicit_record_id(tmp_path: Path) -> None:
    map_info = _example_map('Maps/Example/Map.bin', dialog_key='Example_Map')
    store = RecordStore(tmp_path / '.pist/local-data.sqlite3')
    initial = create_map_record(
        *map_info,
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 2)}),
    )
    current = create_map_record(
        *map_info,
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(2_000), 5)}),
    )

    record_id = store.save(initial)
    assert store.matching_ids(current) == (record_id,)
    assert store.save(current.model_copy(update={'local_id': record_id})) == record_id

    saved = store.load(record_id)
    assert saved.time_played == '0:00:02'
    assert saved.deaths == 5


def test_merge_saved_record_retains_manual_values_but_refreshes_save_data() -> None:
    map_info = _example_map('Maps/Example/Map.bin', dialog_key='Example_Map')
    saved = create_map_record(
        *map_info,
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1000), 2)}),
        authors=('Alice',),
        statistics={
            'n_strawberries': 2,
            'started_at': datetime.fromisoformat('2026-09-01').replace(tzinfo=UTC).date(),
            'notes': '好图',
        },
    )
    current = create_map_record(
        *map_info,
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(2000), 5)}),
        statistics={'n_strawberries': 4, 'n_main_rooms': 8},
    )

    merged = merge_saved_record(current, saved)

    assert merged.authors == ('Alice',)
    assert merged.time_played == '0:00:02'
    assert merged.deaths == 5
    assert merged.model_dump(include={'n_strawberries', 'n_main_rooms', 'started_at', 'notes'}) == {
        'n_strawberries': 4,
        'n_main_rooms': 8,
        'started_at': datetime.fromisoformat('2026-09-01').replace(tzinfo=UTC).date(),
        'notes': '好图',
    }


@pytest.mark.parametrize('cleared', (False, True))
def test_merge_saved_record_preserves_edited_names_mod_link_and_update_date(
    tmp_path: Path, cleared: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    original = MapRecord(
        created_at=datetime(2026, 10, 10, tzinfo=UTC),
        mod_metadata_name='Example',
        map_file='Maps/Example/Map.bin',
        map_name='My Map',
        mod_name=None if cleared else 'My Mod',
        mod_url=None if cleared else 'https://gamebanana.com/mods/456',
        mod_updated_at=None if cleared else datetime(2026, 9, 1, tzinfo=UTC),
        time_played='0:01:00',
        status='进行中',
        save_slot=0,
    )
    saved = store.load(store.save(original))
    scanned = original.model_copy(
        update={
            'map_name': 'Scanned Map',
            'mod_name': 'Scanned Mod',
            'mod_url': 'https://gamebanana.com/mods/123',
            'mod_updated_at': datetime(2026, 10, 10, tzinfo=UTC).date(),
            'save_slot': 1,
            'time_played': '0:02:00',
        }
    )
    merged = merge_saved_record(scanned, saved)
    store.save(merged)
    assert saved.local_id is not None
    latest = store.load(saved.local_id)
    assert (latest.map_name, latest.mod_name, latest.mod_url) == (
        saved.map_name,
        saved.mod_name,
        saved.mod_url,
    )
    assert latest.record_number == saved.record_number
    assert latest.mod_updated_at == saved.mod_updated_at
    assert latest.save_slot == 1 and latest.time_played == '0:02:00'


def test_record_store_preserves_missing_stats_without_inventing_a_number(tmp_path: Path) -> None:
    record = create_map_record(
        *_example_map('Maps/Example/Map.bin', dialog_key='Example_Map'),
        mod=EXAMPLE_MOD,
        save_slot=SaveSlot(0, {}),
    )

    store = RecordStore(tmp_path / '.pist/local-data.sqlite3')
    saved = store.load(store.save(record))
    assert saved.time_played is None
    assert saved.record_number is None


def test_new_records_of_the_same_map_remain_separate(tmp_path: Path) -> None:
    record = MapRecord(
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        mod_metadata_name='Example',
        map_name='Map',
        time_played='0:01:00',
        status='进行中',
    )
    store = RecordStore(tmp_path / 'records.sqlite3')
    first = store.load(store.save(record))
    second = store.load(store.save(record))
    assert first.local_id is not None and second.local_id is not None
    assert first.local_id != second.local_id
    assert (first.record_number, second.record_number) == (1, 2)
    assert store.matching_ids(record) == (first.local_id, second.local_id)
    updated = first.model_copy(update={'deaths': 3})
    store.save(updated)
    assert store.load(first.local_id).record_number == 1
    assert store.load(second.local_id).deaths is None


def test_record_store_keeps_numbers_and_remote_links_unique(tmp_path: Path) -> None:
    record = MapRecord(
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        record_number=166,
        mod_metadata_name='Example',
        map_name='Map',
    )
    store = RecordStore(tmp_path / 'records.sqlite3')
    record_id = store.save(record)
    link = RecordSheetLink(local_id=record_id, file_id='file', sheet_id='sheet', record_id='remote')
    store.link_sheet_record(link)
    assert store.sheet_links(record_id) == (link,)
    import sqlite3

    with pytest.raises(RecordStorageError) as caught:
        store.save(record)
    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)
    assert store.matching_ids(record) == (record_id,)
    with pytest.raises(RecordStorageError) as caught:
        store.link_sheet_record(link.model_copy(update={'local_id': 999}))
    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)
    with pytest.raises(ValueError, match='formal record number'):
        store.save(store.load(record_id).model_copy(update={'record_number': 7}))
    next_record = record.model_copy(
        update={'record_number': None, 'time_played': '0:01:00', 'status': '进行中'}
    )
    assert store.load(store.save(next_record)).record_number == 167
