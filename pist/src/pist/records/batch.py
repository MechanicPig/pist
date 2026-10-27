"""Quota-conscious sheet synchronization with shared reads and response verification."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiohttp import ClientError

from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import MainSheetSnapshot, RecordWrite, SmartSheetRecord
from pist.smartsheet.response import UnknownWriteOptionID

from .models import MapRecord
from .store import RecordSheetLink
from .sync import (
    RecordSubmission,
    RecordSyncCheck,
    RecordSyncConflict,
    RecordSyncResult,
    RecordSyncService,
)

# Application chunk size, not a claim about the remote API's maximum.
UPDATE_BATCH_SIZE = 100
type ConflictResolver = Callable[[MapRecord, RecordSyncCheck], Awaitable[MapRecord | None]]


@dataclass(frozen=True, slots=True)
class RecordQueuedUpdate:
    """A saved local update not yet submitted to the remote sheet."""

    record: MapRecord
    check: RecordSyncCheck


class RecordSyncBatch:
    """Share a sheet read, recheck actual writes and verify accepted writes together."""

    def __init__(self, service: RecordSyncService) -> None:
        self.service = service
        self._snapshot: MainSheetSnapshot | None = None
        self._updates: dict[int, RecordQueuedUpdate] = {}

    def invalidate(self) -> None:
        """Require a fresh snapshot after a user interaction or recovery decision."""
        self._snapshot = None

    def awaiting_confirmation(self, local_id: int) -> bool:
        """Whether a saved local update is still queued for submission."""
        return local_id in self._updates

    async def check(self, record: MapRecord) -> RecordSyncCheck:
        """Reconcile a record using the current shared snapshot."""
        if self._snapshot is None:
            self._snapshot = await self.service.read_snapshot()
        return self.service.check_snapshot(record, self._snapshot)

    async def save(
        self, record: MapRecord, check: RecordSyncCheck
    ) -> RecordQueuedUpdate | RecordSyncResult:
        """Confirm equal data, queue existing updates, or submit a protected new row."""
        if self._snapshot is None:
            self._snapshot = await self.service.read_snapshot()
        latest = self.service.check_snapshot(record, self._snapshot, expected=check)
        if latest.issues:
            raise ValueError('\n'.join(latest.issues))
        if latest.differences:
            raise RecordSyncConflict(latest)
        if latest.remote is not None and not self.service.matches_remote(record, latest):
            saved = self.service.store.load(self.service.store.save(record))
            assert saved.local_id is not None
            queued = RecordQueuedUpdate(saved, latest)
            self._updates[saved.local_id] = queued
            return queued
        if latest.remote is None:
            # New rows retain their individual durable uncertain-add protection.
            latest = await self.service.check(record, expected=check)
        result = await self.service.submit_checked(record, latest)
        if latest.remote is None:
            self.invalidate()
        return result

    async def finish(self, resolve: ConflictResolver | None = None) -> bool:
        """Submit freshly reconciled batches and verify accepted writes; false on cancellation."""
        while self._updates:
            queued = tuple(self._updates.values())[:UPDATE_BATCH_SIZE]
            ids = tuple(
                item.check.remote.record_id
                for item in queued
                if item.check.remote is not None and item.check.remote.record_id is not None
            )
            snapshot = await self.service.read_snapshot(record_ids=ids)
            submissions: list[RecordSubmission] = []
            for item in queued:
                latest = self.service.check_snapshot(item.record, snapshot, expected=item.check)
                if latest.issues:
                    raise ValueError('\n'.join(latest.issues))
                if latest.differences:
                    if resolve is None:
                        raise RecordSyncConflict(latest)
                    chosen = await resolve(item.record, latest)
                    self.invalidate()
                    if chosen is None:
                        await self.abort()
                        return False
                    assert chosen.local_id is not None
                    saved = self.service.store.load(self.service.store.save(chosen))
                    self._updates[chosen.local_id] = RecordQueuedUpdate(saved, latest)
                    break
                assert latest.remote is not None and latest.remote.record_id is not None
                if self.service.matches_remote(item.record, latest):
                    await self.service.submit_checked(item.record, latest)  # pyright: ignore[reportUnusedCallResult]
                    assert item.record.local_id is not None
                    del self._updates[item.record.local_id]
                    continue
                assert item.record.local_id is not None
                submissions.append(
                    RecordSubmission(
                        item.record,
                        RecordSheetLink(
                            local_id=item.record.local_id,
                            file_id=snapshot.file_id,
                            sheet_id=snapshot.sheet_id,
                            record_id=latest.remote.record_id,
                        ),
                        encode_record_values(
                            item.record, self.service.fields.writable_fields(snapshot.fields)
                        ),
                    )
                )
            else:
                if submissions:
                    # Remove before dispatch: an uncertain response must not trigger a retry.
                    for submission in submissions:
                        assert submission.record.local_id is not None
                        del self._updates[submission.record.local_id]
                    response = await self.service.client.update_records(
                        snapshot,
                        tuple(
                            RecordWrite(record_id=item.link.record_id, values=item.values)
                            for item in submissions
                        ),
                    )
                    self.invalidate()
                    await self._confirm_responses(submissions, response, snapshot)
        return True

    async def abort(self) -> None:
        """Discard unsent updates; completed response confirmations remain committed."""
        self._updates.clear()

    async def _confirm_responses(
        self,
        submissions: list[RecordSubmission],
        response: tuple[SmartSheetRecord, ...],
        snapshot: MainSheetSnapshot,
    ) -> None:
        """Confirm each successful batch row without interpreting omissions as success."""
        ids = [row.record_id for row in response]
        expected = {item.link.record_id for item in submissions}
        if any(not id_ for id_ in ids) or len(set(ids)) != len(ids) or not set(ids) <= expected:
            raise ValueError('批量写入响应的记录 ID 无效、重复或不属于提交范围。')
        by_id = {row.record_id: row for row in response}
        refresh_error: ClientError | OSError | RuntimeError | None = None
        try:
            snapshot = await self.service.response_schema(
                snapshot,
                tuple(
                    (item.values, by_id[item.link.record_id])
                    for item in submissions
                    if item.link.record_id in by_id
                ),
            )
        except (ClientError, OSError, RuntimeError) as error:
            # A failed lookup only removes evidence for unknown options. Rows already
            # verifiable with the checked schema can still be confirmed independently.
            # Schema incompatibility (ValueError) remains a batch-wide rejection.
            refresh_error = error
        errors = []
        for submission in submissions:
            try:
                returned = by_id.get(submission.link.record_id)
                if returned is None:
                    raise ValueError('批量写入响应未确认此记录。')
                self.service.confirm_submission(submission, returned, snapshot)
            except UnknownWriteOptionID as error:
                detail = f'{error} 字段刷新失败：{refresh_error}' if refresh_error else str(error)
                errors.append(f'{submission.record.map_name}：{detail}')
            except ValueError as error:
                errors.append(f'{submission.record.map_name}：{error}')
        if errors:
            raise ValueError('\n'.join(errors))
