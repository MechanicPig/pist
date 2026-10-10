"""Exercise local history independently of installed maps and native saves."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.text import Text
from sheet_factory import FakeSheet
from textual.widgets import Button, Checkbox, DataTable, Input
from textual.widgets.data_table import ColumnKey

from berries.game.mods import ModScanReport
from pist.app_data import AppDataStores
from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.records.sync import RecordSyncService
from pist.settings import PistSettings, RecordListMaxWidths, SettingsStore
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import MainSheetSnapshot
from pist.ui.maps.browser.app import MapBrowserApp
from pist.ui.records.app import RecordBrowserApp
from pist.ui.records.editor import RecordEditorScreen
from pist.ui.records.list import RecordListScreen
from pist.ui.records.sync import RecordConflictScreen, RecordOfflineScreen, RecordSyncChoice
from pist.ui.records.table import RecordTable

NOW = datetime(2026, 10, 10, tzinfo=UTC)


@pytest.mark.parametrize('conflict', (False, True))
def test_sync_all_ignores_filters_and_stops_on_cancel(tmp_path: Path, conflict: bool) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = [save_record(store, name) for name in ('First', 'Second', 'Third')]
    for index, record in enumerate(records):
        assert record.local_id is not None
        row_id = str(index)
        values = encode_record_values(record, client.fields)
        client.rows[row_id] = values
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id, file_id='file', sheet_id='main', record_id=row_id
            ),
            values,
            submitted=record,
            confirmed_at=NOW,
        )
    with store.connect() as conn:
        conn.execute('UPDATE records SET sync_state = ?', (RecordSyncState.UNCHECKED,))
    if conflict:
        client.rows['1']['备注'] = [{'text': 'Remote change'}]
    else:
        store.save(records[1].model_copy(update={'notes': 'Local change'}))

    async def run() -> None:
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one('#record-search', Input).value = 'First'
            screen.query_one('#record-pending', Checkbox).value = True
            await pilot.pause()
            assert screen.query_one(DataTable).row_count == 1
            await pilot.click('#record-list-sync-all')
            await pilot.pause()
            if conflict:
                assert isinstance(app.screen, RecordConflictScreen)
                assert screen.query_one('#record-list-sync-all', Button).disabled
                assert screen.query_one('#record-list-edit', Button).disabled
                app.screen.dismiss(None)
                await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.screen is screen
            assert screen.query_one('#record-list-refresh', Button).disabled is False
            states = [store.sync_state(record.local_id) for record in records if record.local_id]
            assert states == (
                [RecordSyncState.SYNCED, RecordSyncState.UNCHECKED, RecordSyncState.UNCHECKED]
                if conflict
                else [RecordSyncState.SYNCED] * 3
            )
            assert len(client.writes) == (0 if conflict else 1)
            if not conflict:
                assert client.writes[0][0] == '1'
                assert client.rows['1']['备注'] == [{'type': 'text', 'text': 'Local change'}]
                assert screen.query_one('#record-list-sync-all', Button).disabled
            assert screen.query_one(DataTable).row_count == 0

    asyncio.run(run())


def test_sync_all_read_failure_preserves_records_and_restores_actions(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = [save_record(store, name) for name in ('First', 'Second')]
    client = FakeSheet()
    client.read_error = True

    async def run() -> None:
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.click('#record-list-sync-all')
            await pilot.pause()
            assert isinstance(app.screen, RecordOfflineScreen)
            app.screen.dismiss(None)
            await app.workers.wait_for_complete()
            assert not screen.query_one('#record-list-sync-all', Button).disabled
            assert [store.load(record.local_id) for record in records if record.local_id] == records
            assert client.writes == []

    asyncio.run(run())


def test_sync_all_uses_one_read_and_no_writes_for_equal_rows(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    for index in range(3):
        record = save_record(store, f'Map {index}')
        assert record.local_id is not None
        values = encode_record_values(record, client.fields)
        client.rows[str(index)] = values
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id, file_id='file', sheet_id='main', record_id=str(index)
            ),
            values,
            submitted=record,
            confirmed_at=NOW,
        )
        store.save(record)

    async def run() -> None:
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click('#record-list-sync-all')
            await app.workers.wait_for_complete()
            assert all(entry.sync_state is RecordSyncState.SYNCED for entry in store.list_records())
            assert client.reads == 1 and client.writes == []

    asyncio.run(run())


@pytest.mark.parametrize('choice', (None, RecordSyncChoice.LOCAL, RecordSyncChoice.REMOTE))
def test_batch_prewrite_conflict_keeps_the_existing_choice_dialog(
    tmp_path: Path,
    choice: RecordSyncChoice | None,
) -> None:
    class ChangedBeforeWrite(FakeSheet):
        async def read_main_sheet(
            self, source: str, *, record_ids: tuple[str, ...] | None = None
        ) -> MainSheetSnapshot:
            if self.reads == 1:
                self.rows['0']['备注'] = [{'text': 'Remote change'}]
            return await super().read_main_sheet(source, record_ids=record_ids)

    store = RecordStore(tmp_path / 'records.sqlite3')
    client = ChangedBeforeWrite()
    record = save_record(store)
    assert record.local_id is not None
    values = encode_record_values(record, client.fields)
    client.rows['0'] = values
    store.confirm_sheet_record(
        RecordSheetLink(local_id=record.local_id, file_id='file', sheet_id='main', record_id='0'),
        values,
        submitted=record,
        confirmed_at=NOW,
    )
    store.save(record.model_copy(update={'notes': 'Local change'}))

    async def run() -> None:
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click('#record-list-sync-all')
            await pilot.pause()
            assert isinstance(app.screen, RecordConflictScreen)
            assert client.writes == []
            app.screen.dismiss(choice)
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, RecordListScreen)
            assert record.local_id is not None
            assert store.sync_state(record.local_id) is (
                RecordSyncState.PENDING if choice is None else RecordSyncState.SYNCED
            )
            assert len(client.update_batches) == (1 if choice is RecordSyncChoice.LOCAL else 0)
            assert store.load(record.local_id).notes == (
                'Remote change' if choice is RecordSyncChoice.REMOTE else 'Local change'
            )

    asyncio.run(run())


def test_drag_scrolled_empty_table_captures_mouse_and_clamps_minimum(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    settings_store = SettingsStore(tmp_path / 'settings.json')
    settings_store.save(PistSettings())

    async def run() -> None:
        app = RecordBrowserApp(store, settings_store=settings_store)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(RecordTable)
            assert table.row_count == 0
            table.scroll_to(x=30, animate=False)
            await pilot.pause()
            edge = (
                sum(column.get_render_width(table) for column in table.ordered_columns[:3])
                - 2
                - int(table.scroll_x)
            )
            await pilot.mouse_down(table, offset=(edge, 0))
            await pilot.hover(offset=(0, table.region.bottom + 1))
            await pilot.mouse_up(offset=(0, table.region.bottom + 1))
            await pilot.pause()
            assert table.columns[ColumnKey('mod_name')].width == 4
            assert settings_store.load().record_list_max_widths.mod_name == 4
            assert app.mouse_captured is None
            assert isinstance(app.screen, RecordListScreen)

    asyncio.run(run())


@pytest.mark.parametrize('entry', ('records', 'maps'))
def test_drag_column_persists_on_release_and_restores_on_reopen(tmp_path: Path, entry: str) -> None:
    stores = AppDataStores(tmp_path / 'records.sqlite3')
    record = save_record(stores.records, '中文地图' * 20)
    save_record(stores.records)
    settings_store = SettingsStore(tmp_path / 'settings.json')
    settings_store.save(PistSettings(theme='textual-light', dialog_languages=('en',)))
    app = (
        RecordBrowserApp(stores.records, settings_store=settings_store)
        if entry == 'records'
        else MapBrowserApp(
            ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[]),
            data_stores=stores,
            settings_store=settings_store,
        )
    )

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            if entry == 'maps':
                await pilot.press('r')
            await pilot.pause()
            table = app.screen.query_one(RecordTable)
            table.move_cursor(row=1)
            edge = sum(column.get_render_width(table) for column in table.ordered_columns[:2]) - 2
            await pilot.mouse_down(table, offset=(edge, 0))
            await pilot.hover(table, offset=(edge - 8, 2))
            assert table.columns[ColumnKey('map_name')].width == 32
            assert settings_store.load().record_list_max_widths.map_name == 40
            await pilot.mouse_up(table, offset=(edge - 8, 2))
            await pilot.pause()
            settings = settings_store.load()
            assert settings.record_list_max_widths.map_name == 32
            assert settings.theme == 'textual-light' and settings.dialog_languages == ('en',)
            assert table.cursor_row == 1
            assert isinstance(app.screen, RecordListScreen)
            await pilot.press('escape')
            if entry == 'maps':
                await pilot.press('r')
                await pilot.pause()
                assert app.screen.query_one(RecordTable).columns[ColumnKey('map_name')].width == 32
        restarted = RecordBrowserApp(stores.records, settings_store=settings_store)
        async with restarted.run_test() as pilot:
            await pilot.pause()
            assert (
                restarted.screen.query_one(RecordTable).columns[ColumnKey('map_name')].width == 32
            )
        assert record.local_id is not None and stores.records.load(record.local_id) == record

    asyncio.run(run())


def test_drag_column_restores_width_after_settings_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    save_record(store)
    settings_store = SettingsStore(tmp_path / 'settings.json')
    settings_store.save(PistSettings())

    def fail_save(settings: PistSettings) -> None:
        raise OSError('disk unavailable')

    monkeypatch.setattr(settings_store, 'save', fail_save)

    async def run() -> None:
        app = RecordBrowserApp(store, settings_store=settings_store)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(RecordTable)
            edge = sum(column.get_render_width(table) for column in table.ordered_columns[:2]) - 2
            await pilot.mouse_down(table, offset=(edge, 0))
            await pilot.mouse_up(table, offset=(edge + 5, 0))
            await pilot.pause()
            assert table.columns[ColumnKey('map_name')].width == 40
            assert settings_store.load().record_list_max_widths.map_name == 40
            assert app.diagnostic_count == 1

    asyncio.run(run())


@pytest.mark.parametrize('entry', ('records', 'maps'))
def test_record_list_entries_use_persisted_column_widths(tmp_path: Path, entry: str) -> None:
    stores = AppDataStores(tmp_path / 'records.sqlite3')
    save_record(stores.records, '中文地图' * 20)
    settings_store = SettingsStore(tmp_path / 'settings.json')
    settings_store.save(
        PistSettings(record_list_max_widths=RecordListMaxWidths(map_name=10, mod_name=4))
    )
    if entry == 'records':
        app = RecordBrowserApp(stores.records, settings_store=settings_store)
    else:
        app = MapBrowserApp(
            ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[]),
            data_stores=stores,
            settings_store=settings_store,
        )

    async def run() -> None:
        async with app.run_test() as pilot:
            if entry == 'maps':
                await pilot.press('r')
            await pilot.pause()
            cells = app.screen.query_one(DataTable).get_row_at(0)
            map_name, mod_name = cells[1:3]
            assert isinstance(map_name, Text) and isinstance(mod_name, Text)
            assert map_name.cell_len <= 10 and map_name.plain.endswith('…')
            assert mod_name.cell_len <= 4 and mod_name.plain.endswith('…')
            assert settings_store.load().record_list_max_widths.map_name == 10

    asyncio.run(run())


def test_long_names_are_ellipsized_but_full_names_remain_searchable(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    saved = save_record(store, '中文地图' * 30 + 'searchable-tail')
    assert saved.local_id is not None
    store.save(saved.model_copy(update={'mod_name': '[Long Mod] ' * 30}))
    original = store.load(saved.local_id)

    async def run() -> None:
        assert original.local_id is not None
        app = RecordBrowserApp(store)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            cells = table.get_row_at(0)
            map_name, mod_name = cells[1:3]
            assert isinstance(map_name, Text) and isinstance(mod_name, Text)
            assert map_name.cell_len <= 40 and map_name.plain.endswith('…')
            assert mod_name.cell_len <= 32 and mod_name.plain.endswith('…')
            assert '[Long Mod]' in mod_name.plain
            assert list(table.columns.values())[1].width <= 40
            assert list(table.columns.values())[2].width <= 32
            app.screen.query_one('#record-search', Input).value = 'searchable-tail'
            await pilot.pause()
            assert table.row_count == 1
            assert store.load(original.local_id) == original

    asyncio.run(run())


def test_maps_browser_opens_shared_history_without_installed_maps(tmp_path: Path) -> None:
    stores = AppDataStores(tmp_path / 'records.sqlite3')
    save_record(stores.records)
    app = MapBrowserApp(
        ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[]),
        data_stores=stores,
    )

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press('r')
            await pilot.pause()
            assert isinstance(app.screen, RecordListScreen)
            assert app.screen.query_one(DataTable).row_count == 1
            await pilot.press('escape')
            await pilot.pause()
            assert not isinstance(app.screen, RecordListScreen)

    asyncio.run(run())


def save_record(store: RecordStore, name: str = 'Uninstalled Map') -> MapRecord:
    local_id = store.save(
        MapRecord(
            created_at=NOW,
            map_name=name,
            mod_metadata_name='AbsentMod',
            map_file='Maps/Missing.bin',
            time_played='0:01:00',
            deaths=3,
            save_slot=0,
            status='进行中',
            notes='Before',
        )
    )
    return store.load(local_id)


def test_list_records_orders_numbered_history_before_unstarted_entries(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    first = store.save(MapRecord(created_at=NOW, map_name='Registered', status='未开始'))
    second = save_record(store).local_id
    entries = store.list_records()
    assert [entry.record.local_id for entry in entries] == [second, first]
    assert all(entry.sync_state is RecordSyncState.PENDING for entry in entries)


@pytest.mark.parametrize('size', ((120, 40), (80, 24)))
def test_search_empty_results_and_edit_exact_historical_record(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    first = save_record(store)
    second = save_record(store)
    assert first.local_id is not None and second.local_id is not None

    async def run() -> None:
        assert first.local_id is not None and second.local_id is not None
        app = RecordBrowserApp(store)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordListScreen)
            table = screen.query_one(DataTable)
            assert table.row_count == 2
            screen.query_one('#record-search', Input).value = 'no match'
            await pilot.pause()
            assert table.row_count == 0
            assert screen.query_one('#record-list-edit', Button).disabled
            screen.query_one('#record-search', Input).value = 'absentmod'
            await pilot.pause()
            assert table.row_count == 2
            table.move_cursor(row=1)
            await pilot.click('#record-list-edit')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert app.screen.query_one('#record-confirm-save', Button).region.width > 0
            # Edit the full local field set; no game refresh or target chooser occurs.
            app.screen.query_one('#record-video-url', Input).value = 'https://example.com/video'
            await pilot.click('#record-confirm-save')
            await pilot.pause()
            assert isinstance(app.screen, RecordListScreen)
            assert store.load(second.local_id).video_url == 'https://example.com/video'
            assert store.load(first.local_id) == first
            assert store.load(second.local_id).deaths == 3
            assert store.load(second.local_id).record_number == second.record_number
            await pilot.click('#record-list-edit')
            await pilot.pause()
            await pilot.click('#record-confirm-cancel')
            await pilot.pause()
            assert store.load(second.local_id).video_url == 'https://example.com/video'
            await pilot.click('#record-list-back')

    asyncio.run(run())


def test_pending_filter_and_sync_retry_use_existing_remote_identity(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    record = save_record(store)
    client = FakeSheet()
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        result = await service.save(record, await service.check(record))
        saved = result.record
        assert saved.local_id is not None
        store.save(saved.model_copy(update={'notes': 'Latest'}))
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one('#record-pending', Checkbox).value = True
            await pilot.pause()
            assert app.screen.query_one(DataTable).row_count == 1
            await pilot.click('#record-list-sync')
            await pilot.pause()
            assert store.sync_state(saved.local_id) is RecordSyncState.SYNCED
            assert app.screen.query_one(DataTable).row_count == 0
            assert [id_ for id_, _ in client.writes] == [None, 'created']
            assert store.load(saved.local_id).notes == 'Latest'

    asyncio.run(run())


@pytest.mark.parametrize(
    'choice', (None, RecordSyncChoice.ONLY_LOCAL, RecordSyncChoice.LOCAL, RecordSyncChoice.REMOTE)
)
def test_retry_conflict_requires_explicit_choice(
    tmp_path: Path, choice: RecordSyncChoice | None
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    record = save_record(store)
    client = FakeSheet()
    service = RecordSyncService(store, client, 'source')

    async def run() -> None:
        saved = (await service.save(record, await service.check(record))).record
        assert saved.local_id is not None
        store.save(saved.model_copy(update={'notes': 'Local'}))
        client.rows['created'] = encode_record_values(
            saved.model_copy(update={'notes': 'Remote'}),
            client.fields,
        )
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click('#record-list-sync')
            await pilot.pause()
            assert isinstance(app.screen, RecordConflictScreen)
            app.screen.dismiss(choice)
            await pilot.pause()
            if choice is None or choice is RecordSyncChoice.ONLY_LOCAL:
                assert store.load(saved.local_id).notes == 'Local'
                assert store.sync_state(saved.local_id) is RecordSyncState.PENDING
                assert len(client.writes) == 1
            else:
                assert store.load(saved.local_id).notes == (
                    'Remote' if choice is RecordSyncChoice.REMOTE else 'Local'
                )
                assert store.sync_state(saved.local_id) is RecordSyncState.SYNCED
                assert len(client.writes) == (1 if choice is RecordSyncChoice.REMOTE else 2)

    asyncio.run(run())


def test_read_failure_cancel_does_not_save_or_sync(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    saved = save_record(store)
    client = FakeSheet()
    client.read_error = True

    async def run() -> None:
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click('#record-list-sync')
            await pilot.pause()
            assert isinstance(app.screen, RecordOfflineScreen)
            app.screen.dismiss(None)
            await pilot.pause()
            assert saved.local_id is not None
            assert store.load(saved.local_id) == saved
            assert client.writes == []

    asyncio.run(run())
