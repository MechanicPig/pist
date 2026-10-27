"""Reconcile one local experience record with its explicitly associated remote row."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from aiohttp import ClientError
from pydantic import JsonValue

from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.fields import SheetFields, load_sheet_fields
from pist.smartsheet.models import (
    FieldsResult,
    JsonObject,
    MainSheetSnapshot,
    RecordWrite,
    SmartSheetRecord,
)
from pist.smartsheet.response import needs_option_refresh, submitted_response_values

from .models import MapRecord
from .store import RecordSheetAddAttempt, RecordSheetLink, RecordStore


class RecordSheetClient(Protocol):
    """Replaceable remote boundary for the record editing workflow."""

    async def read_main_sheet(
        self, source: str, *, record_ids: tuple[str, ...] | None = None
    ) -> MainSheetSnapshot: ...

    async def read_fields(self, file_id: str, sheet_id: str) -> FieldsResult: ...

    async def write_record(
        self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
    ) -> SmartSheetRecord: ...

    async def update_records(
        self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
    ) -> tuple[SmartSheetRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class RecordFieldDifference:
    """One supported remote cell that differs from the reconciliation baseline."""

    title: str
    baseline: JsonValue
    remote: JsonValue


@dataclass(frozen=True, slots=True)
class RecordSyncCheck:
    """A remote read and the diagnostics requiring a user decision before writing."""

    snapshot: MainSheetSnapshot
    remote: SmartSheetRecord | None
    issues: tuple[str, ...]
    differences: tuple[RecordFieldDifference, ...]


class RecordSyncConflict(ValueError):
    """A remote change requiring renewed consent, without saving or writing."""

    def __init__(self, check: RecordSyncCheck) -> None:
        super().__init__('远端数据发生变化，需要重新选择。')
        self.check = check


@dataclass(frozen=True, slots=True)
class RecordAddRecovery:
    """Unbound candidate rows matching the identity submitted by an uncertain add."""

    attempt: RecordSheetAddAttempt
    snapshot: MainSheetSnapshot
    candidates: tuple[SmartSheetRecord, ...]


class RecordAddRecoveryRequired(ValueError):
    """An uncertain add requires explicit row association or permission to retry."""

    def __init__(self, recovery: RecordAddRecovery) -> None:
        super().__init__('之前的新增结果尚未确认，不能再次新增；请核对候选行。')
        self.recovery = recovery


@dataclass(frozen=True, slots=True)
class RecordSyncResult:
    """The saved local record and whether its public data was confirmed remotely."""

    record: MapRecord
    synced: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RecordSubmission:
    """One saved public projection and its explicit remote write target."""

    record: MapRecord
    link: RecordSheetLink
    values: JsonObject


class RecordSyncService:
    """Read, reconcile and verify writes without UI dependencies or automatic retries."""

    def __init__(
        self,
        store: RecordStore,
        client: RecordSheetClient,
        source: str,
        *,
        fields: SheetFields | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.source = source
        self.fields = fields if fields is not None else load_sheet_fields()

    async def check(
        self, record: MapRecord, *, expected: RecordSyncCheck | None = None
    ) -> RecordSyncCheck:
        """Find remote changes not already reflected in the local writable cells.

        Compare against the last confirmation or user-reviewed read. An explicit association
        always wins over map names; missing associated rows never become new rows.
        """
        links = () if record.local_id is None else self.store.sheet_links(record.local_id)
        ids = (links[0].record_id,) if len(links) == 1 else None
        snapshot = await self.read_snapshot(record_ids=ids)
        return self.check_snapshot(record, snapshot, expected=expected)

    async def read_snapshot(
        self, *, record_ids: tuple[str, ...] | None = None
    ) -> MainSheetSnapshot:
        """Read live schema and selected rows; persist observations without confirming them.

        No ID filter reads the complete sheet for unbound-row matching and recovery.
        Observing a subset only refreshes returned rows, never removes other snapshots.
        """
        snapshot = await self.client.read_main_sheet(self.source, record_ids=record_ids)
        self.store.observe_sheet(snapshot, read_at=datetime.now(UTC))
        return snapshot

    def check_snapshot(
        self,
        record: MapRecord,
        snapshot: MainSheetSnapshot,
        *,
        expected: RecordSyncCheck | None = None,
    ) -> RecordSyncCheck:
        """Reconcile a supplied sheet snapshot without additional network reads."""
        issues = self.fields.issues(snapshot.fields)
        links = () if record.local_id is None else self.store.sheet_links(record.local_id)
        link = next(
            (
                link
                for link in links
                if (link.file_id, link.sheet_id) == (snapshot.file_id, snapshot.sheet_id)
            ),
            None,
        )
        if links and link is None:
            raise ValueError('此记录关联的是其他表格，不能自动写入当前表格。')
        if (
            link is not None
            and link.confirmed_values is not None
            and '编号' in link.confirmed_values
            and not any(field.field_title == '编号' for field in snapshot.fields.fields)
        ):
            issues = (*issues, '缺少已确认同步的字段：编号，不能忽略编号确认同步。')
        remote = None
        if link is not None:
            matches = [row for row in snapshot.records if row.record_id == link.record_id]
            if len(matches) != 1:
                raise ValueError('关联的远端记录已删除或无法唯一定位，不能自动新增代替。')
            remote = matches[0]
        elif record.local_id is not None:
            if attempts := self.store.sheet_add_attempts(record.local_id):
                if issues:
                    return RecordSyncCheck(snapshot, None, issues, ())
                raise RecordAddRecoveryRequired(self._add_recovery(attempts, snapshot))
            # A previous add might have succeeded even when its response was lost.
            spec = next(spec for spec in self.fields.fields if spec.title == '地图名')
            mod_spec = next(spec for spec in self.fields.fields if spec.title == 'Mod元数据名')
            bound = self.store.bound_sheet_record_ids(snapshot.file_id, snapshot.sheet_id)
            for row in snapshot.records:
                cells = row.values
                if (
                    row.record_id not in bound
                    and self.fields.decode(spec, cells.get('地图名')) == record.map_name
                    and self.fields.decode(mod_spec, cells.get('Mod元数据名'))
                    == record.mod_metadata_name
                ):
                    raise ValueError(
                        '本地记录尚未关联表格，但远端已有同名记录。请核对关联，不能自动重试新增。'
                    )
        if issues or remote is None:
            return RecordSyncCheck(snapshot, remote, issues, ())
        if expected is not None:
            if (
                (expected.snapshot.file_id, expected.snapshot.sheet_id)
                != (snapshot.file_id, snapshot.sheet_id)
                or expected.remote is None
                or expected.remote.record_id != remote.record_id
            ):
                raise ValueError('核对期间远端记录身份发生变化。')
            baseline = expected.remote.values
        else:
            assert link is not None
            baseline = link.confirmed_values
        if baseline is None:
            # Unconfirmed remote contents need review unless already equal locally.
            baseline = {}
        before = self.fields.comparable(baseline, snapshot.fields)
        after = self.fields.comparable(remote.values, snapshot.fields)
        values = encode_record_values(record, self.fields.writable_fields(snapshot.fields))
        local = self.fields.comparable(values, snapshot.fields)
        differences = tuple(
            RecordFieldDifference(title, value, after[title])
            for title, value in before.items()
            if value != after[title] and (title not in values or local[title] != after[title])
        )
        return RecordSyncCheck(snapshot, remote, issues, differences)

    def _add_recovery(
        self, attempts: tuple[RecordSheetAddAttempt, ...], snapshot: MainSheetSnapshot
    ) -> RecordAddRecovery:
        if len(attempts) != 1:
            raise ValueError('存在多个未确认的新增尝试，不能自动恢复。')
        attempt = attempts[0]
        if (attempt.file_id, attempt.sheet_id) != (snapshot.file_id, snapshot.sheet_id):
            raise ValueError('未确认的新增尝试属于其他表格，请切换回原表格核对。')
        bound = self.store.bound_sheet_record_ids(snapshot.file_id, snapshot.sheet_id)
        map_spec = next(spec for spec in self.fields.fields if spec.title == '地图名')
        mod_spec = next(spec for spec in self.fields.fields if spec.title == 'Mod元数据名')
        candidates = tuple(
            row
            for row in snapshot.records
            if row.record_id is not None
            and row.record_id not in bound
            and self.fields.decode(map_spec, row.values.get('地图名')) == attempt.map_name
            and (self.fields.decode(mod_spec, row.values.get('Mod元数据名')) or '')
            == attempt.mod_metadata_name
        )
        return RecordAddRecovery(attempt, snapshot, candidates)

    async def resolve_add(self, recovery: RecordAddRecovery, *, record_id: str | None) -> None:
        """Revalidate a reviewed choice, then associate a row or permit a confirmed retry.

        Passing no row requires explicit user confirmation that the request has ended
        and no remote row was added. Neither operation acknowledges remote contents.
        """
        snapshot = await self.client.read_main_sheet(self.source)
        self.store.observe_sheet(snapshot, read_at=datetime.now(UTC))
        if issues := self.fields.issues(snapshot.fields):
            raise ValueError('\n'.join(issues))
        latest = self._add_recovery(
            self.store.sheet_add_attempts(recovery.attempt.local_id), snapshot
        )
        if latest.attempt != recovery.attempt:
            raise ValueError('新增尝试已变化，请重新核对。')
        if record_id is None:
            if latest.candidates or recovery.candidates:
                raise ValueError('存在候选行，不能直接解除新增保护；请重新核对。')
            self.store.allow_sheet_add_retry(latest.attempt)
            return
        reviewed = next((row for row in recovery.candidates if row.record_id == record_id), None)
        current = next((row for row in latest.candidates if row.record_id == record_id), None)
        if reviewed is None or current is None or reviewed.values != current.values:
            raise ValueError('选中的候选行已变化或已绑定，请重新核对。')
        self.store.link_sheet_record(
            RecordSheetLink(
                local_id=latest.attempt.local_id,
                file_id=snapshot.file_id,
                sheet_id=snapshot.sheet_id,
                record_id=record_id,
            )
        )

    def use_remote(self, record: MapRecord, check: RecordSyncCheck) -> MapRecord:
        """Adopt public remote contents without replacing private record identity."""
        if check.issues or check.remote is None:
            raise ValueError('没有可采用的兼容远端记录。')
        if any(difference.title == '编号' for difference in check.differences):
            raise ValueError(
                '远端编号已变化，请采用本地编号或先在本地调整顺序，不能逐条采用远端编号。'
            )
        adopted = self.fields.apply_remote(record, check.remote.values, check.snapshot.fields)
        if record.map_file is not None and adopted.mod_metadata_name != record.mod_metadata_name:
            raise ValueError('远端 Mod 元数据名已变化，涉及地图身份调整，不能自动采用。')
        return adopted

    async def save(
        self, record: MapRecord, check: RecordSyncCheck | None = None
    ) -> RecordSyncResult:
        """Recheck and save locally, confirming equal remote data or verifying a write.

        Without a prior reviewed check, reconcile against the last confirmed baseline.
        Failures after the local save leave it pending. A returned row ID is retained
        even if verification fails, so subsequent attempts update rather than add.
        """
        latest = await self.check(record, expected=check)
        return await self.submit_checked(record, latest)

    async def submit_checked(
        self,
        record: MapRecord,
        latest: RecordSyncCheck,
    ) -> RecordSyncResult:
        """Save and submit using a freshly reconciled check, without another read.

        The caller must obtain this check for this record from check() or
        check_snapshot() immediately before submission. This is the shared
        single/batch commit step, not a way to bypass conflict review. Issues and
        unresolved differences are rejected before saving or writing anything.
        """
        if latest.issues:
            raise ValueError('\n'.join(latest.issues))
        if latest.differences:
            raise RecordSyncConflict(latest)
        values = encode_record_values(record, self.fields.writable_fields(latest.snapshot.fields))
        saved = self.store.load(self.store.save(record))
        assert saved.local_id is not None
        if saved.record_number != record.record_number:
            # Starting a record allocates its number during save; publish the saved projection.
            values = encode_record_values(
                saved, self.fields.writable_fields(latest.snapshot.fields)
            )
        if latest.remote is not None:
            written = self.fields.comparable(values, latest.snapshot.fields)
            actual = self.fields.comparable(latest.remote.values, latest.snapshot.fields)
            if all(written[title] == actual[title] for title in values):
                assert latest.remote.record_id is not None
                self.store.confirm_sheet_record(
                    RecordSheetLink(
                        local_id=saved.local_id,
                        file_id=latest.snapshot.file_id,
                        sheet_id=latest.snapshot.sheet_id,
                        record_id=latest.remote.record_id,
                    ),
                    latest.remote.values,
                    submitted=saved,
                    confirmed_at=datetime.now(UTC),
                )
                return RecordSyncResult(saved, True)
        if latest.remote is None:
            self.store.begin_sheet_add(
                RecordSheetAddAttempt(
                    local_id=saved.local_id,
                    file_id=latest.snapshot.file_id,
                    sheet_id=latest.snapshot.sheet_id,
                    started_at=datetime.now(UTC),
                    mod_metadata_name=saved.mod_metadata_name or '',
                    map_name=saved.map_name,
                )
            )
        association_saved = latest.remote is not None
        try:
            response = await self.client.write_record(
                latest.snapshot,
                values,
                record_id=None if latest.remote is None else latest.remote.record_id,
            )
            if not response.record_id:
                raise ValueError('写入响应缺少记录 ID，不能确认成功。')
            if latest.remote is not None and response.record_id != latest.remote.record_id:
                raise ValueError('写入响应返回了不同的记录 ID，不能确认成功。')
            link = RecordSheetLink(
                local_id=saved.local_id,
                file_id=latest.snapshot.file_id,
                sheet_id=latest.snapshot.sheet_id,
                record_id=response.record_id,
            )
            if latest.remote is None:
                self.store.link_sheet_record(link)
                association_saved = True
            checked = await self.response_schema(latest.snapshot, ((values, response),))
            self.confirm_submission(RecordSubmission(saved, link, values), response, checked)
        except (ClientError, OSError, RuntimeError, TypeError, ValueError) as error:
            message = str(error)
            if not association_saved:
                message += '；新增结果尚未确认，请人工核对并关联远端记录，不能直接重试新增。'
            return RecordSyncResult(saved, False, message)
        return RecordSyncResult(saved, True)

    def matches_remote(self, record: MapRecord, check: RecordSyncCheck) -> bool:
        """Whether all supported writable cells already equal the local projection."""
        if check.remote is None or check.issues:
            return False
        values = encode_record_values(record, self.fields.writable_fields(check.snapshot.fields))
        written = self.fields.comparable(values, check.snapshot.fields)
        actual = self.fields.comparable(check.remote.values, check.snapshot.fields)
        return all(written[title] == actual[title] for title in values)

    async def response_schema(
        self, checked: MainSheetSnapshot, responses: tuple[tuple[JsonObject, SmartSheetRecord], ...]
    ) -> MainSheetSnapshot:
        """Refresh schema at most once when a write introduces unknown select options."""
        if not any(
            needs_option_refresh(response.values, values, checked.fields)
            for values, response in responses
        ):
            return checked
        fields = await self.client.read_fields(checked.file_id, checked.sheet_id)
        if issues := self.fields.issues(fields):
            raise ValueError('\n'.join(issues))
        before = {
            (field.field_id, field.field_title, field.field_type)
            for field in self.fields.writable_fields(checked.fields).fields
        }
        after = {
            (field.field_id, field.field_title, field.field_type)
            for field in self.fields.writable_fields(fields).fields
        }
        if before != after:
            raise ValueError('写入期间支持字段的结构发生变化，不能确认成功。')
        return checked.model_copy(update={'fields': fields})

    def confirm_submission(
        self, submission: RecordSubmission, response: SmartSheetRecord, checked: MainSheetSnapshot
    ) -> None:
        """Verify successful response cells and acknowledge only that submitted projection."""
        link, saved, values = submission.link, submission.record, submission.values
        if (checked.file_id, checked.sheet_id) != (link.file_id, link.sheet_id):
            raise ValueError('核对的表格与写入关联不一致。')
        if issues := self.fields.issues(checked.fields):
            raise ValueError('\n'.join(issues))
        if response.record_id != link.record_id:
            raise ValueError('写入响应中的记录 ID 与提交值不一致。')
        returned = submitted_response_values(response.values, values, checked.fields)
        written = self.fields.comparable(values, checked.fields)
        actual = self.fields.comparable(returned, checked.fields)
        if any(written[title] != actual[title] for title in values):
            raise ValueError('写入响应的字段值与提交值不一致，记录保留为待推送。')
        previous = next(
            (row.values for row in checked.records if row.record_id == link.record_id), {}
        )
        self.store.confirm_sheet_record(
            link, {**previous, **returned}, submitted=saved, confirmed_at=datetime.now(UTC)
        )
