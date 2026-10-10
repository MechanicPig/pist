"""Browse and act on local records without consulting installed game content."""

import asyncio
from typing import ClassVar

from aiohttp import ClientError
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, DataTable, Input, Static

from pist.records.batch import RecordSyncBatch
from pist.records.models import MapRecord
from pist.records.store import RecordStorageError, RecordSyncState, StoredRecord
from pist.settings import RecordListMaxWidths, SettingsStore
from pist.smartsheet.report import ManualRecordField
from pist.ui.records.controller import RecordEditorController
from pist.ui.records.editor import RecordEditorScreen
from pist.ui.records.table import RecordTable

SYNC_LABELS = {
    RecordSyncState.UNCHECKED: '待核对',
    RecordSyncState.PENDING: '待同步',
    RecordSyncState.SYNCED: '已同步',
}
RECORD_COLUMNS = {
    'record_number': '编号',
    'map_name': '地图',
    'mod_name': 'Mod',
    'status': '状态',
    'save_slot': '存档',
    'sync_state': '同步',
}


def display_cell(value: str, max_width: int | None) -> Text:
    """Limit free-text cells in terminal columns without changing the source value."""
    text = Text(value, no_wrap=True)
    if max_width is not None:
        text.truncate(max_width, overflow='ellipsis')
    return text


def record_editor(record: MapRecord, fields: tuple[ManualRecordField, ...]) -> RecordEditorScreen:
    """Edit persisted fields without inferring or refreshing game data."""
    return RecordEditorScreen(record, manual_fields=fields)


