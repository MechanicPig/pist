import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Self, cast

import pytest
from aiohttp import ClientSession, ClientTimeout

from berries.game.levels import LevelSide
from pist.credentials.store import CredentialStore, TencentDocsCredentials
from pist.records.models import MapRecord
from pist.smartsheet import client as backend
from pist.smartsheet import encoding, models


def test_unknown_remote_field_type_is_preserved_and_rejected_when_encoding() -> None:
    fields = models.FieldsResult.model_validate(
        {'fields': [{'fieldID': 'deaths', 'fieldTitle': '死亡数', 'fieldType': 999}]}
    )
    assert fields.fields[0].field_type == 999
    record = MapRecord(created_at=datetime(2026, 10, 10, tzinfo=UTC), map_name='Map', deaths=1)
    with pytest.raises(ValueError, match='field type 999'):
        encoding.encode_record_values(record, fields)


def test_read_main_sheet_reads_every_page(sync_session: _SyncSession) -> None:
    sync_session.records = [_remote_record(f'r{i}', name=f'Map{i}') for i in range(101)]
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('encoded-id'))
    assert (snapshot.file_id, snapshot.sheet_id) == ('$file', 'main')
    assert len(snapshot.records) == 101
    assert sync_session.writes == []


@pytest.mark.parametrize('n_records', (1, 100, 101))
def test_read_selected_ids_filters_every_page(sync_session: _SyncSession, n_records: int) -> None:
    sync_session.records = [_remote_record(f'r{i}', name=f'Map{i}') for i in range(200)]
    ids = tuple(f'r{i}' for i in range(n_records))
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('$file', record_ids=ids))
    assert tuple(row.record_id for row in snapshot.records) == ids
    queries = [
        payload['getRecords'] for _, payload in sync_session.posts if 'getRecords' in payload
    ]
    assert len(queries) == (n_records + 99) // 100
    assert all(isinstance(query, dict) and query['recordIDs'] == list(ids) for query in queries)
    assert snapshot.fields.fields  # Schema is still read live.


def test_filtered_read_does_not_replace_deleted_record_with_another(
    sync_session: _SyncSession,
) -> None:
    sync_session.records = [_remote_record('other', name='Same map')]
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('$file', record_ids=('deleted',)))
    assert snapshot.records == ()


@pytest.mark.parametrize('ids', ((), ('',), ('same', 'same')))
def test_filtered_read_rejects_invalid_selection_before_request(
    monkeypatch: pytest.MonkeyPatch, ids: tuple[str, ...]
) -> None:
    async def fail_session(self: backend.TencentSmartSheetClient) -> ClientSession:
        raise AssertionError('Invalid selections must not open a network session.')

    monkeypatch.setattr(backend.TencentSmartSheetClient, '_session', fail_session)
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(ValueError):
        asyncio.run(client.read_main_sheet('$file', record_ids=ids))


def test_write_record_uses_exact_id_without_name_matching(sync_session: _SyncSession) -> None:
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = models.MainSheetSnapshot(
        file_id='$file', sheet_id='main', fields=models.FieldsResult(fields=[]), records=()
    )
    assert (
        asyncio.run(
            client.write_record(snapshot, {'备注': [{'text': 'note'}]}, record_id='explicit-id')
        )
        == 'explicit-id'
    )
    assert sync_session.writes == [
        {
            'updateRecords': {
                'records': [{'values': {'备注': [{'text': 'note'}]}, 'recordID': 'explicit-id'}]
            }
        }
    ]
    assert all('getRecords' not in payload for _, payload in sync_session.posts)


def test_batch_update_uses_one_request_with_explicit_ids(sync_session: _SyncSession) -> None:
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = models.MainSheetSnapshot(
        file_id='$file', sheet_id='main', fields=models.FieldsResult(fields=[]), records=()
    )
    records = tuple(
        models.RecordWrite(record_id=f'id-{i}', values={'死亡数': i}) for i in range(100)
    )
    asyncio.run(client.update_records(snapshot, records))
    assert len(sync_session.writes) == 1
    assert sync_session.writes[0] == {
        'updateRecords': {'records': [record.model_dump(by_alias=True) for record in records]}
    }
    assert sync_session.closed


