import asyncio
import json
from datetime import UTC, datetime
from typing import Self, cast

import pytest
from aiohttp import ClientSession

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


def test_submit_update_reads_all_record_pages(monkeypatch) -> None:
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
        return RecordsResult.model_validate(
            {
                'records': [
                    {'recordID': f'r{index}'} for index in range(offset, min(offset + 100, 101))
                ]
            }
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
                {'fieldID': f'field-{index}', 'fieldTitle': title, 'fieldType': field_type}
                for index, (title, field_type) in enumerate(
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


def test_inspect_uses_one_aiohttp_session_for_all_smart_sheet_operations(monkeypatch) -> None:
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
