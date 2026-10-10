"""Enforce full-sheet read budgets and safe deferred verification for batch sync."""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sheet_factory import FakeSheet, make_sheet_fields

from pist.records.batch import UPDATE_BATCH_SIZE, RecordQueuedUpdate, RecordSyncBatch
from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.records.sync import (
    RecordSyncCheck,
    RecordSyncConflict,
    RecordSyncResult,
    RecordSyncService,
)
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import MainSheetSnapshot, RecordWrite

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def linked_records(store: RecordStore, client: FakeSheet, n_records: int) -> list[MapRecord]:
    result = []
    for index in range(n_records):
        record = store.load(
            store.save(
                MapRecord(
                    created_at=NOW,
                    map_name=f'Map {index}',
                    mod_metadata_name='Mod',
                    status='进行中',
                    notes='Before',
                )
            )
        )
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
        result.append(record)
    return result


def test_166_equal_records_share_one_full_sheet_read(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = linked_records(store, client, 166)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            result = await batch.save(record, await batch.check(record))
            assert isinstance(result, RecordSyncResult) and result.synced
        await batch.finish()

    asyncio.run(run())
    assert client.reads == 1 and client.writes == []
    assert all(entry.sync_state is RecordSyncState.SYNCED for entry in store.list_records())


def test_converged_batch_refreshes_old_baselines_without_writing(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = linked_records(store, client, 3)
    current = [record.model_copy(update={'notes': 'Same'}) for record in records]
    for record in current:
        store.save(record)
    for values in client.rows.values():
        values['备注'] = [{'text': 'Same'}]

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in current:
            check = await batch.check(record)
            assert check.differences == ()
            result = await batch.save(record, check)
            assert isinstance(result, RecordSyncResult) and result.synced
        assert await batch.finish()

    asyncio.run(run())
    assert client.reads == 1 and client.writes == [] and client.update_batches == []
    for index, record in enumerate(current):
        assert record.local_id is not None
        assert store.sheet_links(record.local_id)[0].confirmed_values == client.rows[str(index)]
        assert store.sync_state(record.local_id) is RecordSyncState.SYNCED


def test_multiple_writes_share_one_final_readback(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = linked_records(store, client, 3)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        result = await batch.save(records[0], await batch.check(records[0]))
        assert isinstance(result, RecordSyncResult) and result.synced
        for record in records[1:]:
            changed = record.model_copy(update={'notes': 'After'})
            store.save(changed)
            result = await batch.save(changed, await batch.check(changed))
            assert isinstance(result, RecordQueuedUpdate)
            assert changed.local_id is not None
            assert store.sync_state(changed.local_id) is RecordSyncState.PENDING
        assert client.reads == 1 and client.writes == []
        await batch.finish()
        assert client.reads == 3 and len(client.update_batches) == 1

    asyncio.run(run())
    assert [id_ for id_, _ in client.writes] == ['1', '2']
    assert all(entry.sync_state is RecordSyncState.SYNCED for entry in store.list_records())


@pytest.mark.parametrize('failure', ('read', 'mismatch', 'deleted', 'schema'))
def test_final_readback_failure_preserves_pending_records(tmp_path: Path, failure: str) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')

    class FailingReadbackSheet(FakeSheet):
        async def update_records(
            self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
        ) -> None:
            await super().update_records(snapshot, records)
            if failure == 'read':
                self.read_error = True
            elif failure == 'mismatch':
                self.rows['0']['备注'] = [{'text': 'Changed again'}]
            elif failure == 'deleted':
                del self.rows['0']
            else:
                self.fields.fields.pop(0)

    client = FailingReadbackSheet()
    records = linked_records(store, client, 2)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            changed = record.model_copy(update={'notes': 'After'})
            await batch.save(changed, await batch.check(changed))
        with pytest.raises((OSError, ValueError)):
            await batch.finish()
        assert records[0].local_id is not None and records[1].local_id is not None
        assert store.sync_state(records[0].local_id) is RecordSyncState.PENDING
        assert store.sync_state(records[1].local_id) is (
            RecordSyncState.PENDING if failure in ('read', 'schema') else RecordSyncState.SYNCED
        )
        assert len(client.writes) == 2

    asyncio.run(run())


def test_write_rechecks_remote_and_dialog_invalidates_snapshot(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    record = linked_records(store, client, 1)[0]

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        check = await batch.check(record)
        client.rows['0']['备注'] = [{'text': 'Remote'}]
        await batch.save(record.model_copy(update={'notes': 'Local'}), check)
        with pytest.raises(RecordSyncConflict):
            await batch.finish()
        assert client.writes == []

        async def adopt(record: MapRecord, check: RecordSyncCheck) -> MapRecord:
            return batch.service.use_remote(record, check)

        await batch.finish(adopt)
        assert client.writes == [] and client.reads == 4

    asyncio.run(run())


def test_166_changed_rows_use_two_update_requests(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = linked_records(store, client, 166)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            changed = record.model_copy(update={'notes': 'After'})
            await batch.save(changed, await batch.check(changed))
        assert client.reads == 1 and client.writes == []
        assert await batch.finish()

    asyncio.run(run())
    assert [len(items) for items in client.update_batches] == [UPDATE_BATCH_SIZE, 66]
    assert client.reads == 5
    assert client.read_selections == [
        None,
        tuple(str(i) for i in range(100)),
        tuple(str(i) for i in range(100)),
        tuple(str(i) for i in range(100, 166)),
        tuple(str(i) for i in range(100, 166)),
    ]
    assert all(entry.sync_state is RecordSyncState.SYNCED for entry in store.list_records())


@pytest.mark.parametrize('failure', ('write', 'readback'))
def test_later_batch_failure_preserves_first_batch_confirmation(
    tmp_path: Path, failure: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')

    class SecondBatchFails(FakeSheet):
        async def update_records(
            self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
        ) -> None:
            if self.update_batches:
                if failure == 'write':
                    self.write_error = True
                else:
                    self.read_error = True
            await super().update_records(snapshot, records)

    client = SecondBatchFails()
    records = linked_records(store, client, UPDATE_BATCH_SIZE + 1)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            changed = record.model_copy(update={'notes': 'After'})
            await batch.save(changed, await batch.check(changed))
        with pytest.raises(OSError):
            await batch.finish()

    asyncio.run(run())
    states = [entry.sync_state for entry in store.list_records()]
    assert states[:UPDATE_BATCH_SIZE] == [RecordSyncState.SYNCED] * UPDATE_BATCH_SIZE
    assert states[-1] is RecordSyncState.PENDING


def test_real_date_and_blank_checkbox_shapes_do_not_trigger_writes(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.fields = make_sheet_fields(
        ('地图名', 1),
        ('Mod元数据名', 1),
        ('状态', 17),
        ('备注', 1),
        ('起始日期', 4),
        ('结束日期', 4),
        ('更新时间', 4),
        ('磁带', 3),
    )
    records = linked_records(store, client, 166)
    for i, record in enumerate(records):
        for title in ('起始日期', '结束日期', '更新时间'):
            client.rows[str(i)][title] = int(
                datetime(2026, 10, 10, 16).astimezone().timestamp() * 1000
            )
        client.rows[str(i)].pop('磁带')
        saved = store.load(
            store.save(
                record.model_copy(
                    update={
                        'started_at': date(2026, 10, 10),
                        'finished_at': date(2026, 10, 10),
                        'mod_updated_at': date(2026, 10, 10),
                        'cassette': False,
                    }
                )
            )
        )
        assert saved.local_id is not None
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=saved.local_id, file_id='file', sheet_id='main', record_id=str(i)
            ),
            client.rows[str(i)],
            submitted=saved,
            confirmed_at=NOW,
        )
        store.save(saved)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for entry in store.list_records():
            check = await batch.check(entry.record)
            assert check.differences == ()
            result = await batch.save(entry.record, check)
            assert isinstance(result, RecordSyncResult) and result.synced
        await batch.finish()

    asyncio.run(run())
    assert client.reads == 1 and client.writes == []


def test_cancelled_prewrite_conflict_never_submits_staged_rows(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = linked_records(store, client, 2)

    async def cancel(record: MapRecord, check: RecordSyncCheck) -> None:
        return None

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            await batch.save(
                record.model_copy(update={'notes': 'Local'}), await batch.check(record)
            )
        client.rows['1']['备注'] = [{'text': 'Remote'}]
        assert not await batch.finish(cancel)

    asyncio.run(run())
    assert client.writes == []
    assert all(entry.sync_state is RecordSyncState.PENDING for entry in store.list_records())


def test_failed_batch_is_not_retried_and_leaves_local_data_pending(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    records = linked_records(store, client, 2)

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            await batch.save(
                record.model_copy(update={'notes': 'Local'}), await batch.check(record)
            )
        client.write_error = True
        with pytest.raises(OSError):
            await batch.finish()
        await batch.finish()

    asyncio.run(run())
    assert len(client.update_batches) == 1
    assert all(entry.sync_state is RecordSyncState.PENDING for entry in store.list_records())
