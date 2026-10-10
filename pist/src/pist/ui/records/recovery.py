"""Shared recovery decisions for an add whose remote identity was lost."""

from enum import Enum
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static

from pist.records.sync import RecordAddRecovery, RecordSyncService


class RecordAddRecoveryChoice(Enum):
    """Request a separate confirmation before allowing another add."""

    RETRY = 'retry'
    ONLY_LOCAL = 'only_local'


class RecordAddRecoveryScreen(ModalScreen[str | RecordAddRecoveryChoice | None]):
    """Show all unbound same-map candidates without selecting one automatically."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, recovery: RecordAddRecovery, service: RecordSyncService) -> None:
        super().__init__()
        self.recovery = recovery
        self.service = service

    def compose(self) -> ComposeResult:
        with Vertical(id='record-add-recovery'):
            yield Static(
                Text(
                    f'上次新增结果未确认：{self.recovery.attempt.mod_metadata_name} / '
                    f'{self.recovery.attempt.map_name}\n'
                    '请选择实际新增的远端行。关联后仍需核对数据，不会直接认定已同步。'
                )
            )
            with VerticalScroll(id='record-add-candidates'):
                for row in self.recovery.candidates:
                    values = self.service.fields.comparable(
                        row.values, self.recovery.snapshot.fields
                    )
                    details = '\n'.join(f'{title}：{values.get(title)!r}' for title in values)
                    yield Static(Text(f'远端 ID：{row.record_id}\n{details}\n'))
                if not self.recovery.candidates:
                    yield Static('没有未绑定的同地图候选行。这不代表新增一定失败。')
            yield Select(
                [
                    (row.record_id, row.record_id)
                    for row in self.recovery.candidates
                    if row.record_id is not None
                ],
                prompt='选择远端记录 ID',
                id='record-add-target',
                disabled=not self.recovery.candidates,
            )
            with Horizontal(classes='record-sync-buttons'):
                yield Button('取消', id='record-add-cancel')
                yield Button('仅保存本地', id='record-add-local')
                yield Button(
                    '确认未新增…', id='record-add-retry', disabled=bool(self.recovery.candidates)
                )
                yield Button('关联选中行', id='record-add-bind', variant='primary', disabled=True)

    @on(Select.Changed, '#record-add-target')
    def select_target(self, event: Select.Changed) -> None:
        self.query_one('#record-add-bind', Button).disabled = event.value is Select.NULL

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        match event.button.id:
            case 'record-add-bind':
                target = self.query_one('#record-add-target', Select).value
                if isinstance(target, str):
                    self.dismiss(target)
            case 'record-add-retry':
                self.dismiss(RecordAddRecoveryChoice.RETRY)
            case 'record-add-local':
                self.dismiss(RecordAddRecoveryChoice.ONLY_LOCAL)
            case _:
                self.dismiss(None)


class RecordAddRetryScreen(ModalScreen[bool]):
    """Require explicit confirmation before removing uncertain-add protection."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss(False)', '取消')]

    def compose(self) -> ComposeResult:
        with Vertical(id='record-add-retry-confirm'):
            yield Static(
                '只有确认上次请求已经结束，并检查远端确实没有新增记录后，才能允许重试。\n'
                '远端行可能被改名而未出现在候选中；判断错误会产生重复记录。'
            )
            with Horizontal(classes='record-sync-buttons'):
                yield Button('取消', id='record-add-retry-cancel', variant='primary')
                yield Button(
                    '确认未新增，允许重试', id='record-add-retry-confirmed', variant='warning'
                )

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == 'record-add-retry-confirmed')
