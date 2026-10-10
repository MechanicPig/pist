"""Quota-conscious sheet synchronization with shared reads and deferred verification."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink
from pist.records.sync import (
    RecordSubmission,
    RecordSyncCheck,
    RecordSyncConflict,
    RecordSyncResult,
    RecordSyncService,
)
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.models import MainSheetSnapshot, RecordWrite

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
        self._submissions: list[RecordSubmission] = []

    def invalidate(self) -> None:
        """Require a fresh snapshot after a user interaction or recovery decision."""
        self._snapshot = None

    def awaiting_confirmation(self, local_id: int) -> bool:
        """Whether a local update is queued or an accepted write awaits readback."""
        return local_id in self._updates or any(
            item.record.local_id == local_id for item in self._submissions
        )

    async def check(self, record: MapRecord) -> RecordSyncCheck:
        """Reconcile a record using the current shared snapshot."""
        if self._snapshot is None:
            self._snapshot = await self.service.read_snapshot()
        return self.service.check_snapshot(record, self._snapshot)

    async def save(
        self, record: MapRecord, check: RecordSyncCheck
    ) -> RecordQueuedUpdate | RecordSubmission | RecordSyncResult:
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
        submission = await self.service._submit_checked(record, latest)
        if isinstance(submission, RecordSyncResult):
            return submission
        self._submissions.append(submission)
        # Accepted is not confirmed: persisted synchronization state remains pending.
        return submission

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
                        self._updates.clear()
                        await self.confirm()
                        return False
                    assert chosen.local_id is not None
                    saved = self.service.store.load(self.service.store.save(chosen))
                    self._updates[chosen.local_id] = RecordQueuedUpdate(saved, latest)
                    break
                assert latest.remote is not None and latest.remote.record_id is not None
                if self.service.matches_remote(item.record, latest):
                    await self.service._submit_checked(item.record, latest)
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
                    await self.service.client.update_records(
                        snapshot,
                        tuple(
                            RecordWrite(record_id=item.link.record_id, values=item.values)
                            for item in submissions
                        ),
                    )
                    self._submissions.extend(submissions)
                    await self.confirm()
        await self.confirm()
        return True

    async def confirm(self) -> None:
        """Read back accepted IDs together, confirming only individually matching rows."""
        if not self._submissions:
            return
        snapshot = await self.service.read_snapshot(
            record_ids=tuple(dict.fromkeys(item.link.record_id for item in self._submissions))
        )
        errors = []
        for submission in self._submissions:
            try:
                self.service.confirm_submission(submission, snapshot)
            except ValueError as error:
                errors.append(f'{submission.record.map_name}：{error}')
        self._submissions.clear()
        # A subset must never replace the full snapshot used for unbound-row matching.
        self.invalidate()
        if errors:
            raise ValueError('\n'.join(errors))
