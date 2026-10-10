"""Reconcile one local experience record with its explicitly associated remote row."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from aiohttp import ClientError
from pydantic import JsonValue

from pist.records.models import MapRecord
from pist.records.store import RecordSheetAddAttempt, RecordSheetLink, RecordStore
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.fields import SheetFields, load_sheet_fields
from pist.smartsheet.models import JsonObject, MainSheetSnapshot, RecordWrite, SmartSheetRecord


class RecordSheetClient(Protocol):
    """Replaceable remote boundary for the record editing workflow."""

    async def read_main_sheet(
        self, source: str, *, record_ids: tuple[str, ...] | None = None
    ) -> MainSheetSnapshot: ...

    async def write_record(
        self, snapshot: MainSheetSnapshot, values: JsonObject, *, record_id: str | None
    ) -> str: ...

    async def update_records(
        self, snapshot: MainSheetSnapshot, records: tuple[RecordWrite, ...]
    ) -> None: ...


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
    """A remotely accepted write awaiting readback confirmation."""

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
        adopted = self.fields.apply_remote(record, check.remote.values, check.snapshot.fields)
        if record.map_file is not None and adopted.mod_metadata_name != record.mod_metadata_name:
            raise ValueError('远端 Mod 元数据名已变化，涉及地图身份调整，不能自动采用。')
        return adopted

    async def save(self, record: MapRecord, check: RecordSyncCheck) -> RecordSyncResult:
        """Recheck and save locally, confirming equal remote data or verifying a write.

        Failures after the local save leave it pending. A returned row ID is retained
        even if verification fails, so subsequent attempts update rather than add.
        """
        latest = await self.check(record, expected=check)
        submission = await self._submit_checked(record, latest)
        if isinstance(submission, RecordSyncResult):
            return submission
        try:
            self.confirm_submission(
                submission, await self.read_snapshot(record_ids=(submission.link.record_id,))
            )
        except (ClientError, OSError, RuntimeError, TypeError, ValueError) as error:
            return RecordSyncResult(submission.record, False, str(error))
        return RecordSyncResult(submission.record, True)

    async def _submit_checked(
        self,
        record: MapRecord,
        latest: RecordSyncCheck,
    ) -> RecordSubmission | RecordSyncResult:
        """Save a freshly reconciled record and submit only differing public data."""
        if latest.issues:
            raise ValueError('\n'.join(latest.issues))
        if latest.differences:
            raise RecordSyncConflict(latest)
        values = encode_record_values(record, self.fields.writable_fields(latest.snapshot.fields))
        saved = self.store.load(self.store.save(record))
        assert saved.local_id is not None
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
            remote_id = await self.client.write_record(
                latest.snapshot,
                values,
                record_id=None if latest.remote is None else latest.remote.record_id,
            )
            link = RecordSheetLink(
                local_id=saved.local_id,
                file_id=latest.snapshot.file_id,
                sheet_id=latest.snapshot.sheet_id,
                record_id=remote_id,
            )
            if latest.remote is None:
                self.store.link_sheet_record(link)
                association_saved = True
        except (ClientError, OSError, RuntimeError, TypeError, ValueError) as error:
            message = str(error)
            if not association_saved:
                message += '；新增结果尚未确认，请人工核对并关联远端记录，不能直接重试新增。'
            return RecordSyncResult(saved, False, message)
        return RecordSubmission(saved, link, values)

    def matches_remote(self, record: MapRecord, check: RecordSyncCheck) -> bool:
        """Whether all supported writable cells already equal the local projection."""
        if check.remote is None or check.issues:
            return False
        values = encode_record_values(record, self.fields.writable_fields(check.snapshot.fields))
        written = self.fields.comparable(values, check.snapshot.fields)
        actual = self.fields.comparable(check.remote.values, check.snapshot.fields)
        return all(written[title] == actual[title] for title in values)

    def confirm_submission(self, submission: RecordSubmission, verified: MainSheetSnapshot) -> None:
        """Confirm one accepted write against a readback, preserving pending state on mismatch."""
        link, saved, values = submission.link, submission.record, submission.values
        if (verified.file_id, verified.sheet_id) != (link.file_id, link.sheet_id):
            raise ValueError('写入后读取到了不同的表格。')
        if issues := self.fields.issues(verified.fields):
            raise ValueError('\n'.join(issues))
        rows = [row for row in verified.records if row.record_id == link.record_id]
        if len(rows) != 1:
            raise ValueError('无法读取刚提交的记录，尚不能确认同步成功。')
        written = self.fields.comparable(values, verified.fields)
        actual = self.fields.comparable(rows[0].values, verified.fields)
        if any(written[title] != actual[title] for title in values):
            raise ValueError('写入后的远端值与提交值不一致，记录保留为待同步。')
        self.store.confirm_sheet_record(
            link, rows[0].values, submitted=saved, confirmed_at=datetime.now(UTC)
        )
