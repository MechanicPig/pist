"""Shared dialogs for offline saves and explicit remote-conflict decisions."""

from enum import StrEnum
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from pist.records.models import MapRecord
from pist.records.sync import RecordSyncCheck, RecordSyncService
from pist.smartsheet.encoding import encode_record_values


class RecordSyncChoice(StrEnum):
    """User consent for a failed read or conflicting remote contents."""

    LOCAL = 'local'
    REMOTE = 'remote'
    ONLY_LOCAL = 'only_local'


class RecordOfflineScreen(ModalScreen[RecordSyncChoice | None]):
    """Choose local persistence or cancellation when remote sync is unavailable."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason

    def compose(self) -> ComposeResult:
        with Vertical(id='record-offline'):
            yield Static('暂时无法同步表格。')
            yield Static(Text(self.reason))
            with Horizontal(classes='record-sync-buttons'):
                yield Button('取消', id='record-offline-cancel')
                yield Button('仅保存本地', id='record-offline-local', variant='primary')

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        choices = {
            'record-offline-local': RecordSyncChoice.LOCAL,
        }
        self.dismiss(choices.get(event.button.id or ''))


class RecordConflictScreen(ModalScreen[RecordSyncChoice | None]):
    """Show baseline, local and remote values before permitting an overwrite."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self, record: MapRecord, check: RecordSyncCheck, service: RecordSyncService
    ) -> None:
        super().__init__()
        self.record = record
        self.check = check
        self.service = service

    def compose(self) -> ComposeResult:
        with Vertical(id='record-conflict'):
            yield Static(
                Text(
                    f'记录 {self.record.record_number or "未编号"} · {self.record.map_name}：远端数据已变化。'
                )
            )
            schema = self.check.snapshot.fields
            local = self.service.fields.comparable(
                encode_record_values(self.record, self.service.fields.writable_fields(schema)),
                schema,
            )
            with VerticalScroll(id='record-conflict-values'):
                for difference in self.check.differences:
                    yield Static(
                        Text(
                            f'{difference.title}\n'
                            f'确认基准：{difference.baseline!r}\n'
                            f'本地内容：{local.get(difference.title)!r}\n'
                            f'远端内容：{difference.remote!r}\n'
                        )
                    )
            with Horizontal(classes='record-sync-buttons'):
                yield Button('取消', id='record-conflict-cancel')
                yield Button('仅保存本地', id='record-conflict-only-local')
                yield Button('采用远端', id='record-conflict-remote')
                yield Button('采用本地', id='record-conflict-local', variant='primary')

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        choices = {
            'record-conflict-local': RecordSyncChoice.LOCAL,
            'record-conflict-remote': RecordSyncChoice.REMOTE,
            'record-conflict-only-local': RecordSyncChoice.ONLY_LOCAL,
        }
        self.dismiss(choices.get(event.button.id or ''))
