"""Editing another record cannot cancel a saved edit's queued synchronization."""

import asyncio
import gc
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sheet_factory import FakeSheet
from textual.app import App
from textual.widgets import DataTable
from textual.worker import WorkerCancelled

from berries.game.duration import Duration
from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import (
    JsonObject,
    MainSheetSnapshot,
    SmartSheetRecord,
    SmartSheetSelect,
)
from pist.smartsheet.report import ManualRecordField
from pist.ui.records.app import RecordBrowserApp
from pist.ui.records.background import BackgroundRecordSync
from pist.ui.records.editor import RecordEditorScreen
from pist.ui.records.sync import RecordConflictScreen, RecordOfflineScreen


@pytest.mark.parametrize('cancel_waiter', (False, True))
def test_cancelled_worker_wait_result_is_owned_after_waiter_cancellation(
    cancel_waiter: bool,
) -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        errors: list[str] = []
        loop.set_exception_handler(lambda loop, context: errors.append(str(context['message'])))
        entered = asyncio.Event()

        async def submit(record: MapRecord) -> bool:
            entered.set()
            await asyncio.Event().wait()
            return True

        try:
            app: App[None] = App()
            async with app.run_test() as pilot:
                queue = BackgroundRecordSync(app, submit)
                queue.enqueue(MapRecord(created_at=datetime.now(UTC), local_id=1, map_name='Test'))
                await entered.wait()
                waiter = asyncio.create_task(queue.wait())
                await pilot.pause()
                if cancel_waiter:
                    waiter.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await waiter
                    assert queue.pending == 1
                app.workers.cancel_group(app, 'record-sync')
                if not cancel_waiter:
                    with pytest.raises(WorkerCancelled):
                        await waiter
                await pilot.pause()
                assert queue.pending == 0
                gc.collect()
                await pilot.pause()
            assert errors == []
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())


