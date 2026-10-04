import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Self, cast

import pytest
from aiohttp import ClientSession, ClientTimeout

from pist.records import MapRecord
from pist.secrets import CredentialStore, TencentDocsCredentials
from pist.smartsheet import (
    API_BASE_URL,
    DRIVE_API_URL,
    FieldsResult,
    JsonObject,
    PageOptions,
    RecordsResult,
    SmartSheetOperation,
    SmartSheetSourceValue,
    TencentSmartSheetClient,
    _encode_field_value,
    _record_values,
)


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
        self.posts: list[tuple[str, JsonObject]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def get(self, url: str, *, params: dict[str, object] | None = None) -> _Response:
        if url == DRIVE_API_URL:
            assert params == {'type': 2, 'value': 'encoded-id'}
            return _Response({'ret': 0, 'data': {'fileId': '$file'}})
        assert url == 'files/$file/sheets'
        return _Response({'ret': 0, 'data': {'getSheet': [{'sheetID': 'sheet', 'title': '初见'}]}})

    def post(self, url: str, *, json: JsonObject) -> _Response:
        self.posts.append((url, json))
        operation = next(iter(json))
        result = {
            SmartSheetOperation.GET_VIEWS: {'total': 1},
            SmartSheetOperation.GET_FIELDS: {
                'fields': [{'fieldID': 'field', 'fieldTitle': '地图名', 'fieldType': 1}],
                'total': 1,
            },
            SmartSheetOperation.GET_RECORDS: {'records': [], 'total': 0},
        }[SmartSheetOperation(operation)]
        return _Response({'ret': 0, 'data': {operation: result}})


class _Store:
    async def load_credentials(self) -> TencentDocsCredentials:
        return TencentDocsCredentials(client_id='id', access_token='token', open_id='open-id')


@pytest.mark.parametrize('limit', (0, 101))
def test_page_options_rejects_limit_outside_the_api_range(limit: int) -> None:
    with pytest.raises(ValueError):
        PageOptions(offset=0, limit=limit)


def test_record_pagination_reads_all_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    calls: list[PageOptions] = []

    async def get_records(
        _session: object,
        _endpoint: str,
        operation: SmartSheetOperation,
        options: PageOptions,
        result_type: type[RecordsResult],
    ) -> RecordsResult:
        assert operation is SmartSheetOperation.GET_RECORDS
        assert result_type is RecordsResult
        calls.append(options)
        offset = options.offset
        await asyncio.sleep(0)
        return RecordsResult.model_validate(
            {'records': [{'recordID': f'r{i}'} for i in range(offset, min(offset + 100, 101))]}
        )

    monkeypatch.setattr(client, '_post_operation', get_records)

    records = asyncio.run(client._all_records(cast(ClientSession, object()), 'sheet'))

    assert calls == [PageOptions(offset=0, limit=100), PageOptions(offset=100, limit=100)]
    assert len(records.records) == 101


@pytest.mark.parametrize('status', ('进行中', '未开始'))
def test_incomplete_record_omits_map_data_from_main_table(status: str) -> None:
    record = MapRecord(
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        mod_metadata_name='Example',
        map_name='Map',
        map_file='Maps/Example/Map.bin',
        sid='Example/Map',
        side='A',
        save_slot=0,
        record_values={
            '主表': {
                '状态': status,
                '任意规则统计字段': 3,
                '评分': 8,
            }
        },
    )
    fields = FieldsResult.model_validate(
        {
            'fields': [
                {'fieldID': f'field-{i}', 'fieldTitle': title, 'fieldType': field_type}
                for i, (title, field_type) in enumerate(
                    (
                        ('Mod元数据名', 1),
                        ('地图名', 1),
                        ('状态', 17),
                        ('任意规则统计字段', 2),
                        ('评分', 2),
                    )
                )
            ]
        }
    )

    values = _record_values(record, fields)

    assert values == {
        'Mod元数据名': [{'type': 'text', 'text': 'Example'}],
        '地图名': [{'type': 'text', 'text': 'Map'}],
        '状态': [{'text': status}],
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
        _encode_field_value(cast(SmartSheetSourceValue, value), field_type)


def test_inspect_uses_one_aiohttp_session_for_all_smart_sheet_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()

    def session_factory(*, base_url: str, headers: dict[str, str], timeout: object) -> _Session:
        assert base_url == API_BASE_URL
        assert headers['Access-Token'] == 'token'
        assert timeout is not None
        return session

    monkeypatch.setattr('pist.smartsheet.ClientSession', session_factory)

    report = asyncio.run(
        TencentSmartSheetClient(cast(CredentialStore, _Store())).inspect(
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


def _wire_payload(payload: JsonObject) -> JsonObject:
    """Capture the JSON representation sent over HTTP, not Python-only containers."""
    return cast(JsonObject, json.loads(json.dumps(payload)))


class _SyncSession(_Session):
    """Serve synthetic HTTP responses without replacing the synchronization logic."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[JsonObject] = []
        self.sheets: list[JsonObject] = [
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
        self.responses: dict[SmartSheetOperation, JsonObject] = {}
        self.closed = False

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    def get(self, url: str, *, params: dict[str, object] | None = None) -> _Response:
        if url == DRIVE_API_URL:
            return super().get(url, params=params)
        assert url == 'files/$file/sheets'
        return _Response({'ret': 0, 'data': {'getSheet': self.sheets}})

    def post(self, url: str, *, json: JsonObject) -> _Response:
        assert url == 'files/$file/sheets/main'
        assert len(json) == 1
        json = _wire_payload(json)
        self.posts.append((url, json))
        operation = SmartSheetOperation(next(iter(json)))
        if operation in self.responses:
            return _Response(self.responses[operation])
        match operation:
            case SmartSheetOperation.GET_FIELDS:
                result: JsonObject = {
                    'fields': [
                        {'fieldID': f'f{i}', 'fieldTitle': title, 'fieldType': field_type}
                        for i, (title, field_type) in enumerate(self.field_types.items())
                    ]
                }
            case SmartSheetOperation.GET_RECORDS:
                page = PageOptions.model_validate(json[operation])
                result = {
                    'records': [*self.records[page.offset : page.offset + page.limit]],
                    'total': len(self.records),
                }
            case SmartSheetOperation.ADD_RECORDS:
                result = {'records': [{'recordID': 'created-id'}]}
            case SmartSheetOperation.UPDATE_RECORDS:
                result = {'records': []}
            case _:
                raise AssertionError(f'Unexpected operation: {operation}')
        return _Response({'ret': 0, 'data': {operation: result}})

    @property
    def writes(self) -> list[JsonObject]:
        return [
            payload
            for _, payload in self.posts
            if SmartSheetOperation.ADD_RECORDS in payload
            or SmartSheetOperation.UPDATE_RECORDS in payload
        ]


@pytest.fixture
def sync_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[_SyncSession]:
    session = _SyncSession()

    def session_factory(
        *, base_url: str, headers: dict[str, str], timeout: ClientTimeout
    ) -> _SyncSession:
        assert base_url == API_BASE_URL
        assert headers == {
            'Access-Token': 'token',
            'Client-Id': 'id',
            'Open-Id': 'open-id',
            'Accept': 'application/json',
        }
        assert timeout.total is not None
        return session

    monkeypatch.setattr('pist.smartsheet.ClientSession', session_factory)
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
        side='A',
        save_slot=0,
        authors=('Alice', 'Bob'),
        record_values={
            '主表': {
                '红草莓数': 3,
                '磁带': True,
                '状态': '已完成',
                '结束日期': '2026-10-02',
                '表格没有的字段': 99,
            }
        },
    )


def _remote_record(record_id: str, *, mod: str = 'Example', name: str = 'Map') -> JsonObject:
    return {
        'recordID': record_id,
        'values': {
            'Mod元数据名': [{'type': 'text', 'text': mod}],
            '地图名': [{'type': 'text', 'text': name}],
        },
    }


@pytest.mark.parametrize('file_id', ['encoded-id', '$file'])
def test_sync_record_adds_encoded_values_without_reading_existing_records(
    sync_session: _SyncSession, record_to_sync: MapRecord, file_id: str
) -> None:
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    assert asyncio.run(client.sync_record(file_id, record_to_sync, update=False)) == 'created-id'
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
                            '结束日期': '1790899200000',
                        }
                    }
                ]
            }
        }
    ]
    assert all('getRecords' not in payload for _, payload in sync_session.posts)


@pytest.mark.parametrize('match_index', [0, 100])
def test_sync_record_updates_only_the_unique_match_across_pages(
    sync_session: _SyncSession, record_to_sync: MapRecord, match_index: int
) -> None:
    sync_session.records = [_remote_record(f'r{i}', name=f'Other{i}') for i in range(101)]
    sync_session.records[match_index] = _remote_record('target-id')
    # Same map in another Mod is not a match.
    sync_session.records[1] = _remote_record('other-mod-id', mod='OtherMod')
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    assert asyncio.run(client.sync_record('$file', record_to_sync, update=True)) == 'target-id'
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


@pytest.mark.parametrize('matches', [0, 2])
def test_sync_record_refuses_missing_or_ambiguous_matches_without_writing(
    sync_session: _SyncSession, record_to_sync: MapRecord, matches: int
) -> None:
    sync_session.records = [_remote_record(f'r{i}', name=f'Other{i}') for i in range(101)]
    if matches:
        # A matching first page must not hide a second match on a later page.
        sync_session.records[0] = _remote_record('first-match')
        sync_session.records[100] = _remote_record('second-match')
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(ValueError, match='未找到' if matches == 0 else '不能确定'):
        asyncio.run(client.sync_record('$file', record_to_sync, update=True))
    assert sync_session.writes == []


def test_sync_record_refuses_a_missing_main_sheet_without_writing(
    sync_session: _SyncSession, record_to_sync: MapRecord
) -> None:
    sync_session.sheets = [{'sheetID': 'other', 'title': '其他表'}]
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(ValueError, match='sub-sheet'):
        asyncio.run(client.sync_record('$file', record_to_sync, update=False))
    assert sync_session.writes == []


def test_sync_record_refuses_incompatible_field_types_without_writing(
    sync_session: _SyncSession, record_to_sync: MapRecord
) -> None:
    sync_session.field_types['红草莓数'] = 3
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(ValueError, match='Unsupported value'):
        asyncio.run(client.sync_record('$file', record_to_sync, update=False))
    assert sync_session.writes == []


@pytest.mark.parametrize(
    'operation', [SmartSheetOperation.GET_FIELDS, SmartSheetOperation.GET_RECORDS]
)
def test_sync_record_stops_after_an_api_read_failure(
    sync_session: _SyncSession, record_to_sync: MapRecord, operation: SmartSheetOperation
) -> None:
    sync_session.responses[operation] = {'ret': 1, 'msg': 'offline failure', 'data': {}}
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(RuntimeError, match='offline failure'):
        asyncio.run(client.sync_record('$file', record_to_sync, update=True))
    assert sync_session.writes == []


@pytest.mark.parametrize('update', [False, True])
def test_sync_record_propagates_write_failure_without_retrying(
    sync_session: _SyncSession, record_to_sync: MapRecord, update: bool
) -> None:
    sync_session.records = [_remote_record('target-id')]
    operation = SmartSheetOperation.UPDATE_RECORDS if update else SmartSheetOperation.ADD_RECORDS
    sync_session.responses[operation] = {'ret': 1, 'msg': 'offline write failure', 'data': {}}
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    with pytest.raises(RuntimeError, match='offline write failure'):
        asyncio.run(client.sync_record('$file', record_to_sync, update=update))
    assert len(sync_session.writes) == 1
