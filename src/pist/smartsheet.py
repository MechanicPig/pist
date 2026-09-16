"""Asynchronous Tencent Docs Smart Sheet API client."""

import asyncio
from datetime import UTC, datetime
from urllib.parse import urlparse

from aiohttp import ClientResponse, ClientSession, ClientTimeout
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from pist.records import MANUAL_RECORD_FIELD_TITLES, MapRecord
from pist.secrets import CredentialStore
from pist.types import CellValue

API_BASE_URL = 'https://docs.qq.com/openapi/smartbook/v2/'
DRIVE_API_URL = 'https://docs.qq.com/openapi/drive/v2/util/converter'
REQUEST_TIMEOUT = ClientTimeout(total=20)
MAIN_TABLE_TITLE = '主表'
RECORD_PAGE_SIZE = 100
INCOMPLETE_STATUSES = frozenset({'进行中', '未开始'})

type SmartSheetSourceValue = CellValue | tuple[str, ...] | datetime
type JsonObject = dict[str, JsonValue]


_json_object_adapter = TypeAdapter(JsonObject)


def parse_json_object(value: object, *, context: str) -> JsonObject:
    """Validate a dynamically named API operation result as a JSON object."""
    try:
        return _json_object_adapter.validate_python(value)
    except ValidationError as error:
        raise TypeError(f'Tencent Docs returned an invalid {context} object.') from error


class TencentApiResp(BaseModel):
    """Common OpenAPI response envelope."""

    model_config = ConfigDict(extra='ignore')

    ret: int
    msg: str | None = None
    data: JsonObject = Field(default_factory=dict)


class FileIdConversion(BaseModel):
    """Possible fields returned by the file-ID conversion endpoint."""

    model_config = ConfigDict(extra='ignore')

    file_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices('fileID', 'fileId', 'ID', 'id'),
    )


class SmartSheet(BaseModel):
    """A Smart Sheet sub-sheet."""

    model_config = ConfigDict(extra='allow', populate_by_name=True)

    sheet_id: str = Field(validation_alias='sheetID')
    title: str = ''
    is_visible: bool | None = Field(default=None, validation_alias='isVisible')
    type: str | None = None


class SmartSheetField(BaseModel):
    """One Smart Sheet field, as returned by the metadata operation."""

    model_config = ConfigDict(extra='ignore')

    field_id: str = Field(validation_alias='fieldID')
    field_title: str = Field(validation_alias='fieldTitle')
    field_type: int = Field(validation_alias='fieldType')
    property_formula: object | None = Field(default=None, validation_alias='propertyFormula')


class GetSheetsData(BaseModel):
    """Data payload for the Smart Sheet metadata operation."""

    model_config = ConfigDict(extra='ignore')

    sheets: list[SmartSheet] = Field(default_factory=list, validation_alias='getSheet')


class SubSheetInspection(BaseModel):
    """Raw metadata returned for one sub-sheet."""

    sheet: SmartSheet
    views: JsonObject
    fields: JsonObject
    records: JsonObject


class InspectionReport(BaseModel):
    """Persisted raw output of a read-only Smart Sheet inspection."""

    file_id: str
    sheets: list[SubSheetInspection]


def extract_file_id(source: str) -> str:
    """Return a file ID from a Smart Sheet URL or accept a raw file ID."""
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        file_id = parsed.path.rstrip('/').split('/')[-1]
    else:
        file_id = source
    if not file_id:
        raise ValueError('A Smart Sheet URL or file ID is required.')
    return file_id


