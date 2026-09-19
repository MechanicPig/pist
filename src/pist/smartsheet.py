"""Asynchronous Tencent Docs Smart Sheet API client."""

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlparse

from aiohttp import ClientResponse, ClientSession, ClientTimeout
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
)

from pist.models import ExternalModel, FrozenModel
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


class TencentApiResp(ExternalModel):
    """Common OpenAPI response envelope."""

    ret: int
    msg: str | None = None
    data: JsonObject = Field(default_factory=dict)


class SmartSheetOperation(StrEnum):
    """One Smart Sheet API operation supported by this client."""

    GET_FIELDS = 'getFields'
    GET_RECORDS = 'getRecords'
    GET_VIEWS = 'getViews'
    ADD_RECORDS = 'addRecords'
    UPDATE_RECORDS = 'updateRecords'


class SmartSheetOperationOptions(FrozenModel):
    """Validated parameters serializable beneath one dynamic API operation key."""


class PageOptions(SmartSheetOperationOptions):
    """Pagination parameters for a list operation."""

    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class RecordWrite(FrozenModel):
    """One record create or update payload."""

    model_config = ConfigDict(populate_by_name=True)

    values: JsonObject
    record_id: str | None = Field(default=None, serialization_alias='recordID')


class RecordWriteOptions(SmartSheetOperationOptions):
    """Parameters for a record create or update operation."""

    records: tuple[RecordWrite, ...]


class FileIdConversion(ExternalModel):
    """Possible fields returned by the file-ID conversion endpoint."""

    file_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices('fileID', 'fileId', 'ID', 'id'),
    )


class SmartSheet(ExternalModel):
    """A Smart Sheet sub-sheet."""

    model_config = ConfigDict(extra='allow', populate_by_name=True)

    sheet_id: str = Field(validation_alias='sheetID')
    title: str = ''
    is_visible: bool | None = Field(default=None, validation_alias='isVisible')
    type: str | None = None


class SmartSheetSelectOption(ExternalModel):
    """One visible option declared by a Smart Sheet select field."""

    text: str


class SmartSheetSingleSelect(ExternalModel):
    """Options declared for one Smart Sheet single-select field."""

    options: list[SmartSheetSelectOption] = Field(default_factory=list)


class SmartSheetField(ExternalModel):
    """One Smart Sheet field, as returned by the metadata operation."""

    field_id: str = Field(validation_alias='fieldID')
    field_title: str = Field(validation_alias='fieldTitle')
    field_type: int = Field(validation_alias='fieldType')
    property_formula: object | None = Field(default=None, validation_alias='propertyFormula')
    property_single_select: SmartSheetSingleSelect | None = Field(
        default=None,
        validation_alias='propertySingleSelect',
    )


class FieldsResult(ExternalModel):
    """Validated result of the ``getFields`` operation."""

    fields: list[SmartSheetField]
    total: int | None = None


class ViewsResult(ExternalModel):
    """Validated result of the ``getViews`` operation."""

    total: int | None = None


class SmartSheetRecord(ExternalModel):
    """One Smart Sheet record returned by a read or write operation."""

    record_id: str | None = Field(default=None, validation_alias='recordID')
    values: JsonObject = Field(default_factory=dict)


class RecordsResult(ExternalModel):
    """Validated result of a record read or write operation."""

    records: list[SmartSheetRecord]
    total: int | None = None


class GetSheetsData(ExternalModel):
    """Data payload for the Smart Sheet metadata operation."""

    sheets: list[SmartSheet] = Field(default_factory=list, validation_alias='getSheet')