class RecordListScreen(Screen[RecordListMaxWidths]):
    """Search local history and explicitly edit or retry one selected record."""

    CSS_PATH = '../styles/record_list.tcss'
    BINDINGS: ClassVar = [('escape', 'back', '返回')]

    def __init__(
        self,
        controller: RecordEditorController,
        *,
        max_widths: RecordListMaxWidths,
        settings_store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self._max_widths = max_widths
        self._saved_widths = max_widths
        self._settings_store = settings_store
        self._entries: tuple[StoredRecord, ...] = ()
        self._visible_ids: list[int] = []
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id='record-list'):
            yield Static('本地记录', id='record-list-title')
            with Horizontal(id='record-list-filters'):
                yield Input(placeholder='搜索编号、地图、Mod 或状态', id='record-search')
                yield Checkbox('仅待同步 / 待核对', id='record-pending')
            yield Static('', id='record-list-summary', markup=False)
            yield RecordTable(id='record-table')
            with Horizontal(id='record-list-actions'):
                yield Button('编辑', id='record-list-edit', disabled=True)
                yield Button('同步', id='record-list-sync', disabled=True)
                yield Button('一键同步', id='record-list-sync-all')
                yield Button('刷新', id='record-list-refresh')
                yield Button('返回', id='record-list-back')

    def on_mount(self) -> None:
        self.reload_records()

    @on(RecordTable.ColumnResized)
    def resize_column(self, event: RecordTable.ColumnResized) -> None:
        event.stop()
        self._max_widths = RecordListMaxWidths.model_validate(
            {**self._max_widths.model_dump(), event.key: event.width}
        )
        if event.finished and self._max_widths != self._saved_widths:
            try:
                if self._settings_store is not None:
                    settings = self._settings_store.load()
                    self._settings_store.save(
                        settings.model_copy(update={'record_list_max_widths': self._max_widths})
                    )
            except (OSError, ValueError) as error:
                self._max_widths = self._saved_widths
                self.notify(f'列宽保存失败，已恢复原宽度：{error}', severity='error')
            else:
                self._saved_widths = self._max_widths
        self.filter_records()

    def reload_records(self) -> None:
        """Reload persisted records and keep the current selection when still visible."""
        try:
            entries = self.controller.store.list_records()
        except RecordStorageError as error:
            self.notify(str(error), severity='error')
            return
        self._entries = entries
        self.filter_records()

    @on(Input.Changed, '#record-search')
    @on(Checkbox.Changed, '#record-pending')
    def filter_records(self) -> None:
        """Filter names and display values without using them as identity keys."""
        selected = self.selected_id
        query = self.query_one('#record-search', Input).value.strip().casefold()
        pending = self.query_one('#record-pending', Checkbox).value
        table = self.query_one(DataTable)
        scroll_x, scroll_y = table.scroll_x, table.scroll_y
        table.clear(columns=True)
        widths = self._max_widths.model_dump()
        for key, title in RECORD_COLUMNS.items():
            label = Text(title)
            label.truncate(widths[key] - 1, pad=True, overflow='ellipsis')
            label.append('│')
            table.add_column(label, key=key, width=widths[key])
        self._visible_ids = []
        for entry in self._entries:
            record = entry.record
            assert record.local_id is not None
            if pending and entry.sync_state is RecordSyncState.SYNCED:
                continue
            values = (
                str(record.record_number or '未编号'),
                record.map_name,
                record.mod_name or record.mod_metadata_name or '',
                record.status or '',
                str(record.save_slot) if record.save_slot is not None else '未知',
                SYNC_LABELS[entry.sync_state],
            )
            searchable = (*values, record.mod_metadata_name or '')
            if query and not any(query in value.casefold() for value in searchable):
                continue
            table.add_row(
                *(display_cell(value, widths[key]) for key, value in zip(RECORD_COLUMNS, values)),
                key=str(record.local_id),
            )
            self._visible_ids.append(record.local_id)
        if selected in self._visible_ids:
            table.move_cursor(row=self._visible_ids.index(selected), scroll=False)
        table.scroll_to(x=scroll_x, y=scroll_y, animate=False)
        self.query_one('#record-list-summary', Static).update(
            f'显示 {len(self._visible_ids)} / {len(self._entries)} 条记录'
        )
        self._update_actions()

    @property
    def selected_id(self) -> int | None:
        """Return the selected internal identity, never the displayed formal number."""
        row = self.query_one(DataTable).cursor_row
        return self._visible_ids[row] if 0 <= row < len(self._visible_ids) else None

    @on(DataTable.RowHighlighted)
    def _update_actions(self) -> None:
        disabled = self._busy or self.selected_id is None
        self.query_one('#record-list-edit', Button).disabled = disabled
        self.query_one('#record-list-sync', Button).disabled = disabled
        self.query_one('#record-list-sync-all', Button).disabled = self._busy or not any(
            entry.sync_state is not RecordSyncState.SYNCED for entry in self._entries
        )
        self.query_one('#record-list-refresh', Button).disabled = self._busy
        self.query_one('#record-list-back', Button).disabled = self._busy

    @on(DataTable.RowSelected)
    def select_record(self) -> None:
        self.act_on_record(sync=False)

    @on(Button.Pressed)
    def choose_action(self, event: Button.Pressed) -> None:
        match event.button.id:
            case 'record-list-edit':
                self.act_on_record(sync=False)
            case 'record-list-sync':
                self.act_on_record(sync=True)
            case 'record-list-sync-all':
                self.sync_all()
            case 'record-list-refresh':
                self.reload_records()
            case 'record-list-back':
                self.action_back()

    @work(group='record-list-operation')
    async def act_on_record(self, *, sync: bool) -> None:
        """Act on a freshly read explicit record and refresh after save or cancellation."""
        if self._busy or self.selected_id is None:
            return
        local_id = self.selected_id
        self._busy = True
        self._update_actions()
        try:
            record = self.controller.store.load(local_id)
            if sync:
                await self.controller.sync_record(record)
            else:
                await self.controller.edit_record(record, record_editor)
        except (RecordStorageError, ValueError) as error:
            self.notify(str(error), severity='error')
        finally:
            self._busy = False
            self.reload_records()
            self._update_actions()

    def action_back(self) -> None:
        if not self._busy:
            self.dismiss(self._saved_widths)

    @work(group='record-list-operation')
    async def sync_all(self) -> None:
        """Sync all outstanding records, independent of filters, stopping on unresolved decisions."""
        if self._busy:
            return
        self._busy = True
        self._update_actions()
        completed = 0
        total = 0
        batch = (
            RecordSyncBatch(self.controller.service)
            if self.controller.service is not None
            else None
        )
        try:
            entries = self.controller.store.list_records()
            targets = tuple(
                entry.record.local_id
                for entry in entries
                if entry.sync_state is not RecordSyncState.SYNCED
            )
            total = len(targets)
            for local_id in targets:
                assert local_id is not None
                self.query_one('#record-list-summary', Static).update(
                    f'核对中 {completed + 1} / {total}'
                )
                record = self.controller.store.load(local_id)
                if not await self.controller.sync_record(record, quiet=True, batch=batch):
                    break
                completed += 1
                await asyncio.sleep(0)
            if batch is not None:
                self.query_one('#record-list-summary', Static).update('正在批量提交并回读确认…')
                await batch.finish(self.controller.resolve_batch_conflict)
                completed = sum(
                    self.controller.store.sync_state(id_) is RecordSyncState.SYNCED
                    for id_ in targets
                    if id_ is not None
                )
            self.notify(
                f'一键同步完成：{completed} / {total} 条。'
                if completed == total
                else f'一键同步已停止：已完成 {completed} / {total} 条，剩余记录未处理。',
                severity='information' if completed == total else 'warning',
            )
        except (ClientError, OSError, RuntimeError, TypeError, ValueError) as error:
            self.notify(f'一键同步已停止：{error}', severity='error')
        finally:
            self._busy = False
            self.reload_records()
            self._update_actions()
