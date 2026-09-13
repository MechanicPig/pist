from datetime import UTC, datetime
from pathlib import Path

import pytest

from pist.drafts import create_record_draft, merge_saved_draft
from pist.game.routes import MapRoute
from pist.game.saves import MapStats, SaveSlot
from pist.local_data import LocalDataStore
from pist.models import GameBananaSubmission, InstalledMod, LocalMap
from pist.time import Time


def test_create_record_draft_uses_local_map_and_native_save_data(tmp_path: Path) -> None:
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

    draft = create_record_draft(
        mod,
        map_info,
        save_slot=save_slot,
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert draft.mod_metadata_name == 'ExampleMetadata'
    assert draft.map_name == '示例地图 B'
    assert draft.map_english_name == 'Example Map B'
    assert draft.sid == 'Author/Pack/Map'
    assert draft.side == 'B'
    assert draft.save_slot == 0
    assert draft.time_played == '0:01:23'
    assert draft.deaths == 12
    assert not draft.completed
    assert draft.mod_name is None
    assert draft.mod_url is None
    assert draft.table_values == {}


def test_create_record_draft_preserves_configured_table_values() -> None:
    draft = create_record_draft(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
        table_values={'主表': {'红草莓数': 3, '磁带': True, '水晶之心': '通关收集'}},
    )

    assert draft.table_values == {'主表': {'红草莓数': 3, '磁带': True, '水晶之心': '通关收集'}}


def test_create_record_draft_marks_normal_map_as_a_side() -> None:
    draft = create_record_draft(
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

    assert draft.side == 'A'


def test_create_record_draft_uses_gamebanana_submission_metadata() -> None:
    draft = create_record_draft(
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

    assert draft.mod_name == 'Example Mod'
    assert draft.mod_url == 'https://gamebanana.com/mods/123'
    assert draft.authors == ('Alice', 'Bob')
    assert draft.credits == [
        {
            'groupName': 'Creator',
            'authors': [
                {'name': 'Alice', 'role': '', 'url': ''},
                {'name': 'Bob', 'role': '', 'url': ''},
            ],
        }
    ]
    assert draft.mod_updated_at is not None
    assert draft.mod_updated_at.isoformat() == '2026-09-04T12:00:00+00:00'


def test_create_record_draft_omits_zero_time_stats() -> None:
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    draft = create_record_draft(
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

    assert draft.save_slot == 0
    assert draft.time_played is None
    assert draft.deaths is None


def test_local_data_store_round_trips_a_draft(tmp_path: Path) -> None:
    draft = create_record_draft(
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
    draft_id = store.save_draft(draft)

    assert draft_id == 1
    assert store.load_draft(draft_id) == draft


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
    initial = create_record_draft(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 2)}),
    )
    current = create_record_draft(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(2_000), 5)}),
    )

    draft_id = store.save_draft(initial)
    assert store.existing_draft_id(current) == draft_id
    assert store.save_draft(current) == draft_id

    saved = store.load_draft(draft_id)
    assert saved.time_played == '0:00:02'
    assert saved.deaths == 5


def test_merge_saved_draft_retains_manual_values_but_refreshes_save_data() -> None:
    mod = InstalledMod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='ExampleMetadata',
        metadata_version='1.0.0',
    )
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    saved = create_record_draft(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 2)}),
        authors=('Alice',),
        table_values={'主表': {'红草莓数': 2, '起始日期': '2026-09-01', '备注': '好图'}},
    )
    current = create_record_draft(
        mod,
        map_info,
        save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(2_000), 5)}),
        table_values={'主表': {'红草莓数': 4, '主房间数': 8}},
    )

    merged = merge_saved_draft(current, saved)

    assert merged.authors == ('Alice',)
    assert merged.time_played == '0:00:02'
    assert merged.deaths == 5
    assert merged.table_values == {
        '主表': {'红草莓数': 4, '主房间数': 8, '起始日期': '2026-09-01', '备注': '好图'}
    }


def test_local_data_store_rejects_draft_without_native_play_time(tmp_path: Path) -> None:
    draft = create_record_draft(
        InstalledMod(
            source='zip',
            filename='Example.zip',
            path='C:/Celeste/Mods/Example.zip',
            metadata_name='ExampleMetadata',
            metadata_version='1.0.0',
        ),
        LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map'),
        save_slot=None,
    )

    with pytest.raises(ValueError, match='without native play time'):
        LocalDataStore(tmp_path / '.pist/local-data.sqlite3').save_draft(draft)


def test_local_data_store_imports_legacy_draft_and_route_json(tmp_path: Path) -> None:
    draft = create_record_draft(
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
    data_dir = tmp_path / '.pist'
    (data_dir / 'drafts').mkdir(parents=True)
    (data_dir / 'routes').mkdir()
    (data_dir / 'drafts/draft.json').write_text(draft.model_dump_json(), encoding='utf-8')
    route = MapRoute(map_file='Maps/Example/Map.bin', rooms=('room',))
    (data_dir / 'routes/route.json').write_text(route.model_dump_json(), encoding='utf-8')

    store = LocalDataStore(data_dir / 'local-data.sqlite3')

    assert store.load_draft(1) == draft
    assert store.load_route(route.map_file) == route