class DelayedSheet(FakeSheet):
    """Hold the first write while the player edits the second record."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.started: list[str | None] = []

    async def write_record(
        self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
    ) -> SmartSheetRecord:
        self.started.append(record_id)
        if len(self.started) == 1:
            self.entered.set()
            await self.release.wait()
        return await super().write_record(snapshot, values, record_id=record_id)


def editor(record: MapRecord, fields: tuple[ManualRecordField, ...]) -> RecordEditorScreen:
    return RecordEditorScreen(record, manual_fields=fields)


def linked_records(
    store: RecordStore, client: FakeSheet, *, first_unbound: bool = False
) -> tuple[MapRecord, ...]:
    records = []
    for i, name in enumerate(('First', 'Second')):
        record = store.load(
            store.save(
                MapRecord(
                    created_at=datetime.now(UTC),
                    map_name=name,
                    mod_metadata_name='Example',
                    status='通关',
                    time_played=Duration.from_dotnet_ticks(600000000),
                    notes='Before',
                )
            )
        )
        assert record.local_id is not None
        if first_unbound and i == 0:
            records.append(record)
            continue
        values = encode_record_values(record, client.fields)
        client.rows[str(i)] = values
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id, file_id='file', sheet_id='main', record_id=str(i)
            ),
            values,
            submitted=record,
            confirmed_at=datetime.now(UTC),
        )
        records.append(record)
    return tuple(records)


@pytest.mark.parametrize('action', ('cancel', 'equal', 'update'))
def test_editor_never_reads_before_save_and_saved_edits_use_one_check(
    tmp_path: Path, action: str
) -> None:
    async def run() -> None:
        store = RecordStore(tmp_path / 'records.sqlite3')
        client = FakeSheet()
        first, _ = linked_records(store, client)
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            worker = app.run_worker(app.controller.edit_record(first, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert client.reads == 0
            edited = first.model_copy(update={'notes': 'Edited'}) if action == 'update' else first
            app.screen.dismiss(None if action == 'cancel' else edited)
            await worker.wait()
            await app.controller.background_sync.wait()
            assert client.reads == {'cancel': 0, 'equal': 1, 'update': 1}[action]
            assert len(client.writes) == int(action == 'update')
            assert store.load(1).notes == ('Edited' if action == 'update' else 'Before')
            assert store.sync_state(1) is RecordSyncState.SYNCED

    asyncio.run(run())


@pytest.mark.parametrize('remote_schema_changed', (False, True))
def test_editor_reuses_persisted_options_but_save_checks_current_schema(
    tmp_path: Path, remote_schema_changed: bool
) -> None:
    async def run() -> None:
        store = RecordStore(tmp_path / 'records.sqlite3')
        client = FakeSheet()
        first, _ = linked_records(store, client)
        assert store.latest_sheet_fields() is None
        status = next(field for field in client.fields.fields if field.field_title == '状态')
        status.property_single_select = SmartSheetSelect.model_validate(
            {'options': [{'text': '通关'}, {'text': '进行中'}]}
        )
        store.observe_sheet(await client.read_main_sheet('source'), read_at=datetime.now(UTC))
        assert store.latest_sheet_fields() == client.fields
        if remote_schema_changed:
            status.field_type = 1
        client.reads = 0
        app = RecordBrowserApp(store, client=client, source='source')

        def cached_editor(
            record: MapRecord, fields: tuple[ManualRecordField, ...]
        ) -> RecordEditorScreen:
            assert next(field for field in fields if field.title == '状态').options == (
                '通关',
                '进行中',
            )
            return editor(record, fields)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            worker = app.run_worker(app.controller.edit_record(first, cached_editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert client.reads == 0
            app.screen.dismiss(first.model_copy(update={'notes': 'Saved'}))
            await worker.wait()
            await pilot.pause()
            if remote_schema_changed:
                assert isinstance(app.screen, RecordOfflineScreen)
                app.screen.dismiss(None)
            await app.controller.background_sync.wait()
            assert store.load(1).notes == 'Saved'
            assert len(client.writes) == int(not remote_schema_changed)
            assert store.sync_state(1) is (
                RecordSyncState.PENDING if remote_schema_changed else RecordSyncState.SYNCED
            )

    asyncio.run(run())


@pytest.mark.parametrize('new_record', (False, True))
def test_next_edit_does_not_cancel_save_and_queue_is_serial(
    tmp_path: Path, new_record: bool
) -> None:
    async def run() -> None:
        store = RecordStore(tmp_path / 'records.sqlite3')
        client = DelayedSheet()
        first, second = linked_records(store, client, first_unbound=new_record)
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            worker = app.run_worker(
                app.controller.edit_record(first, editor), group='record-preview', exclusive=True
            )
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.dismiss(first.model_copy(update={'notes': 'First edit'}))
            await worker.wait()
            await asyncio.wait_for(client.entered.wait(), timeout=5)
            assert store.load(1).notes == 'First edit'
            assert store.sync_state(1) is RecordSyncState.PENDING
            assert len(store.sheet_add_attempts(1)) == int(new_record)
            current_screen = app.screen
            blocked = app.run_worker(app.controller.edit_record(store.load(1), editor))
            await blocked.wait()
            assert app.screen is current_screen
            app.run_worker(
                app.controller.edit_record(second, editor), group='record-preview', exclusive=True
            )
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert client.started == [None if new_record else '0']
            await app.action_quit()
            assert app.is_running
            app.screen.dismiss(second.model_copy(update={'notes': 'Second edit'}))
            await pilot.pause()
            assert store.load(2).notes == 'Second edit'
            assert app.controller.background_sync.pending == 2
            assert client.started == [None if new_record else '0']
            # An explicit sync must wait, then reload the latest local content;
            # the old snapshot supplied here must not overwrite the saved edit.
            app.run_worker(app.controller.sync_record(first))
            client.release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert client.started == [None if new_record else '0', '1']
            assert all(item.sync_state is RecordSyncState.SYNCED for item in store.list_records())
            assert not app.controller.background_sync.pending
            assert all(not store.sheet_add_attempts(id_) for id_ in (1, 2))
            assert all(
                cell[-2].plain == '无本地变更'
                for cell in (app.screen.query_one(DataTable).get_row_at(i) for i in range(2))
            )

    asyncio.run(run())


def test_forced_queue_cancellation_retains_uncertain_add_and_reports_warning(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        store = RecordStore(tmp_path / 'records.sqlite3')
        client = DelayedSheet()
        first, _ = linked_records(store, client, first_unbound=True)
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            worker = app.run_worker(app.controller.edit_record(first, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.dismiss(first.model_copy(update={'notes': 'Saved edit'}))
            await worker.wait()
            await asyncio.wait_for(client.entered.wait(), timeout=5)
            app.workers.cancel_group(app, 'record-sync')
            await pilot.pause()
            assert store.load(1).notes == 'Saved edit'
            assert store.sync_state(1) is RecordSyncState.PENDING
            assert len(store.sheet_add_attempts(1)) == 1
            assert not store.sheet_links(1)
            assert app.diagnostic_count == 1
            assert not app.controller.background_sync.pending

    asyncio.run(run())


def test_cancelled_sync_waiter_does_not_cancel_background_write(tmp_path: Path) -> None:
    async def run() -> None:
        store = RecordStore(tmp_path / 'records.sqlite3')
        client = DelayedSheet()
        first, _ = linked_records(store, client)
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            worker = app.run_worker(app.controller.edit_record(first, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.dismiss(first.model_copy(update={'notes': 'Saved edit'}))
            await worker.wait()
            await asyncio.wait_for(client.entered.wait(), timeout=5)
            waiter = app.run_worker(app.controller.sync_record(first))
            await pilot.pause()
            waiter.cancel()
            await pilot.pause()
            assert app.controller.background_sync.pending == 1
            assert not client.writes
            client.release.set()
            await app.workers.wait_for_complete()
            assert store.load(1).notes == 'Saved edit'
            assert store.sync_state(1) is RecordSyncState.SYNCED
            assert len(client.writes) == 1

    asyncio.run(run())


def test_background_conflict_waits_for_current_editor(tmp_path: Path) -> None:
    class ChangingSheet(FakeSheet):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.hold = False

        async def read_main_sheet(
            self, source: str, *, record_ids: tuple[str, ...] | None = None
        ) -> MainSheetSnapshot:
            if self.hold:
                self.hold = False
                self.entered.set()
                await self.release.wait()
            return await super().read_main_sheet(source, record_ids=record_ids)

    async def run() -> None:
        store = RecordStore(tmp_path / 'records.sqlite3')
        client = ChangingSheet()
        first, second = linked_records(store, client)
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            worker = app.run_worker(app.controller.edit_record(first, editor))
            await pilot.pause()
            client.hold = True
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.dismiss(first.model_copy(update={'notes': 'First edit'}))
            await worker.wait()
            await asyncio.wait_for(client.entered.wait(), timeout=5)
            app.run_worker(app.controller.edit_record(second, editor))
            await pilot.pause()
            current_editor = app.screen
            assert isinstance(current_editor, RecordEditorScreen)
            client.rows['0']['备注'] = [{'text': 'Remote edit'}]
            client.release.set()
            await pilot.pause()
            assert app.screen is current_editor
            assert client.writes == []
            current_editor.dismiss(None)
            await pilot.pause()
            assert isinstance(app.screen, RecordConflictScreen)
            app.screen.dismiss(None)
            await app.workers.wait_for_complete()
            assert client.writes == []
            assert store.load(1).notes == 'First edit'
            assert store.sync_state(1) is RecordSyncState.PENDING

    asyncio.run(run())
