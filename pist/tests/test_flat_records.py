"""Flat records, complete public-field round trips and independent observations."""

import asyncio
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
from sheet_factory import FakeSheet, make_sheet_fields
from textual.app import App
from textual.widgets import Button, Input, Select, TextArea
from textual.widgets._select import InvalidSelectValueError

from berries.game.levels import LevelSide
from pist.records.fields import RECORD_ATTRIBUTES
from pist.records.models import MapRecord
from pist.records.schema import RECORD_COLUMNS
from pist.records.store import RecordSheetLink, RecordStorageError, RecordStore, RecordSyncState
from pist.records.sync import RecordSyncService
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.fields import load_sheet_fields
from pist.smartsheet.models import (
    MainSheetSnapshot,
    RecordWrite,
    RecordWriteOptions,
    SmartSheetRecord,
)
from pist.ui.records.editor import RecordEditorScreen

NOW = datetime(2026, 10, 10, 12, 34, 56, 123000, tzinfo=UTC)
CSS = Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss'


def full_record() -> MapRecord:
    return MapRecord(
        created_at=NOW,
        mod_metadata_name='Example',
        mod_name='Example Mod',
        mod_url='https://gamebanana.com/mods/1',
        mod_updated_at=NOW.date(),
        map_name='Map',
        video_url='https://example.com/video',
        authors=('Alice', 'Bob'),
        map_file='Maps/Map.bin',
        sid='Map',
        side=LevelSide.A,
        save_slot=2,
        difficulty='Game difficulty',
        time_played='1:02:03',
        deaths=0,
        completed=False,
        n_strawberries=0,
        n_moonberries=1,
        cassette=False,
        heart='额外收集',
        n_main_rooms=3,
        status='进行中',
        tags=('Techspam', '挑战'),
        perceived_difficulty='高级',
        perceived_difficulty_tier='简单',
        rated_difficulty='专家级',
        rated_difficulty_tier='中等',
        started_at=NOW.date(),
        finished_at=NOW.date(),
        save_load_usage='练习',
        rating=0,
        notes='First line\nSecond line',
    )


