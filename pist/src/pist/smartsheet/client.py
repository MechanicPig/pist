"""Asynchronous Tencent Docs Smart Sheet API client."""

import asyncio
from urllib.parse import urlparse

from aiohttp import ClientResponse, ClientSession, ClientTimeout
from pydantic import BaseModel

from pist.credentials.store import CredentialStore
from pist.smartsheet import models

API_BASE_URL = 'https://docs.qq.com/openapi/smartbook/v2/'
DRIVE_API_URL = 'https://docs.qq.com/openapi/drive/v2/util/converter'
REQUEST_TIMEOUT = ClientTimeout(total=20)
RECORD_PAGE_SIZE = models.MAX_PAGE_SIZE
FIELD_PAGE_SIZE = models.MAX_PAGE_SIZE
VIEW_PAGE_SIZE = models.MAX_PAGE_SIZE
# https://docs.qq.com/open/document/app/openapi/v2/file/util/converter.html
ENCODED_ID_TO_FILE_ID = 2


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

    async def read_main_sheet(
        self, source: str, *, record_ids: tuple[str, ...] | None = None
    ) -> models.MainSheetSnapshot:
        """Read live schema and all rows, or only requested IDs, without remote writes.

        Missing requested rows remain absent, so callers can detect deletion. An empty
        selection is rejected rather than accidentally becoming an unfiltered query.
        """
        if record_ids is not None and (
            not record_ids
            or any(not id_ for id_ in record_ids)
            or len(set(record_ids)) != len(record_ids)
        ):
            raise ValueError('记录查询必须指定非空且不重复的 ID。')
        async with await self._session() as session:
            file_id = await self._resolve_file_id(session, extract_file_id(source))
            sheets = await self._get_sheets(session, file_id)
            matches = [sheet for sheet in sheets if sheet.title == models.MAIN_TABLE_TITLE]
            if len(matches) != 1:
                raise ValueError('表格必须恰好包含一个主表。')
            sheet = matches[0]
            endpoint = f'files/{file_id}/sheets/{sheet.sheet_id}'
            fields, records = await asyncio.gather(
                self._post_operation(
                    session,
                    endpoint,
                    models.SmartSheetOperation.GET_FIELDS,
                    models.PageOptions(offset=0, limit=FIELD_PAGE_SIZE),
                    models.FieldsResult,
                ),
                self._all_records(session, endpoint, record_ids=record_ids),
            )
        if fields.total is not None and fields.total > len(fields.fields):
            raise ValueError('主表字段超过本次读取范围，不能安全核对。')
        return models.MainSheetSnapshot(
            file_id=file_id,
            sheet_id=sheet.sheet_id,
            fields=fields,
            records=tuple(records.records),
        )

    async def write_record(
        self,
        snapshot: models.MainSheetSnapshot,
        values: models.JsonObject,
        *,
        record_id: str | None,
    ) -> str:
        """Write one explicit row, or create one, without matching by map name.

        The caller owns reconciliation and must recheck before calling this method.
        Writes are never retried automatically, including ambiguous network failures.
        """
        async with await self._session() as session:
            result = await self._post_operation(
                session,
                f'files/{snapshot.file_id}/sheets/{snapshot.sheet_id}',
                models.SmartSheetOperation.ADD_RECORDS
                if record_id is None
                else models.SmartSheetOperation.UPDATE_RECORDS,
                models.RecordWriteOptions(
                    records=(models.RecordWrite(record_id=record_id, values=values),)
                ),
                models.RecordsResult,
            )
        if record_id is not None:
            returned = _submitted_record_id(result, fallback=record_id)
            if returned != record_id:
                raise RuntimeError('表格写入返回了不同的记录 ID，不能确认同步成功。')
            return record_id
        if len(result.records) != 1 or not result.records[0].record_id:
            raise RuntimeError('表格可能已写入，但未返回唯一记录 ID；请核对后再尝试。')
        return result.records[0].record_id

    async def _session(self) -> ClientSession:
        """Create a credentialed session that the caller must close."""
        credentials = await self._store.load_credentials()
        return ClientSession(
            base_url=API_BASE_URL,
            headers={
                'Access-Token': credentials.access_token,
                'Client-Id': credentials.client_id,
                'Open-Id': credentials.open_id,
                'Accept': 'application/json',
            },
            timeout=REQUEST_TIMEOUT,
        )

    async def update_records(
        self, snapshot: models.MainSheetSnapshot, records: tuple[models.RecordWrite, ...]
    ) -> None:
        """Submit explicit existing rows together, without retrying ambiguous failures.

        An acknowledgement is not verification; the caller must read back every row.
        """
        ids = {record.record_id for record in records}
        if not records or None in ids or len(ids) != len(records):
            raise ValueError('批量更新必须指定非重复的现有记录 ID。')
        async with await self._session() as session:
            result = await self._post_operation(
                session,
                f'files/{snapshot.file_id}/sheets/{snapshot.sheet_id}',
                models.SmartSheetOperation.UPDATE_RECORDS,
                models.RecordWriteOptions(records=records),
                models.RecordsResult,
            )
        returned = [record.record_id for record in result.records]
        if returned and (len(set(returned)) != len(returned) or set(returned) != ids):
            raise RuntimeError('批量更新响应中的记录 ID 与提交值不一致，尚不能确认成功。')

    async def inspect(self, file_id: str, *, record_limit: int) -> models.InspectionReport:
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
        return models.InspectionReport(file_id=file_id, sheets=details)

    async def _all_records(
        self, session: ClientSession, endpoint: str, *, record_ids: tuple[str, ...] | None = None
    ) -> models.RecordsResult:
        """Read every matching record across pages, preserving the ID filter on each page."""
        records: list[models.SmartSheetRecord] = []
        offset = 0
        while True:
            page = await self._post_operation(
                session,
                endpoint,
                models.SmartSheetOperation.GET_RECORDS,
                models.RecordReadOptions(
                    offset=offset, limit=RECORD_PAGE_SIZE, record_ids=record_ids
                ),
                models.RecordsResult,
            )
            records.extend(page.records)
            if record_ids is not None and any(
                row.record_id not in record_ids for row in page.records
            ):
                raise ValueError('查询返回了未请求的记录 ID，不能安全核对。')
            if len(page.records) < RECORD_PAGE_SIZE or (
                record_ids is not None and len(records) >= len(record_ids)
            ):
                return models.RecordsResult(records=records)
            offset += len(page.records)

    async def _resolve_file_id(self, session: ClientSession, value: str) -> str:
        """Convert the encoded ID embedded in a docs.qq.com URL to an API file ID."""
        if '$' in value:
            return value
        async with session.get(
            DRIVE_API_URL,
            params={'type': ENCODED_ID_TO_FILE_ID, 'value': value},
        ) as resp:
            payload = models.FileIdConversion.model_validate((await self._response_data(resp)).data)
        if payload.file_id:
            return payload.file_id
        raise RuntimeError('Tencent Docs returned an unexpected file-ID conversion response.')

    async def _get_sheets(self, session: ClientSession, file_id: str) -> list[models.SmartSheet]:
        async with session.get(f'files/{file_id}/sheets') as resp:
            return models.GetSheetsData.model_validate(
                (await self._response_data(resp)).data
            ).sheets

    async def _inspect_sub_sheet(
        self,
        session: ClientSession,
        file_id: str,
        sheet: models.SmartSheet,
        record_limit: int,
    ) -> models.SubSheetInspection:
        endpoint = f'files/{file_id}/sheets/{sheet.sheet_id}'
        views, fields, records = await asyncio.gather(
            self._post_operation(
                session,
                endpoint,
                models.SmartSheetOperation.GET_VIEWS,
                models.PageOptions(offset=0, limit=VIEW_PAGE_SIZE),
                models.ViewsResult,
            ),
            self._post_operation(
                session,
                endpoint,
                models.SmartSheetOperation.GET_FIELDS,
                models.PageOptions(offset=0, limit=FIELD_PAGE_SIZE),
                models.FieldsResult,
            ),
            self._post_operation(
                session,
                endpoint,
                models.SmartSheetOperation.GET_RECORDS,
                models.PageOptions(offset=0, limit=record_limit),
                models.RecordsResult,
            ),
        )
        return models.SubSheetInspection(sheet=sheet, views=views, fields=fields, records=records)

    async def _post_operation[T: BaseModel](
        self,
        session: ClientSession,
        endpoint: str,
        operation: models.SmartSheetOperation,
        options: models.SmartSheetOperationOptions,
        result_type: type[T],
    ) -> T:
        async with session.post(
            endpoint,
            json={operation: options.model_dump(by_alias=True, exclude_none=True)},
        ) as resp:
            return result_type.model_validate((await self._response_data(resp)).data.get(operation))

    @staticmethod
    async def _response_data(resp: ClientResponse) -> models.TencentApiResp:
        resp.raise_for_status()
        payload = models.TencentApiResp.model_validate_json(await resp.read())
        if payload.ret != 0:
            raise RuntimeError(f'Tencent Docs API failed (ret={payload.ret}): {payload.msg}')
        return payload


def _submitted_record_id(result: models.RecordsResult, *, fallback: str) -> str:
    """Return the acknowledged update ID or the exact ID already selected."""
    if result.records and (record_id := result.records[0].record_id) is not None:
        return record_id
    return fallback
