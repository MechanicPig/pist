"""Shared record editing and supporting dialogs."""

import csv
from calendar import monthrange
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from io import StringIO
from types import MappingProxyType
from typing import ClassVar

from pydantic import ValidationError
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from berries.entities.classification import CollectedEntityRuleIssue, CollectedEntityRuleIssueStatus
from berries.game.binmap import AttrValue
from berries.gamebanana import GameBananaSubmission
from pist.records.fields import RECORD_ATTRIBUTES
from pist.records.models import MapRecord, MapRecordProgress
from pist.smartsheet.fields import load_sheet_fields
from pist.smartsheet.models import FieldType
from pist.smartsheet.report import ManualRecordField
from pist.types import CellValue
from pist.ui.mouse import DOUBLE_CLICK_COUNT, LEFT_MOUSE_BUTTON

type AuthorSource = GameBananaSubmission | str | None

RECORD_FIELD_LABELS = {
    'Mod元数据名': 'Mod 元数据名',
    'Mod名': 'Mod 名',
    '标注难度子阶': '难度子阶',
    'SL使用': 'SL 使用',
}

RECORD_FIELD_ORDER = (
    'Mod元数据名',
    'Mod名',
    '地图名',
    '作者',
    '用时',
    '死亡数',
    '红草莓数',
    '月莓数',
    '磁带',
    '水晶之心',
    '主房间数',
    '状态',
    '体感难度',
    '难度子阶',
    '标注难度',
    '标注难度子阶',
    '起始日期',
    '结束日期',
    'SL使用',
    '评分',
    '标签',
    '备注',
)

COLLECTED_ENTITY_ISSUE_REASONS = MappingProxyType(
    {
        CollectedEntityRuleIssueStatus.UNMATCHED: '尚无匹配规则',
        CollectedEntityRuleIssueStatus.EXCLUDED: '与现有排除规则冲突',
        CollectedEntityRuleIssueStatus.UNREVIEWED_VARIANT: '出现尚未审查的属性变体',
    }
)


def _tags_text(tags: tuple[str, ...]) -> str:
    """Represent tags on one editable line, quoting commas inside individual tags."""
    output = StringIO()
    csv.writer(output, lineterminator='\n').writerow(tags)
    return output.getvalue().removesuffix('\n')


def _reference_summary(title: str, content: str, *, limit: int = 120) -> str:
    value = ' '.join(content.split())
    if len(value) > limit:
        value = f'{value[:limit].rstrip()}…'
    return f'[b]{title}[/b]\n{value}'


def _entity_attrs_summary(attrs: Mapping[str, AttrValue] | None) -> str:
    return (
        '无'
        if not attrs
        else ' · '.join(f'{name} = {value!r}' for name, value in sorted(attrs.items()))
    )


def _entity_meta_summary(meta: Mapping[str, AttrValue] | None) -> str:
    return '' if not meta else f'\n地图元数据：{_entity_attrs_summary(meta)}'


class RecordSIDInput(Input):
    """Display a read-only SID with cursor navigation and selection for copying."""

    def replace(self, text: str, start: int, end: int) -> None:
        """Keep the SID unchanged when typing, deleting, cutting or pasting."""