@pytest.mark.parametrize('ids', ((), (None,), ('same', 'same')))
def test_batch_update_rejects_missing_or_duplicate_ids(
    monkeypatch: pytest.MonkeyPatch, ids: tuple[str | None, ...]
) -> None:
    async def fail_session(self: backend.TencentSmartSheetClient) -> ClientSession:
        raise AssertionError('Invalid updates must not open a network session.')

    monkeypatch.setattr(backend.TencentSmartSheetClient, '_session', fail_session)
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = models.MainSheetSnapshot(
        file_id='$file', sheet_id='main', fields=models.FieldsResult(fields=[]), records=()
    )
    with pytest.raises(ValueError):
        asyncio.run(
            client.update_records(
                snapshot, tuple(models.RecordWrite(record_id=id_, values={}) for id_ in ids)
            )
        )


def test_create_requires_real_record_id(sync_session: _SyncSession) -> None:
    sync_session.responses[models.SmartSheetOperation.ADD_RECORDS] = {
        'ret': 0,
        'data': {'addRecords': {'records': []}},
    }
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = models.MainSheetSnapshot(
        file_id='$file', sheet_id='main', fields=models.FieldsResult(fields=[]), records=()
    )
    with pytest.raises(RuntimeError, match='记录 ID'):
        asyncio.run(client.write_record(snapshot, {}, record_id=None))
    assert len(sync_session.writes) == 1


@pytest.mark.parametrize('url', (None, 'https://www.bilibili.com/video/example'))
def test_sync_encodes_video_as_map_name_hyperlink(
    sync_session: _SyncSession, record_to_sync: MapRecord, url: str | None
) -> None:
    record = record_to_sync.model_copy(update={'video_url': url})
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('$file'))
    asyncio.run(
        client.write_record(
            snapshot, encoding.encode_record_values(record, snapshot.fields), record_id=None
        )
    )
    payload = sync_session.writes[0]['addRecords']
    assert isinstance(payload, dict)
    submitted = payload['records']
    assert isinstance(submitted, list) and isinstance(submitted[0], dict)
    values = submitted[0]['values']
    assert isinstance(values, dict)
    expected: models.JsonObject = (
        {'type': 'text', 'text': 'Map'}
        if url is None
        else {'type': 'url', 'text': 'Map', 'link': url}
    )
    assert values['地图名'] == [expected]


class _Response:
    def __init__(self, payload: object) -> None:
        self._content = json.dumps(payload).encode()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    async def read(self) -> bytes:
        return self._content