class TencentSmartSheetClient:
    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    async def inspect(self, file_id: str, *, record_limit: int) -> InspectionReport:
        credentials = await self._store.load_credentials()
        headers = {
            'Access-Token': credentials.access_token,
            'Client-Id': credentials.client_id,
            'Open-Id': credentials.open_id,
            'Accept': 'application/json',
        }
        async with ClientSession(
            base_url=API_BASE_URL, headers=headers, timeout=REQUEST_TIMEOUT
        ) as session:
            file_id = await self._resolve_file_id(session, file_id)
            sheets = await self._get_sheets(session, file_id)
            details = await asyncio.gather(
                *(
                    self._inspect_sub_sheet(session, file_id, sheet, record_limit)
                    for sheet in sheets
                )
            )
        return InspectionReport(file_id=file_id, sheets=details)

    async def sync_record(self, file_id: str, record: MapRecord, *, update: bool) -> str:
        """Add a local record to the main table, or update its one unambiguous match."""
        credentials = await self._store.load_credentials()
        headers = {
            'Access-Token': credentials.access_token,
            'Client-Id': credentials.client_id,
            'Open-Id': credentials.open_id,
            'Accept': 'application/json',
        }
        async with ClientSession(
            base_url=API_BASE_URL, headers=headers, timeout=REQUEST_TIMEOUT
        ) as session:
            resolved_id = await self._resolve_file_id(session, file_id)
            sheets = await self._get_sheets(session, resolved_id)
            sheet = next((sheet for sheet in sheets if sheet.title == MAIN_TABLE_TITLE), None)
            if sheet is None:
                raise ValueError(
                    f'No {MAIN_TABLE_TITLE!r} sub-sheet exists in the selected Smart Sheet.'
                )
            endpoint = f'files/{resolved_id}/sheets/{sheet.sheet_id}'
            fields = await self._post_operation(
                session, endpoint, 'getFields', {'offset': 0, 'limit': 100}
            )
            values = _record_values(record, fields)
            if not update:
                result = await self._post_write_operation(
                    session, endpoint, 'addRecords', {'records': [{'values': values}]}
                )
                return _submitted_record_id(result)
            records = await self._all_records(session, endpoint)
            matches = _matching_record_ids(records, record)
            if not matches:
                raise ValueError('未找到同 Mod 元数据名和地图名的既有记录，不能更新。')
            if len(matches) != 1:
                raise ValueError(f'找到 {len(matches)} 条同名记录，不能确定应更新哪一条。')
            result = await self._post_write_operation(
                session,
                endpoint,
                'updateRecords',
                {'records': [{'recordID': matches[0], 'values': values}]},
            )
            return _submitted_record_id(result, fallback=matches[0])

    async def _all_records(self, session: ClientSession, endpoint: str) -> JsonObject:
        """Read every main-table record so updates are not limited to the first page."""
        records: list[JsonValue] = []
        offset = 0
        while True:
            page = await self._post_operation(
                session, endpoint, 'getRecords', {'offset': offset, 'limit': RECORD_PAGE_SIZE}
            )
            page_records = page.get('records')
            if not isinstance(page_records, list):
                raise TypeError('Tencent Docs returned invalid record data.')
            records.extend(page_records)
            if len(page_records) < RECORD_PAGE_SIZE:
                result: JsonObject = {'records': records}
                return result
            offset += len(page_records)

    async def _resolve_file_id(self, session: ClientSession, value: str) -> str:
        """Convert the encoded ID embedded in a docs.qq.com URL to an API file ID."""
        if '$' in value:
            return value
        async with session.get(
            DRIVE_API_URL,
            params={'type': 2, 'value': value},
        ) as resp:
            payload = FileIdConversion.model_validate((await self._response_data(resp)).data)
        if payload.file_id:
            return payload.file_id
        raise RuntimeError('Tencent Docs returned an unexpected file-ID conversion response.')

    async def _get_sheets(self, session: ClientSession, file_id: str) -> list[SmartSheet]:
        async with session.get(f'files/{file_id}/sheets') as resp:
            return GetSheetsData.model_validate((await self._response_data(resp)).data).sheets

    async def _inspect_sub_sheet(
        self,
        session: ClientSession,
        file_id: str,
        sheet: SmartSheet,
        record_limit: int,
    ) -> SubSheetInspection:
        endpoint = f'files/{file_id}/sheets/{sheet.sheet_id}'
        views, fields, records = await asyncio.gather(
            self._post_operation(session, endpoint, 'getViews', {'offset': 0, 'limit': 100}),
            self._post_operation(session, endpoint, 'getFields', {'offset': 0, 'limit': 100}),
            self._post_operation(
                session, endpoint, 'getRecords', {'offset': 0, 'limit': record_limit}
            ),
        )
        return SubSheetInspection(sheet=sheet, views=views, fields=fields, records=records)

    async def _post_operation(
        self,
        session: ClientSession,
        endpoint: str,
        operation: str,
        options: dict[str, int],
    ) -> JsonObject:
        async with session.post(endpoint, json={operation: options}) as resp:
            return parse_json_object(
                (await self._response_data(resp)).data.get(operation),
                context=f'{operation} operation result',
            )

    async def _post_write_operation(
        self,
        session: ClientSession,
        endpoint: str,
        operation: str,
        options: JsonObject,
    ) -> JsonObject:
        async with session.post(endpoint, json={operation: options}) as resp:
            return parse_json_object(
                (await self._response_data(resp)).data.get(operation),
                context=f'{operation} operation result',
            )

    @staticmethod
    async def _response_data(resp: ClientResponse) -> TencentApiResp:
        resp.raise_for_status()
        payload = TencentApiResp.model_validate_json(await resp.read())
        if payload.ret != 0:
            raise RuntimeError(f'Tencent Docs API failed (ret={payload.ret}): {payload.msg}')
        return payload