def test_flat_storage_round_trips_all_fields_without_payload(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    record = full_record()
    local_id = store.save(record)
    saved = store.load(local_id)
    assert saved.side is LevelSide.A
    assert saved == record.model_copy(update={'local_id': local_id, 'record_number': 1})
    assert set(RECORD_COLUMNS) == set(MapRecord.model_fields) - {'local_id'}
    with store.connect() as conn:
        assert (
            conn.execute('SELECT side FROM records WHERE id = ?', (local_id,)).fetchone()[0] == 'A'
        )
        columns = {row[1] for row in conn.execute('PRAGMA table_info(records)')}
        assert columns == set(RECORD_COLUMNS) | {'id', 'sync_state'}
        assert 'payload' not in columns and 'record_values' not in columns
    store.save(saved.model_copy(update={'tags': (), 'notes': None, 'cassette': False}))
    assert store.load(local_id).tags == ()
    assert store.load(local_id).notes is None
    assert store.load(local_id).cassette is False


def test_cassette_defaults_to_false_and_cannot_be_saved_as_null(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    record = MapRecord(created_at=NOW, map_name='Map')
    assert record.cassette is False
    local_id = store.save(record)
    assert store.load(local_id).cassette is False
    with pytest.raises(ValueError):
        MapRecord.model_validate({'created_at': NOW, 'map_name': 'Map', 'cassette': None})
    with pytest.raises(RecordStorageError), store.connect() as conn:
        conn.execute('UPDATE records SET cassette = NULL WHERE id = ?', (local_id,))
    assert store.load(local_id).cassette is False
    with store.connect() as conn:
        column = next(
            row for row in conn.execute('PRAGMA table_info(records)') if row[1] == 'cassette'
        )
        assert column[3] == 1 and column[4] == '0'


def test_all_public_columns_round_trip_and_clear_without_clearing_missing_columns() -> None:
    contract = load_sheet_fields()
    schema = make_sheet_fields(*((spec.title, spec.type) for spec in contract.fields))
    record = full_record()
    encoded = encode_record_values(record, schema)
    assert set(encoded) == set(RECORD_ATTRIBUTES)
    assert encoded['磁带'] is False and encoded['死亡数'] == 0 and encoded['评分'] == 0
    adopted = contract.apply_remote(
        record.model_copy(update={'notes': None, 'tags': ()}), encoded, schema
    )
    assert adopted == record
    cleared = record.model_copy(
        update={
            'mod_name': None,
            'mod_url': None,
            'authors': (),
            'tags': (),
            'notes': None,
            'cassette': False,
            'heart': None,
            'rating': None,
            'started_at': None,
            'video_url': None,
        }
    )
    cells = encode_record_values(cleared, schema)
    assert cells['标签'] == [] and cells['备注'] == [] and cells['磁带'] is False
    assert cells['起始日期'] is None and cells['评分'] is None
    submitted = RecordWriteOptions(
        records=(RecordWrite(record_id='row', values=cells),)
    ).model_dump(exclude_none=True)
    assert submitted['records'][0]['values']['起始日期'] is None
    assert cleared.cassette is False
    assert contract.apply_remote(record, cells, schema) == cleared
    partial = contract.apply_remote(
        record, {'地图名': [{'text': 'Other'}]}, make_sheet_fields(('地图名', 1))
    )
    assert partial.tags == record.tags and partial.notes == record.notes


def test_observed_formula_and_unknown_cells_do_not_advance_confirmation(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    record = store.load(store.save(full_record()))
    assert record.local_id is not None
    link = RecordSheetLink(
        local_id=record.local_id, file_id='file', sheet_id='main', record_id='row'
    )
    store.confirm_sheet_record(link, {'备注': [{'text': 'A'}]}, submitted=record, confirmed_at=NOW)
    schema = make_sheet_fields(
        ('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 1), ('草莓数', 19), ('新列', 1)
    )
    formula = schema.fields[-2].model_copy(update={'property_formula': {'formulaModel': []}})
    schema = schema.model_copy(update={'fields': [*schema.fields[:-2], formula, schema.fields[-1]]})
    values = {'备注': [{'text': 'B'}], '草莓数': 1, '新列': [{'text': 'Must retain'}]}
    snapshot = MainSheetSnapshot(
        file_id='file',
        sheet_id='main',
        fields=schema,
        records=(SmartSheetRecord.model_validate({'recordID': 'row', 'values': values}),),
    )
    store.observe_sheet(snapshot, read_at=NOW)
    observed = store.sheet_snapshots(record.local_id)[0]
    assert observed.values == values and observed.fields == schema and observed.read_at == NOW
    assert store.sheet_links(record.local_id)[0].confirmed_values == {'备注': [{'text': 'A'}]}
    assert store.sync_state(record.local_id) is RecordSyncState.SYNCED
    assert load_sheet_fields().issues(schema) == ('尚未支持的表格字段：新列',)
    assert '草莓数' not in encode_record_values(record, schema)


def test_tags_and_clearing_are_written_and_verified(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.fields = make_sheet_fields(
        *((spec.title, spec.type) for spec in load_sheet_fields().fields)
    )
    record = store.load(store.save(full_record()))
    assert record.local_id is not None
    values = encode_record_values(record, client.fields)
    client.rows['row'] = values
    store.confirm_sheet_record(
        RecordSheetLink(local_id=record.local_id, file_id='file', sheet_id='main', record_id='row'),
        values,
        submitted=record,
        confirmed_at=NOW,
    )
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        assert record.local_id is not None
        check = await service.check(record)
        edited = record.model_copy(
            update={'tags': (), 'notes': None, 'finished_at': None, 'cassette': False}
        )
        result = await service.save(edited, check)
        assert result.synced and result.error is None
        assert client.rows['row']['标签'] == [] and client.rows['row']['备注'] == []
        assert client.rows['row']['结束日期'] is None and client.rows['row']['磁带'] is False
        assert store.load(record.local_id) == edited

    asyncio.run(run())


def test_editor_exposes_every_public_field_and_clears_values() -> None:
    record = full_record()
    results: list[MapRecord | None] = []

    async def run() -> None:
        app = App[None](css_path=CSS)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = RecordEditorScreen(record)
            app.push_screen(screen, results.append)
            await pilot.pause()
            for field in screen._manual_fields:
                control = screen.query_one(f'#{screen._manual_field_id(field)}')
                if field.title == '标签':
                    assert isinstance(control, Input)
                    control.value = 'New tag,Another tag'
                elif field.title in {'作者', '备注', '结束日期', '评分'}:
                    if isinstance(control, TextArea):
                        control.load_text('')
                    else:
                        assert isinstance(control, Input)
                        control.value = ''
                elif field.title == '磁带':
                    assert isinstance(control, Select)
                    with pytest.raises(InvalidSelectValueError):
                        control.clear()
                    control.value = False
            screen.query_one('#record-confirm-save', Button).press()
            await pilot.pause()

    asyncio.run(run())
    assert len(results) == 1 and results[0] is not None
    saved = results[0]
    assert saved.tags == ('New tag', 'Another tag') and saved.authors == ()
    assert (
        saved.notes is None
        and saved.finished_at is None
        and saved.rating is None
        and saved.cassette is False
    )
    assert saved.started_at == NOW.date() and saved.mod_updated_at == NOW.date()


@pytest.mark.parametrize('value', (-1, 0))
def test_main_room_count_requires_positive_number_or_none(value: int) -> None:
    with pytest.raises(ValueError):
        MapRecord(created_at=NOW, map_name='Map', n_main_rooms=value)
    contract = load_sheet_fields()
    schema = make_sheet_fields(('主房间数', 2))
    record = full_record()
    assert contract.apply_remote(record, {'主房间数': value}, schema).n_main_rooms is None
    assert contract.comparable({'主房间数': value}, schema)['主房间数'] == value


def test_editor_preserves_multiline_notes_and_unmodified_values() -> None:
    record = full_record()
    results: list[MapRecord | None] = []

    async def run() -> None:
        app = App[None](css_path=CSS)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = RecordEditorScreen(record)
            app.push_screen(screen, results.append)
            await pilot.pause()
            screen.query_one('#record-confirm-save', Button).press()
            await pilot.pause()

    asyncio.run(run())
    assert results == [record]


def test_editor_rejects_room_placeholders_and_allows_clearing_by_keyboard() -> None:
    record = full_record()
    results: list[MapRecord | None] = []

    async def run() -> None:
        app = App[None](css_path=CSS)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = RecordEditorScreen(record)
            app.push_screen(screen, results.append)
            await pilot.pause()
            fields = [
                screen.query_one(f'#{screen._manual_field_id(field)}')
                for field in screen._manual_fields
            ]
            ordered = sorted(fields, key=lambda field: (field.region.y, field.region.x))
            for previous, following in pairwise(ordered):
                if previous.region.y == following.region.y:
                    assert previous.region.right <= following.region.x
                else:
                    assert previous.region.bottom <= following.region.y
            field = screen._manual_field('主房间数')
            assert field is not None
            control = screen.query_one(f'#{screen._manual_field_id(field)}', Input)
            for placeholder in ('-1', '0'):
                control.value = placeholder
                screen.query_one('#record-confirm-save', Button).press()
                await pilot.pause()
                assert results == [] and app.screen is screen
            control.focus()
            await pilot.press('home', 'shift+end', 'backspace')
            assert control.value == ''
            screen.query_one('#record-confirm-save', Button).press()
            await pilot.pause()

    asyncio.run(run())
    assert len(results) == 1 and results[0] is not None
    assert results[0].n_main_rooms is None and results[0].notes == record.notes
