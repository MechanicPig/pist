"""Coordinate record editing and sync decisions without parsing external data."""

from collections.abc import Callable
from typing import Literal

from aiohttp import ClientError
from textual.app import App

from pist.records.batch import RecordQueuedUpdate, RecordSyncBatch
from pist.records.models import MANUAL_RECORD_FIELD_TITLES, MapRecord, merge_saved_record
from pist.records.store import RecordStorageError, RecordStore, RecordSyncState
from pist.records.sync import (
    RecordAddRecoveryRequired,
    RecordSheetClient,
    RecordSubmission,
    RecordSyncCheck,
    RecordSyncConflict,
    RecordSyncService,
)
from pist.smartsheet.fields import load_sheet_fields
from pist.smartsheet.models import FieldType
from pist.smartsheet.report import ManualRecordField, manual_fields_for_schema

from .editor import RecordEditorScreen, RecordSaveModeScreen
from .recovery import RecordAddRecoveryChoice, RecordAddRecoveryScreen, RecordAddRetryScreen
from .sync import RecordConflictScreen, RecordOfflineScreen, RecordSyncChoice

type EditorFactory = Callable[[MapRecord, tuple[ManualRecordField, ...]], RecordEditorScreen]


class RecordEditorController:
    """Run one edit while retaining per-application consent for local-only saves."""

    def __init__(
        self,
        app: App[None],
        store: RecordStore,
        *,
        client: RecordSheetClient | None,
        source: str | None,
        manual_fields: tuple[ManualRecordField, ...],
    ) -> None:
        self.app = app
        self.store = store
        self.service = (
            None if client is None or not source else RecordSyncService(store, client, source)
        )
        self.remote_requested = client is not None or source is not None
        self.allow_offline = False
        self.manual_fields = manual_fields or tuple(
            ManualRecordField(
                spec.title, FieldType.TEXT if spec.type == FieldType.SINGLE_SELECT else spec.type
            )
            for spec in load_sheet_fields().fields
            if spec.title in MANUAL_RECORD_FIELD_TITLES
        )

    async def edit(self, record: MapRecord, factory: EditorFactory) -> None:
        """Choose a record target, reconcile, edit and save after explicit decisions."""
        candidates = tuple(self.store.load(id_) for id_ in self.store.matching_ids(record))
        if candidates:
            target = await self.app.push_screen_wait(
                RecordSaveModeScreen(candidates, save_slot=record.save_slot)
            )
            if target is None:
                return
            if target:
                record = merge_saved_record(record, self.store.load(target))
        await self.edit_record(record, factory)

    async def edit_record(self, record: MapRecord, factory: EditorFactory) -> None:
        """Edit one explicit record without map matching or refreshing game statistics."""
        check, mode = await self._prepare(record)
        if mode is None:
            return
        if check is not None and check.differences:
            assert self.service is not None
            mode = await self.app.push_screen_wait(
                RecordConflictScreen(record, check, self.service)
            )
            if mode is None:
                return
            if mode is RecordSyncChoice.REMOTE:
                record = self.service.use_remote(record, check)
            elif mode is RecordSyncChoice.ONLY_LOCAL:
                check = None
        edited = await self.app.push_screen_wait(factory(record, self.manual_fields))
        if edited is None:
            return
        await self._save(edited, check)

    async def sync_record(
        self, record: MapRecord, *, quiet: bool = False, batch: RecordSyncBatch | None = None
    ) -> bool:
        """Reconcile one saved record, reporting confirmation or enrollment in a batch."""
        check, mode = await self._prepare(record, batch=batch)
        if mode is None:
            return False
        if check is None:
            self.app.notify('未能核对远端，记录未同步。', severity='warning')
            return False
        if check.differences:
            assert self.service is not None
            choice = await self.app.push_screen_wait(
                RecordConflictScreen(record, check, self.service)
            )
            if batch is not None:
                batch.invalidate()
            if choice is None or choice is RecordSyncChoice.ONLY_LOCAL:
                return False
            if choice is RecordSyncChoice.REMOTE:
                record = self.service.use_remote(record, check)
        await self._save(record, check, quiet=quiet, batch=batch)
        assert record.local_id is not None
        return self.store.sync_state(record.local_id) is RecordSyncState.SYNCED or (
            batch is not None and batch.awaiting_confirmation(record.local_id)
        )

    async def _prepare(
        self, record: MapRecord, *, batch: RecordSyncBatch | None = None
    ) -> tuple[RecordSyncCheck | None, RecordSyncChoice | None]:
        if self.service is None:
            mode = (
                await self._offline('未配置表格地址或同步客户端。')
                if self.remote_requested
                else RecordSyncChoice.LOCAL
            )
            return None, mode
        try:
            check = await self._check_with_recovery(record, batch=batch)
            if check is None:
                return None, None
            if check is RecordSyncChoice.ONLY_LOCAL:
                return None, RecordSyncChoice.LOCAL
        except RecordStorageError as error:
            self.app.notify(str(error), severity='error')
            return None, None
        except (ClientError, OSError, RuntimeError) as error:
            return None, await self._offline(str(error))
        except (TypeError, ValueError) as error:
            return None, await self._offline(str(error), remember=False)
        if check.issues:
            return None, await self._offline('\n'.join(check.issues), remember=False)
        self.manual_fields = manual_fields_for_schema(check.snapshot.fields)
        return check, RecordSyncChoice.LOCAL

    async def _check_with_recovery(
        self, record: MapRecord, *, batch: RecordSyncBatch | None = None
    ) -> RecordSyncCheck | Literal[RecordSyncChoice.ONLY_LOCAL] | None:
        assert self.service is not None
        while True:
            try:
                return await (self.service.check(record) if batch is None else batch.check(record))
            except RecordAddRecoveryRequired as error:
                choice = await self.app.push_screen_wait(
                    RecordAddRecoveryScreen(error.recovery, self.service)
                )
                if batch is not None:
                    batch.invalidate()
                if choice is None:
                    return None
                if choice is RecordAddRecoveryChoice.ONLY_LOCAL:
                    # Keep the protection while allowing further local edits.
                    return RecordSyncChoice.ONLY_LOCAL
                if choice is RecordAddRecoveryChoice.RETRY:
                    confirmed = await self.app.push_screen_wait(RecordAddRetryScreen())
                    if not confirmed:
                        continue
                    target = None
                else:
                    target = choice
                try:
                    await self.service.resolve_add(error.recovery, record_id=target)
                except RecordStorageError:
                    raise
                except ValueError as changed:
                    self.app.notify(str(changed), severity='warning')
                    # Re-read candidates; a stale choice never becomes fresh consent.

    async def _offline(self, reason: str, *, remember: bool = True) -> RecordSyncChoice | None:
        if remember and self.allow_offline:
            return RecordSyncChoice.LOCAL
        choice = await self.app.push_screen_wait(RecordOfflineScreen(reason))
        if choice is RecordSyncChoice.LOCAL and remember:
            self.allow_offline = True
        return choice

    async def _save(
        self,
        record: MapRecord,
        check: RecordSyncCheck | None,
        *,
        quiet: bool = False,
        batch: RecordSyncBatch | None = None,
    ) -> None:
        if check is None or self.service is None:
            self._local(record)
            return
        while True:
            try:
                result = await (
                    self.service.save(record, check) if batch is None else batch.save(record, check)
                )
            except RecordSyncConflict as error:
                check = error.check
                choice = await self.app.push_screen_wait(
                    RecordConflictScreen(record, check, self.service)
                )
                if batch is not None:
                    batch.invalidate()
                if choice is None:
                    return
                if choice is RecordSyncChoice.ONLY_LOCAL:
                    self._local(record)
                    return
                if choice is RecordSyncChoice.REMOTE:
                    record = self.service.use_remote(record, check)
                continue
            except RecordStorageError as error:
                self.app.notify(str(error), severity='error')
                return
            except (ClientError, OSError, RuntimeError) as error:
                choice = await self._offline(str(error))
                if choice is RecordSyncChoice.LOCAL:
                    self._local(record)
                return
            except (TypeError, ValueError) as error:
                choice = await self._offline(str(error), remember=False)
                if choice is RecordSyncChoice.LOCAL:
                    self._local(record)
                return
            if isinstance(result, (RecordSubmission, RecordQueuedUpdate)):
                return
            assert result.record.local_id is not None
            if (
                result.synced
                and self.store.sync_state(result.record.local_id) is RecordSyncState.SYNCED
            ):
                if not quiet:
                    self.app.notify(
                        f'本地记录 {result.record.record_number or "未编号"} 已保存并同步。'
                    )
            else:
                self.app.notify(
                    f'本地记录已保存，仍待同步：{result.error or "存在更新的本地修改"}',
                    severity='warning',
                )
            return

    async def resolve_batch_conflict(
        self, record: MapRecord, check: RecordSyncCheck
    ) -> MapRecord | None:
        """Choose how a staged batch row should handle a newly detected remote change."""
        assert self.service is not None
        choice = await self.app.push_screen_wait(RecordConflictScreen(record, check, self.service))
        if choice is None or choice is RecordSyncChoice.ONLY_LOCAL:
            return None
        return (
            self.service.use_remote(record, check) if choice is RecordSyncChoice.REMOTE else record
        )

    def _local(self, record: MapRecord) -> None:
        try:
            saved = self.store.load(self.store.save(record))
        except (RecordStorageError, ValueError) as error:
            self.app.notify(str(error), severity='error')
            return
        self.app.notify(f'本地记录 {saved.record_number or "未编号"} 已保存，待同步。')