def _record_values(record: MapRecord, fields: JsonObject) -> JsonObject:
    """Encode present local record values according to the live main-table schema."""
    raw_fields = fields.get('fields', [])
    if not isinstance(raw_fields, list):
        raise TypeError('Tencent Docs returned invalid field metadata.')
    types: dict[str, int] = {
        title: field_type
        for field in raw_fields
        if isinstance(field, dict)
        and isinstance(title := field.get('fieldTitle'), str)
        and isinstance(field_type := field.get('fieldType'), int)
    }
    source_values: dict[str, SmartSheetSourceValue] = {
        'Mod元数据名': record.mod_metadata_name,
        '地图名': record.map_name,
        '作者': record.authors,
    }
    if record.mod_name is not None and record.mod_url is not None:
        source_values['Mod名'] = (record.mod_name, record.mod_url)
    if record.mod_updated_at is not None:
        source_values['更新时间'] = record.mod_updated_at
    if record.time_played is not None:
        source_values['用时'] = record.time_played
    if record.deaths is not None:
        source_values['死亡数'] = record.deaths
    main_record_values = record.record_values.get(MAIN_TABLE_TITLE, {})
    if main_record_values.get('状态') in INCOMPLETE_STATUSES:
        source_values.update(
            {
                title: value
                for title, value in main_record_values.items()
                if title in MANUAL_RECORD_FIELD_TITLES
            }
        )
    else:
        source_values.update(main_record_values)
    values: JsonObject = {
        title: _encode_field_value(value, types[title])
        for title, value in source_values.items()
        if title in types and value not in ((), '')
    }
    return values


def _encode_field_value(value: SmartSheetSourceValue, field_type: int) -> JsonValue:
    """Encode one native value in Tencent Smart Sheet's field-value shape."""
    if field_type == 1 and isinstance(value, str):
        return [{'type': 'text', 'text': value}]
    if field_type == 2 and isinstance(value, int):
        return value
    if field_type == 3 and isinstance(value, bool):
        return value
    if field_type == 4:
        if isinstance(value, str):
            value = datetime.fromisoformat(value).replace(tzinfo=UTC)
        assert isinstance(value, datetime)
        return str(int(value.timestamp() * 1000))
    if field_type == 8 and isinstance(value, tuple) and len(value) == 2:
        return [{'type': 'url', 'text': value[0], 'link': value[1]}]
    if field_type in {9, 17}:
        items = (value,) if isinstance(value, str) else value
        assert isinstance(items, tuple)
        return [{'text': item} for item in items]
    raise ValueError(f'Unsupported value {value!r} for Smart Sheet field type {field_type}.')


def _matching_record_ids(records: JsonObject, record: MapRecord) -> list[str]:
    """Return existing main-table record IDs matching the stable local-record identity."""
    raw_records = records.get('records', [])
    if not isinstance(raw_records, list):
        raise TypeError('Tencent Docs returned invalid record data.')
    matches: list[str] = []
    for remote_record in raw_records:
        if not isinstance(remote_record, dict):
            continue
        values = remote_record.get('values')
        record_id = remote_record.get('recordID')
        if not isinstance(values, dict) or not isinstance(record_id, str):
            continue
        if (
            _field_text(values.get('Mod元数据名')) == record.mod_metadata_name
            and _field_text(values.get('地图名')) == record.map_name
        ):
            matches.append(record_id)
    return matches


def _field_text(value: object) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        text = value[0].get('text')
        return text if isinstance(text, str) else None
    return None


def _submitted_record_id(result: JsonObject, *, fallback: str | None = None) -> str:
    records = result.get('records')
    if isinstance(records, list) and records and isinstance(records[0], dict):
        record_id = records[0].get('recordID')
        if isinstance(record_id, str):
            return record_id
    return fallback or '已提交（未返回记录 ID）'
