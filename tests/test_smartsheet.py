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
    JsonObject,
    TencentSmartSheetClient,
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
        self.posts: list[tuple[str, dict[str, dict[str, int]]]] = []

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

    def post(self, url: str, *, json: dict[str, dict[str, int]]) -> _Response:
        self.posts.append((url, json))
        operation = next(iter(json))
        return _Response({'ret': 0, 'data': {operation: {'ok': operation}}})


class _Store:
    async def load_credentials(self) -> TencentDocsCredentials:
        return TencentDocsCredentials(client_id='id', access_token='token', open_id='open-id')


def test_submit_update_reads_all_record_pages(monkeypatch) -> None:
    client = TencentSmartSheetClient(cast(CredentialStore, _Store()))
    calls: list[dict[str, int]] = []

    async def get_records(
        _session: object, _endpoint: str, operation: str, options: dict[str, int]
    ) -> JsonObject:
        assert operation == 'getRecords'
        calls.append(options)
        offset = options['offset']
        return {
            'records': [
                {'recordID': f'r{index}'} for index in range(offset, min(offset + 100, 101))
            ]
        }

    monkeypatch.setattr(client, '_post_operation', get_records)

    records = asyncio.run(client._all_records(cast(ClientSession, object()), 'sheet'))

    assert calls == [{'offset': 0, 'limit': 100}, {'offset': 100, 'limit': 100}]
    assert isinstance(records_value := records['records'], list)
    assert len(records_value) == 101


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
    fields: JsonObject = {
        'fields': [
            {'fieldTitle': title, 'fieldType': field_type}
            for title, field_type in (
                ('Mod元数据名', 1),
                ('地图名', 1),
                ('状态', 17),
                ('任意规则统计字段', 2),
                ('评分', 2),
            )
        ]
    }

    values = _record_values(record, fields)

    assert values == {
        'Mod元数据名': [{'type': 'text', 'text': 'Example'}],
        '地图名': [{'type': 'text', 'text': 'Map'}],
        '状态': [{'text': status}],
        '评分': 8,
    }


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
    assert {operation for _, payload in session.posts for operation in payload} == {
        'getViews',
        'getFields',
        'getRecords',
    }
    assert all(url == 'files/$file/sheets/sheet' for url, _ in session.posts)