class DatePickerScreen(ModalScreen[str | None]):
    """Choose one optional ISO date from a compact month calendar."""

    CSS_PATH = '../styles/record_editor.tcss'

    def __init__(self, selected: date | None = None) -> None:
        super().__init__()
        selected = selected or datetime.now(UTC).astimezone().date()
        self._year = selected.year
        self._month = selected.month

    def compose(self) -> ComposeResult:
        with Vertical(id='date-picker'):
            with Horizontal(id='date-picker-header'):
                yield Button('‹', id='date-picker-previous')
                yield Static(f'{self._year} 年 {self._month} 月', id='date-picker-month')
                yield Button('›', id='date-picker-next')
            with Grid(id='date-picker-days'):
                for weekday in '一二三四五六日':
                    yield Static(weekday, classes='date-picker-weekday')
                first_weekday, days = monthrange(self._year, self._month)
                for _ in range(first_weekday):
                    yield Static('')
                for day in range(1, days + 1):
                    yield Button(str(day), id=f'date-picker-day-{day}')
            with Horizontal(id='date-picker-actions'):
                yield Button('取消', id='date-picker-cancel')

    @on(Button.Pressed)
    def select_date(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == 'date-picker-cancel':
            self.dismiss(None)
        elif button_id == 'date-picker-previous':
            self._year, self._month = (
                (self._year - 1, 12) if self._month == 1 else (self._year, self._month - 1)
            )
            self.refresh(recompose=True)
        elif button_id == 'date-picker-next':
            self._year, self._month = (
                (self._year + 1, 1) if self._month == 12 else (self._year, self._month + 1)
            )
            self.refresh(recompose=True)
        elif button_id is not None and button_id.startswith('date-picker-day-'):
            self.dismiss(
                date(
                    self._year, self._month, int(button_id.removeprefix('date-picker-day-'))
                ).isoformat()
            )


class RecordRefSummary(Static):
    """Compact supplemental record information that opens in full on double-click."""

    class Opened(Message):
        """The player requested the complete reference text."""

        def __init__(self, title: str, content: str) -> None:
            super().__init__()
            self.title = title
            self.content = content

    def __init__(
        self,
        title: str,
        content: str,
        *,
        expand: bool = False,
        shrink: bool = False,
        markup: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            _reference_summary(title, content),
            expand=expand,
            shrink=shrink,
            markup=markup,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self._title = title
        self._content = content

    async def _on_click(self, event: events.Click) -> None:
        if event.button == LEFT_MOUSE_BUTTON and event.chain == DOUBLE_CLICK_COUNT:
            event.stop()
            self.post_message(self.Opened(self._title, self._content))


class RecordRefScreen(ModalScreen[None]):
    """Show the complete GameBanana introduction or collab tag text."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '关闭')]

    def __init__(self, title: str, content: str) -> None:
        super().__init__()
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical(id='record-ref-dialog'):
            yield Static(self._title, classes='record-ref-title')
            with VerticalScroll(id='record-ref-content'):
                yield Static(self._content)
            with Horizontal(id='record-ref-actions'):
                yield Button('关闭', id='record-ref-close', variant='primary')

    @on(Button.Pressed, '#record-ref-close')
    def close(self) -> None:
        self.dismiss()


class CollectedEntityRulesResult(StrEnum):
    """The user's next step after reviewing a collected-entity rule warning."""

    CLOSE = 'close'
    REFRESH = 'refresh'


class CollectedEntityRulesScreen(ModalScreen[CollectedEntityRulesResult]):
    """Explain why a record cannot proceed until collected entities have rules."""

    CSS_PATH = '../styles/record_editor.tcss'

    def __init__(self, issues: tuple[CollectedEntityRuleIssue, ...]) -> None:
        super().__init__()
        self._issues = issues

    def compose(self) -> ComposeResult:
        with Vertical(id='collected-entity-rules'):
            yield Static('已收集实体需要补充规则', classes='record-ref-title')
            yield Static('请在实体审计中确认下列实体的类别或排除条件，再点击“刷新规则”重新检查。')
            with VerticalScroll(id='collected-entity-rules-content'):
                for issue in self._issues:
                    entity = issue.entity_name or '未知实体'
                    try:
                        reason = COLLECTED_ENTITY_ISSUE_REASONS[issue.status]
                    except KeyError as error:
                        raise ValueError(
                            f'Unexpected blocking collected-entity issue: {issue.status}'
                        ) from error
                    yield Static(
                        f'{issue.collected_id.room}:{issue.collected_id.entity_id}\n'
                        f'{entity} · {reason}\n属性：{_entity_attrs_summary(issue.attrs)}'
                        f'{_entity_meta_summary(issue.meta)}',
                        classes='collected-entity-rule-issue',
                    )
            with Horizontal(id='collected-entity-rules-actions'):
                yield Button('关闭', id='collected-entity-rules-close')
                yield Button('刷新规则', id='collected-entity-rules-refresh', variant='primary')

    @on(Button.Pressed, '#collected-entity-rules-close')
    def close(self) -> None:
        self.dismiss(CollectedEntityRulesResult.CLOSE)

    @on(Button.Pressed, '#collected-entity-rules-refresh')
    def refresh_rules(self) -> None:
        self.dismiss(CollectedEntityRulesResult.REFRESH)


class RecordAuthorField(Static):
    """An editable author value in the record-information grid."""

    class Clicked(Message):
        """The player wants to revise the selected credited authors."""

    async def _on_click(self, event: events.Click) -> None:
        if event.button == LEFT_MOUSE_BUTTON:
            event.stop()
            self.post_message(self.Clicked())


class RecordRouteField(Static):
    """A main-room-count value that opens the map route editor."""

    class Clicked(Message):
        """The player wants to edit the map route."""

    async def _on_click(self, event: events.Click) -> None:
        if event.button == LEFT_MOUSE_BUTTON:
            event.stop()
            self.post_message(self.Clicked())


class RecordEditorScreen(ModalScreen[MapRecord | None]):
    """Edit one local record before writing it to local storage."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self,
        record: MapRecord,
        *,
        manual_fields: tuple[ManualRecordField, ...] = (),
        reference: tuple[str, str] | None = None,
        author_source: AuthorSource = None,
        collab_tags: str | None = None,
        field_hints: Mapping[str, str] | None = None,
        edit_route: Callable[[], None] | None = None,
        progress: MapRecordProgress | None = None,
    ) -> None:
        super().__init__()
        self._record = record
        fields = {
            spec.title: ManualRecordField(spec.title, spec.type)
            for spec in load_sheet_fields().fields
        }
        fields.update((field.title, field) for field in manual_fields)
        self._manual_fields = tuple(fields[title] for title in RECORD_FIELD_ORDER)
        self._reference = reference
        self._author_source = author_source
        self._collab_tags = collab_tags
        self._field_hints = {} if field_hints is None else field_hints
        self._edit_route = edit_route
        self._progress = progress

    def compose(self) -> ComposeResult:
        with Vertical(id='record-confirm'):
            with VerticalScroll(id='record-confirm-content'):
                if self._reference is not None:
                    title, content = self._reference
                    yield RecordRefSummary(
                        title,
                        content,
                        id='record-ref-summary',
                        classes='record-ref-summary',
                    )
                with Grid(id='record-fields'):
                    yield from self._record_field_widgets()
            with Horizontal(id='record-confirm-actions'):
                yield Button('取消', id='record-confirm-cancel')
                yield Button('保存', id='record-confirm-save', variant='primary')

    def on_mount(self) -> None:
        self._update_field_layout()

    def on_resize(self, event: events.Resize) -> None:
        self._update_field_layout()

    def _update_field_layout(self) -> None:
        for grid in self.query('#record-fields'):
            grid.set_class(self.size.width < 120, 'record-fields-narrow')

    @on(RecordRefSummary.Opened)
    def open_record_ref(self, event: RecordRefSummary.Opened) -> None:
        """Open the complete supplemental reference without leaving the record."""
        self.app.push_screen(RecordRefScreen(event.title, event.content))

    @on(RecordAuthorField.Clicked)
    def edit_authors(self) -> None:
        """Reopen the source-specific author selector from the record grid."""
        if isinstance(self._author_source, GameBananaSubmission):
            self.app.push_screen(
                AuthorSelectionScreen(self._author_source, selected_authors=self._record.authors),
                self._set_authors,
            )
        elif isinstance(self._author_source, str):
            self.app.push_screen(
                DialogAuthorSelectionScreen(self._author_source, authors=self._record.authors),
                self._set_authors,
            )

    @on(RecordRouteField.Clicked)
    def edit_route(self) -> None:
        """Open the non-blocking route editor for this record's map."""
        if self._edit_route is not None:
            self._edit_route()

    def _set_authors(self, authors: tuple[str, ...] | None) -> None:
        if authors is None:
            return
        self._record = self._record.model_copy(update={'authors': authors})
        field = self._manual_field('作者')
        assert field is not None
        self.query_one(f'#{self._manual_field_id(field)}', TextArea).load_text('\n'.join(authors))
        author_field = self.query_one(RecordAuthorField)
        author_field.update('、'.join(authors) or '单击选择作者')
        if authors:
            author_field.remove_class('record-author-placeholder')
        else:
            author_field.add_class('record-author-placeholder')

    def _record_field_widgets(self) -> ComposeResult:
        """Group map information, experience data and personal assessments for editing."""
        yield Static('地图信息', classes='record-section-title')
        yield from self._manual_field_widgets(('Mod名', 'Mod元数据名', '地图名', '作者'))
        yield self._label('SID')
        yield RecordSIDInput(self._record.sid or '', id='record-sid', compact=True)
        yield self._label('合集标签')
        yield self._field_value('合集标签参考', self._collab_tags)
        yield self._label('更新时间')
        yield self._updated_at_control()
        yield self._label('Mod 链接')
        yield Input(self._record.mod_url or '', id='record-mod-url', compact=True)
        if self._author_source is not None:
            yield self._label('作者来源')
            yield RecordAuthorField('单击选择作者')
            yield Static('', classes='record-field-spacer')
            yield Static('', classes='record-field-spacer')

        yield Static('初见记录', classes='record-section-title')
        yield from self._pair(
            '存档编号',
            self._record.save_slot,
            '存档通关参考',
            self._progress.label if self._progress else self._record.completed,
        )
        yield from self._manual_field_widgets(
            (
                '起始日期',
                '结束日期',
                '用时',
                '死亡数',
                '红草莓数',
                '月莓数',
                '磁带',
                '水晶之心',
                '主房间数',
                '状态',
            )
        )
        if self._edit_route is not None:
            yield self._label('路线')
            yield RecordRouteField('单击编辑路线')
            yield Static('', classes='record-field-spacer')
            yield Static('', classes='record-field-spacer')
        yield from self._manual_field_widgets(('SL使用',))
        yield self._label('视频链接')
        yield Input(self._record.video_url or '', id='record-video-url', compact=True)

        yield Static('评价与备注', classes='record-section-title')
        yield from self._manual_field_widgets(
            (
                '体感难度',
                '难度子阶',
                '标注难度',
                '标注难度子阶',
                '评分',
                '标签',
            )
        )
        yield from self._manual_field_widgets(('备注',))

    def _manual_field_widgets(self, titles: tuple[str, ...]) -> ComposeResult:
        """Render editable fields with their validation hints in the given order."""
        for title in titles:
            field = self._manual_field(title)
            assert field is not None
            yield self._label(field.title)
            control = self._manual_control(
                field, classes='record-wide-control' if title == '备注' else None
            )
            control.tooltip = self._field_hints.get(field.title)
            if field.title in self._field_hints:
                control.add_class('record-field-warning')
            yield control

    def _pair(
        self, left_label: str, left_value: object, right_label: str, right_value: object
    ) -> ComposeResult:
        yield self._label(left_label)
        yield self._field_value(left_label, left_value)
        yield self._label(right_label)
        yield self._field_value(right_label, right_value)

    @staticmethod
    def _label(value: str) -> Static:
        """Render a bold field label without affecting its paired value."""
        return Static(RECORD_FIELD_LABELS.get(value, value), classes='record-field-label')

    def _field_value(self, title: str, value: object) -> Static:
        """Render a generated field value or a visible explanation for a missing value."""
        hint = self._field_hints.get(title) if value is None else None
        field = Static(self._display_value(value) if hint is None else hint)
        if hint is not None:
            field.add_class('record-field-warning')
        return field

    def _manual_field(self, title: str) -> ManualRecordField | None:
        return next((field for field in self._manual_fields if field.title == title), None)

    def _manual_control(self, field: ManualRecordField, *, classes: str | None = None) -> Widget:
        field_id = self._manual_field_id(field)
        value = self._table_value(field.title)
        match field.field_type:
            case FieldType.DATE:
                return Horizontal(
                    Input(
                        self._display_value(value),
                        placeholder='YYYY-MM-DD',
                        id=field_id,
                        compact=True,
                    ),
                    Button(
                        '选择日期',
                        id=f'{field_id}-picker',
                        classes='record-date-picker',
                        compact=True,
                    ),
                    classes=f'record-date-control {classes or ""}'.strip(),
                )
            case FieldType.CHECKBOX:
                return Select(
                    (('有', True), ('无', False)),
                    value=value,
                    allow_blank=False,
                    id=field_id,
                    compact=True,
                )
            case FieldType.MULTI_SELECT:
                if field.title == '标签':
                    return Input(
                        _tags_text(value) if isinstance(value, tuple) else '',
                        id=field_id,
                        placeholder='多个标签用英文逗号分隔',
                        compact=True,
                    )
                return TextArea(
                    '\n'.join(value) if isinstance(value, tuple) else '',
                    id=field_id,
                    classes='record-list-control',
                    compact=True,
                )
            case FieldType.SINGLE_SELECT:
                options = field.options
                if not options:
                    return Input(self._display_value(value), id=field_id, compact=True)
                if isinstance(value, str) and value and value not in options:
                    options = (*options, value)
                if isinstance(value, str):
                    return Select(
                        ((option, option) for option in options),
                        prompt=f'选择{RECORD_FIELD_LABELS.get(field.title, field.title)}',
                        value=value,
                        id=field_id,
                        classes=classes,
                        compact=True,
                    )
                return Select(
                    ((option, option) for option in options),
                    prompt=f'选择{RECORD_FIELD_LABELS.get(field.title, field.title)}',
                    id=field_id,
                    classes=classes,
                    compact=True,
                )
            case FieldType.NUMBER:
                return Input(
                    self._display_value(value),
                    placeholder='未统计' if field.title == '主房间数' else '整数',
                    type='integer',
                    id=field_id,
                    classes=classes,
                    validators=None,
                    compact=True,
                )
            case FieldType.TEXT | FieldType.LINK:
                return Input(self._display_value(value), id=field_id, classes=classes, compact=True)
            case _:
                return Static('')

    def _updated_at_control(self) -> Horizontal:
        value = (
            '' if self._record.mod_updated_at is None else self._record.mod_updated_at.isoformat()
        )
        return Horizontal(
            Input(value=value, placeholder='YYYY-MM-DD', id='record-updated-at', compact=True),
            Button(
                '选择日期',
                id='record-updated-at-picker',
                classes='record-date-picker',
                compact=True,
            ),
            classes='record-date-control',
        )

    def _table_value(self, title: str) -> CellValue | None:
        value = getattr(self._record, RECORD_ATTRIBUTES[title])
        if isinstance(value, date):
            return value.isoformat()
        return value

    @staticmethod
    def _display_value(value: object) -> str:
        match value:
            case None:
                return ''
            case True:
                return '✓'
            case False:
                return '✗'
            case _:
                return str(value)

    @on(Button.Pressed)
    def confirm(self, event: Button.Pressed) -> None:
        """Return the record only after the user explicitly confirms it."""
        if event.button.id == 'record-confirm-cancel':
            self.dismiss(None)
            return
        if event.button.id == 'record-confirm-save':
            video_url = self.query_one('#record-video-url', Input).value.strip() or None
            try:
                MapRecord.validate_http_url(video_url)
            except ValueError:
                self.notify('视频链接请使用完整的 HTTP 或 HTTPS 地址。', severity='warning')
                return
            manual_values = self._manual_values()
            if manual_values is None:
                return
            try:
                updated_at = self._updated_at()
            except ValueError:
                self.notify('更新时间请使用 YYYY-MM-DD 格式。', severity='warning')
                return
            data = self._record.model_dump()
            data.update(manual_values)
            data.update(
                mod_updated_at=updated_at,
                video_url=video_url,
                mod_url=self.query_one('#record-mod-url', Input).value.strip() or None,
            )
            try:
                result = MapRecord.model_validate(data)
            except ValidationError as error:
                titles = {attribute: title for title, attribute in RECORD_ATTRIBUTES.items()}
                issues = '；'.join(
                    f'{titles.get(str(issue["loc"][0]), str(issue["loc"][0]))}：{issue["msg"]}'
                    for issue in error.errors(include_url=False, include_input=False)
                )
                self.notify(f'记录内容无效：{issues}', severity='warning')
                return
            self.dismiss(result)

    @on(Button.Pressed)
    def open_date_picker(self, event: Button.Pressed) -> None:
        """Open a calendar for the date input whose picker button was clicked."""
        button_id = event.button.id
        if button_id is None or not button_id.endswith('-picker'):
            return
        field_id = button_id.removesuffix('-picker')
        current = self.query_one(f'#{field_id}', Input).value
        try:
            selected = date.fromisoformat(current) if current else None
        except ValueError:
            self.notify('日期请使用 YYYY-MM-DD 格式。', severity='warning')
            return
        event.stop()
        self.app.push_screen(
            DatePickerScreen(selected),
            lambda value: self._set_date_value(field_id, value),
        )

    def _set_date_value(self, field_id: str, value: str | None) -> None:
        if value is not None:
            self.query_one(f'#{field_id}', Input).value = value

    def _updated_at(self) -> date | None:
        value = self.query_one('#record-updated-at', Input).value.strip()
        if not value:
            return None
        return date.fromisoformat(value)

    def _manual_field_id(self, field: ManualRecordField) -> str:
        """Return a DOM-safe ID for a selected inspected field."""
        return f'record-manual-{self._manual_fields.index(field)}'

    def _manual_values(self) -> dict[str, CellValue | date | None] | None:
        """Read all editable values, preserving explicit clearing and false values."""
        values: dict[str, CellValue | date | None] = {}
        for field in self._manual_fields:
            attribute = RECORD_ATTRIBUTES[field.title]
            control = self.query_one(f'#{self._manual_field_id(field)}')
            try:
                if isinstance(control, TextArea):
                    if field.field_type == FieldType.TEXT:
                        values[attribute] = control.text or None
                        continue
                    previous = getattr(self._record, attribute)
                    if control.text == '\n'.join(previous):
                        values[attribute] = previous
                        continue
                    values[attribute] = tuple(
                        dict.fromkeys(
                            item.strip() for item in control.text.splitlines() if item.strip()
                        )
                    )
                elif isinstance(control, Select):
                    selected = control.value
                    values[attribute] = selected if isinstance(selected, (str, bool)) else None
                elif isinstance(control, Input):
                    text = control.value
                    if field.field_type == FieldType.MULTI_SELECT:
                        previous = getattr(self._record, attribute)
                        if text == _tags_text(previous):
                            values[attribute] = previous
                        else:
                            items = next(csv.reader([text], strict=True)) if text else []
                            values[attribute] = tuple(
                                dict.fromkeys(item.strip() for item in items if item.strip())
                            )
                    elif not text.strip():
                        values[attribute] = None
                    elif field.field_type == FieldType.DATE:
                        values[attribute] = date.fromisoformat(text)
                    elif field.field_type == FieldType.NUMBER:
                        if attribute == 'n_main_rooms' and int(text) <= 0:
                            self.notify('主房间数请填写正整数；未统计请留空。', severity='warning')
                            return None
                        values[attribute] = int(text)
                    else:
                        values[attribute] = text
            except ValueError, csv.Error:
                self.notify(f'{field.title}格式不正确。', severity='warning')
                return None
        return values


class RecordSaveModeScreen(ModalScreen[int | None]):
    """Choose a new experience record or the explicit existing record to update."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, candidates: tuple[MapRecord, ...], *, save_slot: int | None) -> None:
        super().__init__()
        self._save_slot = save_slot
        self._same_slot = tuple(
            record
            for record in candidates
            if save_slot is not None and record.save_slot == save_slot
        )
        self._candidates = self._same_slot + tuple(
            record for record in candidates if save_slot is None or record.save_slot != save_slot
        )

    @staticmethod
    def _slot_label(save_slot: int | None) -> str:
        return '存档未知' if save_slot is None else f'存档 {save_slot}'

    def compose(self) -> ComposeResult:
        with Vertical(id='record-sync-mode'):
            yield Static(
                '此地图已有同存档记录，请选择新增或更新。'
                if self._same_slot
                else '此地图已有其他存档或存档未知的记录，默认新增；更新需明确选择原记录。'
            )
            yield Select(
                (
                    (
                        (
                            f'记录 {record.record_number or "未编号"} · '
                            f'{self._slot_label(record.save_slot)} · '
                            f'{record.created_at.astimezone().date()}'
                        ),
                        record.local_id,
                    )
                    for record in self._candidates
                ),
                value=self._same_slot[0].local_id if self._same_slot else Select.NULL,
                allow_blank=not self._same_slot,
                prompt='选择要更新的原记录（可选）',
                id='record-save-target',
            )
            yield Static('', id='record-save-warning')
            with Horizontal(id='record-sync-actions'):
                yield Button('取消', id='record-save-cancel')
                yield Button(
                    '新增记录',
                    id='record-save-add',
                    variant='default' if self._same_slot else 'primary',
                )
                yield Button(
                    '更新原记录',
                    id='record-save-update',
                    variant='primary' if self._same_slot else 'default',
                    disabled=not self._same_slot,
                )

    def on_mount(self) -> None:
        self.query_one(
            '#record-save-update' if self._same_slot else '#record-save-add', Button
        ).focus()

    @on(Select.Changed, '#record-save-target')
    def select_target(self, event: Select.Changed) -> None:
        button = self.query_one('#record-save-update', Button)
        button.disabled = event.value is Select.NULL
        warning = ''
        if isinstance(event.value, int):
            selected = next(record for record in self._candidates if record.local_id == event.value)
            if selected.save_slot is None or self._save_slot is None:
                warning = '存档归属无法确认；更新将覆盖游戏数据，不累加，并替换为当前存档编号。'
            elif selected.save_slot != self._save_slot:
                warning = (
                    f'跨存档更新：存档 {selected.save_slot} → {self._slot_label(self._save_slot)}。'
                    '游戏数据只覆盖、不累加，原记录编号与手填信息保留。'
                )
        button.label = '覆盖原记录' if warning else '更新原记录'
        message = self.query_one('#record-save-warning', Static)
        message.update(warning)
        message.display = bool(warning)

    @on(Button.Pressed)
    def choose_record(self, event: Button.Pressed) -> None:
        if event.button.id == 'record-save-add':
            self.dismiss(0)
        elif event.button.id == 'record-save-update':
            selected = self.query_one('#record-save-target', Select).value
            assert isinstance(selected, int)
            self.dismiss(selected)
        elif event.button.id == 'record-save-cancel':
            self.dismiss(None)


class AuthorSelectionScreen(ModalScreen[tuple[str, ...] | None]):
    """Let the player select author entries from raw GameBanana Credits."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self, submission: GameBananaSubmission, *, selected_authors: tuple[str, ...] = ()
    ) -> None:
        super().__init__()
        self._choices = submission.author_choices
        self._selected_authors = frozenset(selected_authors)

    def compose(self) -> ComposeResult:
        with Vertical(id='author-select'):
            yield Static('选择要记为作者的 Credits 条目：')
            with VerticalScroll(id='author-select-list'):
                for i, (name, group, role) in enumerate(self._choices):
                    label = f'{name}  [{group}]'
                    if role:
                        label += f'  [{role}]'
                    yield Checkbox(
                        Text(label),
                        value=name in self._selected_authors,
                        id=f'record-author-{i}',
                        compact=True,
                    )
            with Horizontal(id='author-select-actions'):
                yield Button('取消', id='author-select-cancel')
                yield Button('继续', id='author-select-save', variant='primary')

    @on(Button.Pressed)
    def confirm(self, event: Button.Pressed) -> None:
        """Return only the names explicitly selected by the player."""
        if event.button.id == 'author-select-cancel':
            self.dismiss(None)
            return
        authors = tuple(
            dict.fromkeys(
                name
                for i, (name, _, _) in enumerate(self._choices)
                if self.query_one(f'#record-author-{i}', Checkbox).value
            )
        )
        self.dismiss(authors)


class DialogAuthorSelectionScreen(ModalScreen[tuple[str, ...] | None]):
    """Select one or more arbitrary author-name spans from Dialog text."""

    CSS_PATH = '../styles/record_editor.tcss'

    BINDINGS: ClassVar = [
        ('ctrl+enter', 'add_author', '添加选区'),
        ('escape', 'dismiss', '取消'),
    ]

    def __init__(self, text: str, *, authors: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._authors = list(authors)
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(id='dialog-author-select'):
            yield Static('在原文中选中作者片段后添加；可重复选择。')
            yield TextArea(self._text, read_only=True, id='dialog-author-text')
            yield Vertical(id='dialog-author-list')
            with Horizontal(id='dialog-author-actions'):
                yield Button('取消', id='dialog-author-cancel')
                yield Button('添加选区', id='dialog-author-add')
                yield Button('继续', id='dialog-author-save', variant='primary')

    async def on_mount(self) -> None:
        if self._authors:
            await self._refresh_authors()

    @on(Button.Pressed)
    async def confirm(self, event: Button.Pressed) -> None:
        """Add a selected span or return the accumulated author names."""
        button_id = event.button.id
        if button_id == 'dialog-author-cancel':
            self.dismiss(None)
        elif button_id == 'dialog-author-add':
            await self.action_add_author()
        elif button_id is not None and button_id.startswith('dialog-author-remove-'):
            index = int(button_id.removeprefix('dialog-author-remove-'))
            self._sync_authors(skip_index=index)
            await self._refresh_authors()
        else:
            self._sync_authors()
            self.dismiss(tuple(self._authors))

    async def action_add_author(self) -> None:
        """Add the currently selected Dialog text as one editable author."""
        selected = self.query_one(TextArea).selected_text.strip()
        if selected and selected not in self._authors:
            self._authors.append(selected)
            await self._refresh_authors()
        elif not selected:
            self.notify('请先在原文中选中一个作者片段。', severity='warning')

    async def _refresh_authors(self) -> None:
        """Render each selected author with an editable field and remove button."""
        author_list = self.query_one('#dialog-author-list', Vertical)
        await author_list.remove_children()
        await author_list.mount(
            *(
                Horizontal(
                    Input(author, id=f'dialog-author-{i}', compact=True),
                    Button('×', id=f'dialog-author-remove-{i}', compact=True),
                    classes='dialog-author-row',
                )
                for i, author in enumerate(self._authors)
            )
        )

    def _sync_authors(self, *, skip_index: int | None = None) -> None:
        """Keep edits made in the selected-author fields before rebuilding or saving."""
        self._authors = list(
            dict.fromkeys(
                value
                for i in range(len(self._authors))
                if i != skip_index
                if (value := self.query_one(f'#dialog-author-{i}', Input).value.strip())
            )
        )