class _Session:
    def __init__(self) -> None:
        self.posts: list[tuple[str, models.JsonObject]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def get(self, url: str, *, params: dict[str, object] | None = None) -> _Response:
        if url == backend.DRIVE_API_URL:
            assert params == {'type': 2, 'value': 'encoded-id'}
            return _Response({'ret': 0, 'data': {'fileId': '$file'}})
        assert url == 'files/$file/sheets'
        return _Response({'ret': 0, 'data': {'getSheet': [{'sheetID': 'sheet', 'title': '初见'}]}})

    def post(self, url: str, *, json: models.JsonObject) -> _Response:
        self.posts.append((url, json))
        operation = next(iter(json))
        result = {
            models.SmartSheetOperation.GET_VIEWS: {'total': 1},
            models.SmartSheetOperation.GET_FIELDS: {
                'fields': [{'fieldID': 'field', 'fieldTitle': '地图名', 'fieldType': 1}],
                'total': 1,
            },
            models.SmartSheetOperation.GET_RECORDS: {'records': [], 'total': 0},
        }[models.SmartSheetOperation(operation)]
        return _Response({'ret': 0, 'data': {operation: result}})


class _Store:
    async def load_credentials(self) -> TencentDocsCredentials:
        return TencentDocsCredentials(client_id='id', access_token='token', open_id='open-id')


@pytest.mark.parametrize('limit', (0, 101))
def test_page_options_rejects_limit_outside_the_api_range(limit: int) -> None:
    with pytest.raises(ValueError):
        models.PageOptions(offset=0, limit=limit)


def test_record_pagination_reads_all_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    calls: list[models.PageOptions] = []

    async def get_records(
        _session: object,
        _endpoint: str,
        operation: models.SmartSheetOperation,
        options: models.PageOptions,
        result_type: type[models.RecordsResult],
    ) -> models.RecordsResult:
        assert operation is models.SmartSheetOperation.GET_RECORDS
        assert result_type is models.RecordsResult
        calls.append(options)
        offset = options.offset
        await asyncio.sleep(0)
        return models.RecordsResult.model_validate(
            {'records': [{'recordID': f'r{i}'} for i in range(offset, min(offset + 100, 101))]}
        )

    monkeypatch.setattr(client, '_post_operation', get_records)

    records = asyncio.run(client._all_records(cast(ClientSession, object()), 'sheet'))

    assert calls == [
        models.RecordReadOptions(offset=0, limit=100),
        models.RecordReadOptions(offset=100, limit=100),
    ]
    assert len(records.records) == 101


@pytest.mark.parametrize('status', ('进行中', '未开始'))
def test_incomplete_record_does_not_hide_explicitly_edited_statistics(status: str) -> None:
    record = MapRecord(
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        mod_metadata_name='Example',
        map_name='Map',
        map_file='Maps/Example/Map.bin',
        sid='Example/Map',
        side=LevelSide.A,
        save_slot=0,
        status=status,
        n_strawberries=3,
        rating=8,
    )
    fields = models.FieldsResult.model_validate(
        {
            'fields': [
                {'fieldID': f'field-{i}', 'fieldTitle': title, 'fieldType': field_type}
                for i, (title, field_type) in enumerate(
                    (
                        ('Mod元数据名', 1),
                        ('地图名', 1),
                        ('状态', 17),
                        ('红草莓数', 2),
                        ('评分', 2),
                    )
                )
            ]
        }
    )

    values = encoding.encode_record_values(record, fields)

    assert values == {
        'Mod元数据名': [{'type': 'text', 'text': 'Example'}],
        '地图名': [{'type': 'text', 'text': 'Map'}],
        '状态': [{'text': status}],
        '红草莓数': 3,
        '评分': 8,
    }


@pytest.mark.parametrize(
    ('value', 'field_type'),
    [
        (True, 2),
        ('2026-09-19T12:00:00', 2),
        (1, 4),
        (('choice', 1), 17),
    ],
)
def test_field_value_encoding_rejects_incompatible_types(value: object, field_type: int) -> None:
    with pytest.raises(ValueError, match='Unsupported value'):
        encoding._encode_field_value(cast(encoding.SmartSheetSourceValue, value), field_type)


def test_inspect_uses_one_aiohttp_session_for_all_smart_sheet_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()

    def session_factory(*, base_url: str, headers: dict[str, str], timeout: object) -> _Session:
        assert base_url == backend.API_BASE_URL
        assert headers['Access-Token'] == 'token'
        assert timeout is not None
        return session

    monkeypatch.setattr('pist.smartsheet.client.ClientSession', session_factory)

    report = asyncio.run(
        backend.TencentSmartSheetClient(cast(CredentialStore, _Store())).inspect(
            'encoded-id', record_limit=5
        )
    )

    assert report.file_id == '$file'
    assert report.sheets[0].sheet.title == '初见'
    assert report.sheets[0].fields.fields[0].field_id == 'field'
    assert {operation for _, payload in session.posts for operation in payload} == {
        'getViews',
        'getFields',
        'getRecords',
    }
    assert all(url == 'files/$file/sheets/sheet' for url, _ in session.posts)


def _wire_payload(payload: models.JsonObject) -> models.JsonObject:
    """Capture the JSON representation sent over HTTP, not Python-only containers."""
    return cast(models.JsonObject, json.loads(json.dumps(payload)))


class _SyncSession(_Session):
    """Serve synthetic HTTP responses without replacing the synchronization logic."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[models.JsonObject] = []
        self.sheets: list[models.JsonObject] = [
            {'sheetID': 'other', 'title': '其他表'},
            {'sheetID': 'main', 'title': '主表'},
        ]
        self.field_types = {
            'Mod元数据名': 1,
            '地图名': 1,
            '作者': 9,
            'Mod名': 8,
            '红草莓数': 2,
            '磁带': 3,
            '状态': 17,
            '结束日期': 4,
        }
        self.responses: dict[models.SmartSheetOperation, models.JsonObject] = {}
        self.closed = False

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    def get(self, url: str, *, params: dict[str, object] | None = None) -> _Response:
        if url == backend.DRIVE_API_URL:
            return super().get(url, params=params)
        assert url == 'files/$file/sheets'
        return _Response({'ret': 0, 'data': {'getSheet': self.sheets}})

    def post(self, url: str, *, json: models.JsonObject) -> _Response:
        assert url == 'files/$file/sheets/main'
        assert len(json) == 1
        json = _wire_payload(json)
        self.posts.append((url, json))
        operation = models.SmartSheetOperation(next(iter(json)))
        if operation in self.responses:
            return _Response(self.responses[operation])
        match operation:
            case models.SmartSheetOperation.GET_FIELDS:
                result: models.JsonObject = {
                    'fields': [
                        {'fieldID': f'f{i}', 'fieldTitle': title, 'fieldType': field_type}
                        for i, (title, field_type) in enumerate(self.field_types.items())
                    ]
                }
            case models.SmartSheetOperation.GET_RECORDS:
                page = models.RecordReadOptions.model_validate(json[operation])
                selected = [
                    row
                    for row in self.records
                    if page.record_ids is None or row.get('recordID') in page.record_ids
                ]
                result = {
                    'records': [*selected[page.offset : page.offset + page.limit]],
                    'total': len(selected),
                }
            case models.SmartSheetOperation.ADD_RECORDS:
                result = {'records': [{'recordID': 'created-id'}]}
            case models.SmartSheetOperation.UPDATE_RECORDS:
                result = {'records': []}
            case _:
                raise AssertionError(f'Unexpected operation: {operation}')
        return _Response({'ret': 0, 'data': {operation: result}})

    @property
    def writes(self) -> list[models.JsonObject]:
        return [
            payload
            for _, payload in self.posts
            if models.SmartSheetOperation.ADD_RECORDS in payload
            or models.SmartSheetOperation.UPDATE_RECORDS in payload
        ]


@pytest.fixture
def sync_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[_SyncSession]:
    session = _SyncSession()

    def session_factory(
        *, base_url: str, headers: dict[str, str], timeout: ClientTimeout
    ) -> _SyncSession:
        assert base_url == backend.API_BASE_URL
        assert headers == {
            'Access-Token': 'token',
            'Client-Id': 'id',
            'Open-Id': 'open-id',
            'Accept': 'application/json',
        }
        assert timeout.total is not None
        return session

    monkeypatch.setattr('pist.smartsheet.client.ClientSession', session_factory)
    yield session
    assert session.closed


@pytest.fixture
def record_to_sync() -> MapRecord:
    return MapRecord(
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
        mod_metadata_name='Example',
        map_name='Map',
        mod_name='Example Mod',
        mod_url='https://gamebanana.com/mods/123',
        map_file='Maps/Example/Map.bin',
        sid='Example/Map',
        side=LevelSide.A,
        save_slot=0,
        authors=('Alice', 'Bob'),
        n_strawberries=3,
        cassette=True,
        status='已完成',
        finished_at=datetime.fromisoformat('2026-10-02').replace(tzinfo=UTC).date(),
    )


def _remote_record(record_id: str, *, mod: str = 'Example', name: str = 'Map') -> models.JsonObject:
    return {
        'recordID': record_id,
        'values': {
            'Mod元数据名': [{'type': 'text', 'text': mod}],
            '地图名': [{'type': 'text', 'text': name}],
        },
    }


@pytest.mark.parametrize('file_id', ['encoded-id', '$file'])
def test_write_record_adds_encoded_values_without_name_matching(
    sync_session: _SyncSession, record_to_sync: MapRecord, file_id: str
) -> None:
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet(file_id))
    sync_session.posts.clear()
    assert (
        asyncio.run(
            client.write_record(
                snapshot,
                encoding.encode_record_values(record_to_sync, snapshot.fields),
                record_id=None,
            )
        )
        == 'created-id'
    )
    assert sync_session.writes == [
        {
            'addRecords': {
                'records': [
                    {
                        'values': {
                            'Mod元数据名': [{'type': 'text', 'text': 'Example'}],
                            '地图名': [{'type': 'text', 'text': 'Map'}],
                            '作者': [{'text': 'Alice'}, {'text': 'Bob'}],
                            'Mod名': [
                                {
                                    'type': 'url',
                                    'text': 'Example Mod',
                                    'link': 'https://gamebanana.com/mods/123',
                                }
                            ],
                            '红草莓数': 3,
                            '磁带': True,
                            '状态': [{'text': '已完成'}],
                            '结束日期': str(
                                int(datetime(2026, 10, 2).astimezone().timestamp() * 1000)
                            ),
                        }
                    }
                ]
            }
        }
    ]
    assert all('getRecords' not in payload for _, payload in sync_session.posts)


@pytest.mark.parametrize('match_index', [0, 100])
def test_read_all_pages_then_update_an_explicit_record_id(
    sync_session: _SyncSession, record_to_sync: MapRecord, match_index: int
) -> None:
    sync_session.records = [_remote_record(f'r{i}', name=f'Other{i}') for i in range(101)]
    sync_session.records[match_index] = _remote_record('target-id')
    # Same map in another Mod is not a match.
    sync_session.records[1] = _remote_record('other-mod-id', mod='OtherMod')
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('$file'))
    assert (
        asyncio.run(
            client.write_record(
                snapshot,
                encoding.encode_record_values(record_to_sync, snapshot.fields),
                record_id='target-id',
            )
        )
        == 'target-id'
    )
    assert len(sync_session.writes) == 1
    write = sync_session.writes[0]
    assert set(write) == {'updateRecords'}
    records = write['updateRecords']
    assert isinstance(records, dict)
    rows = records['records']
    assert isinstance(rows, list) and len(rows) == 1
    assert isinstance(rows[0], dict)
    assert rows[0]['recordID'] == 'target-id'
    values = rows[0]['values']
    assert isinstance(values, dict)
    assert values['红草莓数'] == 3
    assert [
        payload['getRecords'] for _, payload in sync_session.posts if 'getRecords' in payload
    ] == [
        {'offset': 0, 'limit': 100},
        {'offset': 100, 'limit': 100},
    ]


def test_read_main_sheet_refuses_a_missing_main_sheet(sync_session: _SyncSession) -> None:
    sync_session.sheets = [{'sheetID': 'other', 'title': '其他表'}]
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(ValueError):
        asyncio.run(client.read_main_sheet('$file'))
    assert sync_session.writes == []


def test_encode_refuses_incompatible_field_types_without_writing(
    sync_session: _SyncSession, record_to_sync: MapRecord
) -> None:
    sync_session.field_types['红草莓数'] = 3
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('$file'))
    with pytest.raises(ValueError, match='Unsupported value'):
        encoding.encode_record_values(record_to_sync, snapshot.fields)
    assert sync_session.writes == []


@pytest.mark.parametrize(
    'operation',
    [models.SmartSheetOperation.GET_FIELDS, models.SmartSheetOperation.GET_RECORDS],
)
def test_read_main_sheet_stops_after_an_api_read_failure(
    sync_session: _SyncSession, operation: models.SmartSheetOperation
) -> None:
    sync_session.responses[operation] = {'ret': 1, 'msg': 'offline failure', 'data': {}}
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(RuntimeError, match='offline failure'):
        asyncio.run(client.read_main_sheet('$file'))
    assert sync_session.writes == []


@pytest.mark.parametrize('update', [False, True])
def test_write_record_propagates_write_failure_without_retrying(
    sync_session: _SyncSession, record_to_sync: MapRecord, update: bool
) -> None:
    sync_session.records = [_remote_record('target-id')]
    operation = (
        models.SmartSheetOperation.UPDATE_RECORDS
        if update
        else models.SmartSheetOperation.ADD_RECORDS
    )
    sync_session.responses[operation] = {'ret': 1, 'msg': 'offline write failure', 'data': {}}
    client = backend.TencentSmartSheetClient(cast(CredentialStore, _Store()))
    snapshot = asyncio.run(client.read_main_sheet('$file'))
    with pytest.raises(RuntimeError, match='offline write failure'):
        asyncio.run(
            client.write_record(
                snapshot,
                encoding.encode_record_values(record_to_sync, snapshot.fields),
                record_id='target-id' if update else None,
            )
        )
    assert len(sync_session.writes) == 1
