"""Coordinate record editing and sync decisions without parsing external data."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from aiohttp import ClientError
from textual.app import App
from textual.screen import ModalScreen

from pist.records.batch import RecordQueuedUpdate, RecordSyncBatch
from pist.records.models import MANUAL_RECORD_FIELD_TITLES, MapRecord
from pist.records.store import RecordStorageError, RecordStore, RecordSyncState
from pist.records.sync import (
    RecordAddRecoveryRequired,
    RecordSheetClient,
    RecordSyncCheck,
    RecordSyncConflict,
    RecordSyncService,
)
from pist.smartsheet.fields import load_sheet_fields
from pist.smartsheet.models import FieldType
from pist.smartsheet.report import ManualRecordField, manual_fields_for_schema

from .background import BackgroundRecordSync
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
        self.background_sync = BackgroundRecordSync(app, self._background_save)
        self._interaction_lock = asyncio.Lock()
        self._interaction_owner: asyncio.Task[None] | None = None
        self.manual_fields = manual_fields or tuple(
            ManualRecordField(
                spec.title,
                FieldType.TEXT if spec.type == FieldType.SINGLE_SELECT else spec.type,
            )
            for spec in load_sheet_fields().fields
            if spec.title in MANUAL_RECORD_FIELD_TITLES
        )

    async def edit(self, record: MapRecord, factory: EditorFactory) -> None:
        """Choose and edit a record, then independently synchronize the saved result."""
        async with self._interaction():
            await self._edit(record, factory)

    async def _edit(self, record: MapRecord, factory: EditorFactory) -> None:
        """Choose a local record target before editing, without querying the remote sheet."""
        candidates = tuple(self.store.load(id_) for id_ in self.store.matching_ids(record))
        if candidates:
            target = await self._dialog(
                RecordSaveModeScreen(candidates, save_slot=record.save_slot)
            )
            if target is None:
                return
            if target:
                record = record.with_saved_fields(self.store.load(target))
        await self.edit_record(record, factory)

    async def edit_record(self, record: MapRecord, factory: EditorFactory) -> None:
        """Edit one identity and enqueue its locally saved changes for synchronization."""
        async with self._interaction():
            await self._edit_record(record, factory)

    async def _edit_record(self, record: MapRecord, factory: EditorFactory) -> None:
        """Edit one explicit record without map matching or refreshing game statistics."""
        if self.background_sync.contains(record.local_id):
            self.app.notify('该记录仍在后台同步，请完成后再编辑。', severity='warning')
            return
        observed = self.store.latest_sheet_fields()
        supported = self.service.fields if self.service is not None else load_sheet_fields()
        fields = (
            manual_fields_for_schema(observed)
            if observed is not None and not supported.issues(observed)
            else self.manual_fields
        )
        edited = await self._dialog(factory(record, fields))
        if edited is None:
            return
        if self.service is None and not self.remote_requested:
            self._local(edited)
            return
        try:
            saved = self.store.load(self.store.save(edited))
        except (RecordStorageError, ValueError) as error:
            self.app.notify(str(error), severity='error')
            return
        self.background_sync.enqueue(saved)
        self.app.notify(f'本地记录 {saved.record_number or "未编号"} 已保存，正在后台同步。')

    async def _background_save(self, record: MapRecord) -> bool:
        """Reconcile a saved edit for the first time immediately before remote submission."""
        if self.service is None:
            await self._offline('未配置表格地址或同步客户端；修改已保存本地。')  # pyright: ignore[reportUnusedCallResult]
            return False
        return await self._save(record, None)

    @asynccontextmanager
    async def _interaction(self) -> AsyncGenerator[None]:
        """Serialize dialogs while allowing network requests behind an open editor."""
        task = asyncio.current_task()
        if task is self._interaction_owner:
            yield
            return
        async with self._interaction_lock:
            self._interaction_owner = task
            try:
                yield
            finally:
                self._interaction_owner = None

    async def _dialog[T](self, screen: ModalScreen[T]) -> T:
        async with self._interaction():
            return await self.app.push_screen_wait(screen)

    async def sync_record(
        self, record: MapRecord, *, quiet: bool = False, batch: RecordSyncBatch | None = None
    ) -> bool:
        """Reconcile one saved record, reporting confirmation or enrollment in a batch."""
        await self.background_sync.wait()
        if record.local_id is not None:
            record = self.store.load(record.local_id)
        check, mode = await self._prepare(record, batch=batch)
        if mode is None:
            return False
        if check is None:
            self.app.notify('未能核对远端，记录未同步。', severity='warning')
            return False
        if check.differences:
            assert self.service is not None
            choice = await self._dialog(RecordConflictScreen(record, check, self.service))
            if batch is not None:
                batch.invalidate()
            if choice is None or choice is RecordSyncChoice.ONLY_LOCAL:
                return False
            if choice is RecordSyncChoice.REMOTE:
                record = self.service.use_remote(record, check)
        if not await self._save(record, check, quiet=quiet, batch=batch):
            return False
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
                choice = await self._dialog(RecordAddRecoveryScreen(error.recovery, self.service))
                if batch is not None:
                    batch.invalidate()
                if choice is None:
                    return None
                if choice is RecordAddRecoveryChoice.ONLY_LOCAL:
                    # Keep the protection while allowing further local edits.
                    return RecordSyncChoice.ONLY_LOCAL
                if choice is RecordAddRecoveryChoice.RETRY:
                    confirmed = await self._dialog(RecordAddRetryScreen())
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
        choice = await self._dialog(RecordOfflineScreen(reason))
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
    ) -> bool:
        """Report success only for this operation's confirmation or batch enrollment."""
        if self.service is None:
            self._local(record)
            return False
        while True:
            try:
                if batch is None:
                    result = await self.service.save(record, check)
                else:
                    assert check is not None
                    result = await batch.save(record, check)
            except RecordAddRecoveryRequired:
                # Recovery requires its own fresh read and explicit user decisions.
                # No uncertain add may bypass protection just because editing is now offline.
                check, mode = await self._prepare(record, batch=batch)
                if check is None or mode is None:
                    return False
                if check.differences:
                    chosen = await self._review_conflict(record, check, batch=batch)
                    if chosen is None:
                        return False
                    record = chosen
                continue
            except RecordSyncConflict as error:
                check = error.check
                chosen = await self._review_conflict(record, check, batch=batch)
                if chosen is None:
                    return False
                record = chosen
                continue
            except RecordStorageError as error:
                self.app.notify(str(error), severity='error')
                return False
            except (ClientError, OSError, RuntimeError) as error:
                choice = await self._offline(str(error))
                if choice is RecordSyncChoice.LOCAL:
                    self._local(record)
                return False
            except (TypeError, ValueError) as error:
                choice = await self._offline(str(error), remember=False)
                if choice is RecordSyncChoice.LOCAL:
                    self._local(record)
                return False
            if isinstance(result, RecordQueuedUpdate):
                return True
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
                    f'本地记录已保存，仍待推送：{result.error or "存在更新的本地修改"}',
                    severity='warning',
                )
            return result.synced

    async def _review_conflict(
        self, record: MapRecord, check: RecordSyncCheck, *, batch: RecordSyncBatch | None = None
    ) -> MapRecord | None:
        """Review a live conflict without discarding an already saved local edit."""
        assert self.service is not None
        choice = await self._dialog(RecordConflictScreen(record, check, self.service))
        if batch is not None:
            batch.invalidate()
        if choice is None:
            return None
        if choice is RecordSyncChoice.ONLY_LOCAL:
            self._local(record)
            return None
        return (
            self.service.use_remote(record, check) if choice is RecordSyncChoice.REMOTE else record
        )

    async def resolve_batch_conflict(
        self, record: MapRecord, check: RecordSyncCheck
    ) -> MapRecord | None:
        """Choose how a staged batch row should handle a newly detected remote change."""
        assert self.service is not None
        choice = await self._dialog(RecordConflictScreen(record, check, self.service))
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
        self.app.notify(f'本地记录 {saved.record_number or "未编号"} 已保存，待推送。')
