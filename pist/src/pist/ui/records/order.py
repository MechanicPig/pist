"""Preview moving a saved record within the player's actual play order."""

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from pist.records.models import MapRecord
from pist.records.order import RecordOrderPlan, plan_record_move


class RecordOrderScreen(ModalScreen[RecordOrderPlan | None]):
    """Confirm a row drop or choose a numbered anchor, previewing every affected number."""

    CSS_PATH = '../styles/record_list.tcss'
    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self,
        records: tuple[MapRecord, ...],
        local_id: int,
        *,
        target_id: int | None = None,
        before: bool = True,
    ) -> None:
        super().__init__()
        self.records = records
        self.local_id = local_id
        self._target_id = target_id
        self._before = before
        self._plan = (
            None
            if target_id is None
            else plan_record_move(records, local_id, target_id, before=before)
        )

    def compose(self) -> ComposeResult:
        record = next(record for record in self.records if record.local_id == self.local_id)
        with Vertical(id='record-order'):
            yield Static(f'调整顺序：{record.record_number} · {record.map_name}', markup=False)
            if self._target_id is None:
                yield Static('输入目标记录编号，将当前记录移到它之前或之后。')
                with Horizontal(id='record-order-controls'):
                    yield Input(placeholder='目标编号', type='integer', id='record-order-target')
                    yield Select(
                        (('之前', True), ('之后', False)),
                        value=True,
                        allow_blank=False,
                        id='record-order-position',
                    )
            else:
                target = next(
                    record for record in self.records if record.local_id == self._target_id
                )
                yield Static(
                    f'移到 {target.record_number} · {target.map_name} '
                    f'{"之前" if self._before else "之后"}，是否确认？',
                    markup=False,
                )
            with VerticalScroll(id='record-order-preview'):
                yield Static(self._plan_text(), id='record-order-changes', markup=False)
            yield Static('仅调整编号；地图内容、存档归属与记录关联不变。保存后尝试同步编号。')
            with Horizontal(id='record-order-actions'):
                yield Button('取消', id='record-order-cancel')
                yield Button(
                    '确认调整',
                    id='record-order-save',
                    variant='primary',
                    disabled=self._plan is None or not self._plan.changes,
                )

    def _plan_text(self) -> str:
        """Describe proposed number changes without modifying the stored order."""
        if self._plan is None:
            return ''
        return '\n'.join(
            f'{change.old_number} → {change.new_number} · {change.map_name}'
            for change in self._plan.changes
        )

    @on(Input.Changed, '#record-order-target')
    @on(Select.Changed, '#record-order-position')
    def preview(self) -> None:
        if not self.is_mounted:
            return
        self._plan = None
        number = self.query_one('#record-order-target', Input).value
        try:
            target_number = int(number)
            target = next(
                (record for record in self.records if record.record_number == target_number), None
            )
            if target is None or target.local_id is None:
                raise ValueError('找不到这个编号，请输入已登记记录的编号。')
            before = self.query_one('#record-order-position', Select).value
            assert isinstance(before, bool)
            plan = plan_record_move(self.records, self.local_id, target.local_id, before=before)
            if not plan.changes:
                raise ValueError('当前记录已经在这个位置，无需调整。')
            self._plan = plan
            text = self._plan_text()
        except ValueError as error:
            text = str(error) if number else '请输入目标记录编号。'
        self.query_one('#record-order-changes', Static).update(text)
        self.query_one('#record-order-save', Button).disabled = self._plan is None

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == 'record-order-save' and self._plan is not None:
            self.dismiss(self._plan)  # pyright: ignore[reportUnusedCallResult]
        elif event.button.id == 'record-order-cancel':
            self.dismiss(None)  # pyright: ignore[reportUnusedCallResult]