class SubSheetInspection(BaseModel):
    """Validated metadata returned for one sub-sheet."""

    sheet: SmartSheet
    views: ViewsResult
    fields: FieldsResult
    records: RecordsResult


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
                session,
                endpoint,
                SmartSheetOperation.GET_FIELDS,
                PageOptions(offset=0, limit=100),
                FieldsResult,
            )
            values = _record_values(record, fields)
            if not update:
                result = await self._post_operation(
                    session,
                    endpoint,
                    SmartSheetOperation.ADD_RECORDS,
                    RecordWriteOptions(records=(RecordWrite(values=values),)),
                    RecordsResult,
                )
                return _submitted_record_id(result)
            records = await self._all_records(session, endpoint)
            matches = _matching_record_ids(records, record)
            if not matches:
                raise ValueError('未找到同 Mod 元数据名和地图名的既有记录，不能更新。')
            if len(matches) != 1:
                raise ValueError(f'找到 {len(matches)} 条同名记录，不能确定应更新哪一条。')
            result = await self._post_operation(
                session,
                endpoint,
                SmartSheetOperation.UPDATE_RECORDS,
                RecordWriteOptions(records=(RecordWrite(record_id=matches[0], values=values),)),
                RecordsResult,
            )
            return _submitted_record_id(result, fallback=matches[0])

    async def _all_records(self, session: ClientSession, endpoint: str) -> RecordsResult:
        """Read every main-table record so updates are not limited to the first page."""
        records: list[SmartSheetRecord] = []
        offset = 0
        while True:
            page = await self._post_operation(
                session,
                endpoint,
                SmartSheetOperation.GET_RECORDS,
                PageOptions(offset=offset, limit=RECORD_PAGE_SIZE),
                RecordsResult,
            )
            records.extend(page.records)
            if len(page.records) < RECORD_PAGE_SIZE:
                return RecordsResult(records=records)
            offset += len(page.records)

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
            self._post_operation(
                session,
                endpoint,
                SmartSheetOperation.GET_VIEWS,
                PageOptions(offset=0, limit=100),
                ViewsResult,
            ),
            self._post_operation(
                session,
                endpoint,
                SmartSheetOperation.GET_FIELDS,
                PageOptions(offset=0, limit=100),
                FieldsResult,
            ),
            self._post_operation(
                session,
                endpoint,
                SmartSheetOperation.GET_RECORDS,
                PageOptions(offset=0, limit=record_limit),
                RecordsResult,
            ),
        )
        return SubSheetInspection(sheet=sheet, views=views, fields=fields, records=records)

    async def _post_operation[T: BaseModel](
        self,
        session: ClientSession,
        endpoint: str,
        operation: SmartSheetOperation,
        options: SmartSheetOperationOptions,
        result_type: type[T],
    ) -> T:
        async with session.post(
            endpoint,
            json={operation: options.model_dump(by_alias=True, exclude_none=True)},
        ) as resp:
            return result_type.model_validate((await self._response_data(resp)).data.get(operation))

    @staticmethod
    async def _response_data(resp: ClientResponse) -> TencentApiResp:
        resp.raise_for_status()
        payload = TencentApiResp.model_validate_json(await resp.read())
        if payload.ret != 0:
            raise RuntimeError(f'Tencent Docs API failed (ret={payload.ret}): {payload.msg}')
        return payload


def _record_values(record: MapRecord, fields: FieldsResult) -> JsonObject:
    """Encode present local record values according to the live main-table schema."""
    types = {field.field_title: field.field_type for field in fields.fields}
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
    if field_type == 2 and type(value) is int:
        return value
    if field_type == 3 and isinstance(value, bool):
        return value
    if field_type == 4:
        if isinstance(value, str):
            value = datetime.fromisoformat(value).replace(tzinfo=UTC)
        if isinstance(value, datetime):
            return str(int(value.timestamp() * 1000))
    if (
        field_type == 8
        and isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) for item in value)
    ):
        return [{'type': 'url', 'text': value[0], 'link': value[1]}]
    if field_type in {9, 17}:
        items = (value,) if isinstance(value, str) else value
        if isinstance(items, tuple) and all(isinstance(item, str) for item in items):
            return [{'text': item} for item in items]
    raise ValueError(f'Unsupported value {value!r} for Smart Sheet field type {field_type}.')


def _matching_record_ids(records: RecordsResult, record: MapRecord) -> list[str]:
    """Return existing main-table record IDs matching the stable local-record identity."""
    matches: list[str] = []
    for remote_record in records.records:
        if remote_record.record_id is None:
            continue
        values = remote_record.values
        if (
            _field_text(values.get('Mod元数据名')) == record.mod_metadata_name
            and _field_text(values.get('地图名')) == record.map_name
        ):
            matches.append(remote_record.record_id)
    return matches


def _field_text(value: object) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        text = value[0].get('text')
        return text if isinstance(text, str) else None
    return None


def _submitted_record_id(result: RecordsResult, *, fallback: str | None = None) -> str:
    if result.records and (record_id := result.records[0].record_id) is not None:
        return record_id
    return fallback or '已提交（未返回记录 ID）'
