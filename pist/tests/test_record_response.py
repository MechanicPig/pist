"""Successful write responses establish baselines without a record readback."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aiohttp import ClientConnectionError, ClientResponseError, RequestInfo, ServerDisconnectedError
from multidict import CIMultiDict, CIMultiDictProxy
from sheet_factory import FakeSheet, make_sheet_fields
from yarl import URL

from pist.records.batch import RecordSyncBatch
from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.records.sync import RecordSyncService
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import (
    FieldsResult,
    JsonObject,
    MainSheetSnapshot,
    RecordWrite,
    SmartSheetRecord,
    SmartSheetSelect,
)
from pist.smartsheet.response import needs_option_refresh


class OptionResponseSheet(FakeSheet):
    """Return real select-ID shapes, including options created during a write."""

    def __init__(self, *, new_options: bool, failure: str = '') -> None:
        super().__init__()
        self.new_options = new_options
        self.failure = failure
        self.fields = make_sheet_fields(
            ('Mod元数据名', 1), ('地图名', 1), ('状态', 17), ('标签', 9), ('备注', 1)
        )
        self.set_options(include_new=not new_options)

    def set_options(self, *, include_new: bool) -> None:
        status = next(field for field in self.fields.fields if field.field_title == '状态')
        status.property_single_select = SmartSheetSelect.model_validate(
            {'options': [{'id': 'status-id', 'text': '进行中'}]}
        )
        tags = next(field for field in self.fields.fields if field.field_title == '标签')
        tags.property_select = SmartSheetSelect.model_validate(
            {
                'options': [{'id': 'tag-a', 'text': 'New A'}, {'id': 'tag-b', 'text': 'New B'}]
                if include_new
                else []
            }
        )

    async def write_record(
        self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
    ) -> SmartSheetRecord:
        returned = await super().write_record(snapshot, values, record_id=record_id)
        returned.values['状态'] = ['status-id']
        returned.values['标签'] = ['tag-a', 'tag-b']
        if self.new_options:
            self.set_options(include_new=self.failure != 'unknown')
        if self.failure == 'refresh':
            self.read_error = True
        elif self.failure == 'schema':
            self.fields.fields.pop()
        elif self.failure == 'missing-cell':
            returned.values.pop('备注')
        elif self.failure == 'mismatch':
            returned.values['备注'] = [{'text': 'Unexpected'}]
        return returned


@pytest.mark.parametrize(
    ('returned', 'submitted', 'expected'),
    (
        ({'标签': ['tag-a', 'tag-b']}, {'标签': []}, True),
        ({'状态': ['status-id']}, {'状态': []}, False),
        ({'标签': ['tag-a'], '状态': ['status-id']}, {'状态': []}, False),
        ({'标签': ['tag-a']}, {'标签': [], '备注': 'missing'}, False),
        ({'标签': [{'text': 'New A'}]}, {'标签': []}, False),
        ({'标签': []}, {'标签': []}, False),
    ),
)
def test_option_refresh_only_checks_complete_submitted_select_cells(
    returned: JsonObject, submitted: JsonObject, expected: bool
) -> None:
    client = OptionResponseSheet(new_options=True)
    assert needs_option_refresh(returned, submitted, client.fields) is expected


def setup_record(store: RecordStore, client: FakeSheet, name: str, *, linked: bool) -> MapRecord:
    record = store.load(
        store.save(
            MapRecord(
                created_at=datetime.now(UTC),
                mod_metadata_name='Synthetic',
                map_name=name,
                status='进行中',
                notes='Before',
                tags=('New A', 'New B'),
            )
        )
    )
    assert record.local_id is not None
    if linked:
        target = str(record.local_id)
        values = encode_record_values(record, client.fields)
        values['只读附加字段'] = 'Previously observed'
        client.rows[target] = values
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id, file_id='file', sheet_id='main', record_id=target
            ),
            values,
            submitted=record,
            confirmed_at=datetime.now(UTC),
        )
    return record.model_copy(update={'notes': 'After'})


@pytest.mark.parametrize('linked', (False, True))
@pytest.mark.parametrize('new_options', (False, True))
def test_response_select_ids_are_normalized_with_at_most_one_schema_refresh(
    tmp_path: Path, linked: bool, new_options: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = OptionResponseSheet(new_options=new_options)
    record = setup_record(store, client, 'Map', linked=linked)
    result = asyncio.run(RecordSyncService(store, client, 'source').save(record))
    assert result.synced, result.error
    assert client.reads == 1 and client.field_reads == int(new_options)
    assert len(client.writes) == 1
    assert record.local_id is not None
    confirmed = store.sheet_links(record.local_id)[0].confirmed_values
    assert confirmed is not None
    assert confirmed['标签'] == [{'text': 'New A'}, {'text': 'New B'}]
    assert confirmed['状态'] == [{'text': '进行中'}]
    if linked:
        assert confirmed['只读附加字段'] == 'Previously observed'
    assert not store.sheet_add_attempts(record.local_id)


@pytest.mark.parametrize('failure', ('unknown', 'refresh', 'schema', 'missing-cell', 'mismatch'))
def test_unverifiable_response_keeps_new_id_but_never_confirms_or_retries(
    tmp_path: Path, failure: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = OptionResponseSheet(new_options=True, failure=failure)
    record = setup_record(store, client, 'Map', linked=False)
    result = asyncio.run(RecordSyncService(store, client, 'source').save(record))
    assert not result.synced and result.error
    assert record.local_id is not None
    link = store.sheet_links(record.local_id)[0]
    assert link.record_id == 'created' and link.confirmed_values is None
    assert store.sync_state(record.local_id) is RecordSyncState.PENDING
    assert not store.sheet_add_attempts(record.local_id)
    assert client.field_reads <= 1 and client.reads == 1 and len(client.writes) == 1


def test_batch_unknown_options_refresh_schema_once_for_all_returned_rows(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = OptionResponseSheet(new_options=True)
    records = [setup_record(store, client, name, linked=True) for name in ('First', 'Second')]

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            await batch.save(record, await batch.check(record))
        assert await batch.finish()

    asyncio.run(run())
    assert client.field_reads == 1 and client.reads == 2
    assert len(client.update_batches) == 1
    assert all(item.sync_state is RecordSyncState.SYNCED for item in store.list_records())


@pytest.mark.parametrize(
    'failure', ('network', 'api', 'schema', 'disconnected', 'connection', 'http', 'timeout')
)
@pytest.mark.parametrize('unknown_index', (0, 1))
def test_batch_refresh_failure_preserves_independently_verifiable_rows(
    tmp_path: Path, failure: str, unknown_index: int
) -> None:
    class MixedResponseSheet(FakeSheet):
        async def update_records(
            self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
        ) -> tuple[SmartSheetRecord, ...]:
            returned = await super().update_records(snapshot, records)
            returned[unknown_index].values['状态'] = ['unknown-status']
            return returned

        async def read_fields(self, file_id: str, sheet_id: str) -> FieldsResult:
            self.field_reads += 1
            if failure == 'network':
                raise OSError('schema refresh unavailable')
            if failure == 'api':
                raise RuntimeError('schema API rejected request')
            if failure == 'disconnected':
                raise ServerDisconnectedError('schema connection closed')
            if failure == 'connection':
                raise ClientConnectionError('schema connection failed')
            if failure == 'http':
                headers: CIMultiDict[str] = CIMultiDict()
                raise ClientResponseError(
                    RequestInfo(
                        URL('https://example.invalid/fields'),
                        'POST',
                        CIMultiDictProxy(headers),
                    ),
                    (),
                    status=503,
                    message='schema service unavailable',
                )
            if failure == 'timeout':
                raise TimeoutError('schema request timed out')
            return make_sheet_fields(('Mod元数据名', 1), ('地图名', 1), ('状态', 17))

    store = RecordStore(tmp_path / 'records.sqlite3')
    client = MixedResponseSheet()
    records = [setup_record(store, client, name, linked=True) for name in ('First', 'Second')]
    old_links = []
    for record in records:
        assert record.local_id is not None
        old_links.append(store.sheet_links(record.local_id)[0])

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            await batch.save(record, await batch.check(record))
        with pytest.raises(ValueError):
            await batch.finish()
        # Dispatch is not retried even when part of its response cannot be confirmed.
        assert await batch.finish()

    asyncio.run(run())
    assert client.field_reads == 1 and client.reads == 2
    assert len(client.update_batches) == 1 and len(client.writes) == 2
    for i, record in enumerate(records):
        assert record.local_id is not None
        link = store.sheet_links(record.local_id)[0]
        if i != unknown_index and failure != 'schema':
            assert store.sync_state(record.local_id) is RecordSyncState.SYNCED
            assert link.confirmed_values is not None
            assert link.confirmed_values['备注'] == client.rows[link.record_id]['备注']
            assert link.confirmed_at != old_links[i].confirmed_at
        else:
            assert store.sync_state(record.local_id) is RecordSyncState.PENDING
            assert link == old_links[i]


@pytest.mark.parametrize('invalid', ('duplicate', 'unexpected'))
def test_invalid_batch_response_identity_cannot_confirm_or_retry(
    tmp_path: Path, invalid: str
) -> None:
    class InvalidIdentitySheet(FakeSheet):
        async def update_records(
            self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
        ) -> tuple[SmartSheetRecord, ...]:
            result = await super().update_records(snapshot, records)
            result[1].record_id = result[0].record_id if invalid == 'duplicate' else 'not-submitted'
            return result

    store = RecordStore(tmp_path / 'records.sqlite3')
    client = InvalidIdentitySheet()
    records = [setup_record(store, client, name, linked=True) for name in ('First', 'Second')]

    async def run() -> None:
        batch = RecordSyncBatch(RecordSyncService(store, client, 'source'))
        for record in records:
            await batch.save(record, await batch.check(record))
        with pytest.raises(ValueError):
            await batch.finish()
        assert await batch.finish()

    asyncio.run(run())
    assert all(item.sync_state is RecordSyncState.PENDING for item in store.list_records())
    assert len(client.update_batches) == 1
