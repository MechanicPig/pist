"""Record editing and record-specific dialogs for the Mod browser."""

from calendar import monthrange
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from pist.entities.classification import CollectedEntityRuleIssue, CollectedEntityRuleIssueStatus
from pist.game.binmap import AttrValue
from pist.gamebanana import GameBananaSubmission
from pist.records import MapRecord, MapRecordProgress
from pist.sheet_report import ManualRecordField
from pist.types import CellValue

LEFT_MOUSE_BUTTON = 1
DOUBLE_CLICK_COUNT = 2
type AuthorSource = GameBananaSubmission | str | None

RECORD_DIFFICULTY_ROWS = (
    ('体感难度', '难度子阶'),
    ('标注难度', '标注难度子阶'),
)
COLLECTED_ENTITY_ISSUE_REASONS = MappingProxyType(
    {
        CollectedEntityRuleIssueStatus.UNMATCHED: '尚无匹配规则',
        CollectedEntityRuleIssueStatus.EXCLUDED: '与现有排除规则冲突',
        CollectedEntityRuleIssueStatus.UNREVIEWED_VARIANT: '出现尚未审查的属性变体',
    }
)


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


class DatePickerScreen(ModalScreen[str | None]):
    """Choose one optional ISO date from a compact month calendar."""

    def __init__(self, selected: date | None = None) -> None:
        super().__init__()
        selected = selected or datetime.now(tz=UTC).date()
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


class RecordReferenceSummary(Static):
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


class RecordReferenceScreen(ModalScreen[None]):
    """Show the complete GameBanana introduction or collab tag text."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '关闭')]

    def __init__(self, title: str, content: str) -> None:
        super().__init__()
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical(id='record-reference-dialog'):
            yield Static(self._title, classes='record-reference-title')
            with VerticalScroll(id='record-reference-content'):
                yield Static(self._content)
            with Horizontal(id='record-reference-actions'):
                yield Button('关闭', id='record-reference-close', variant='primary')

    @on(Button.Pressed, '#record-reference-close')
    def close(self) -> None:
        self.dismiss()


class CollectedEntityRulesResult(StrEnum):
    """The user's next step after reviewing a collected-entity rule warning."""

    CLOSE = 'close'
    REFRESH = 'refresh'


class CollectedEntityRulesScreen(ModalScreen[CollectedEntityRulesResult]):
    """Explain why a record cannot proceed until collected entities have rules."""

    def __init__(self, issues: tuple[CollectedEntityRuleIssue, ...]) -> None:
        super().__init__()
        self._issues = issues

    def compose(self) -> ComposeResult:
        with Vertical(id='collected-entity-rules'):
            yield Static('已收集实体需要补充规则', classes='record-reference-title')
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
        self._manual_fields = manual_fields
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
                    yield RecordReferenceSummary(
                        title,
                        content,
                        id='record-reference-summary',
                        classes='record-reference-summary',
                    )
                with Grid(id='record-fields'):
                    yield from self._record_field_widgets()
            with Horizontal(id='record-confirm-actions'):
                yield Button('取消', id='record-confirm-cancel')
                yield Button('保存', id='record-confirm-save', variant='primary')

    @on(RecordReferenceSummary.Opened)
    def open_record_reference(self, event: RecordReferenceSummary.Opened) -> None:
        """Open the complete supplemental reference without leaving the record."""
        self.app.push_screen(RecordReferenceScreen(event.title, event.content))

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
        author_field = self.query_one(RecordAuthorField)
        author_field.update('、'.join(authors) or '单击选择作者')
        if authors:
            author_field.remove_class('record-author-placeholder')
        else:
            author_field.add_class('record-author-placeholder')

    def _record_field_widgets(self) -> ComposeResult:
        """Render generated data and editable table fields in the requested row order."""
        yield from self._pair(
            'Mod 名', self._record.mod_name, 'Mod 元数据名', self._record.mod_metadata_name
        )
        yield self._label('作者')
        if self._author_source is None:
            yield Static('、'.join(self._record.authors))
        else:
            author_field = RecordAuthorField('、'.join(self._record.authors) or '单击选择作者')
            if not self._record.authors:
                author_field.add_class('record-author-placeholder')
            yield author_field
        yield self._label('更新时间')
        yield self._updated_at_control()
        yield from self._pair('地图', self._record.map_name, 'SID', self._record.sid)
        yield from self._pair(
            '存档槽',
            self._record.save_slot,
            '通关状态' if self._progress is not None else '已通关',
            self._progress.label if self._progress is not None else self._record.completed,
        )
        yield from self._pair('用时', self._record.time_played, '死亡', self._record.deaths)
        yield self._label('主房间数')
        room_count = self._table_value('主房间数')
        if self._edit_route is None:
            yield Static(self._display_value(room_count))
        else:
            route_label = '单击编辑路线'
            if room_count is not None:
                route_label = f'{room_count}（单击编辑路线）'
            route_field = RecordRouteField(route_label)
            if room_count is None:
                route_field.add_class('record-route-placeholder')
            yield route_field
        yield self._label('合集标签')
        yield Static(self._display_value(self._collab_tags))
        yield from self._pair(
            '红草莓数', self._table_value('红草莓数'), '月莓数', self._table_value('月莓数')
        )
        yield from self._pair(
            '磁带', self._table_value('磁带'), '水晶之心', self._table_value('水晶之心')
        )
        for titles in RECORD_DIFFICULTY_ROWS:
            yield from self._manual_pair(*titles)
        yield from self._manual_pair('起始日期', '结束日期')
        yield from self._manual_pair('状态', 'SL使用')
        rating = self._manual_field('评分')
        if rating is not None:
            yield self._label('评分')
            yield self._manual_control(rating, classes='record-wide-control')
        note = self._manual_field('备注')
        if note is not None:
            yield self._label('备注')
            yield self._manual_control(note, classes='record-note-control')

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
        return Static(value, classes='record-field-label')

    def _field_value(self, title: str, value: object) -> Static:
        """Render a generated field value or a visible explanation for a missing value."""
        hint = self._field_hints.get(title) if value is None else None
        field = Static(self._display_value(value) if hint is None else hint)
        if hint is not None:
            field.add_class('record-field-warning')
        return field

    def _manual_pair(self, left_title: str, right_title: str) -> ComposeResult:
        for title in (left_title, right_title):
            field = self._manual_field(title)
            yield self._label('难度子阶' if title == '标注难度子阶' else title)
            yield Static('') if field is None else self._manual_control(field)

    def _manual_field(self, title: str) -> ManualRecordField | None:
        return next((field for field in self._manual_fields if field.title == title), None)

    def _manual_control(self, field: ManualRecordField, *, classes: str | None = None) -> Widget:
        field_id = self._manual_field_id(field)
        value = self._table_value(field.title)
        match field.field_type:
            case 4:
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
            case 17:
                options = field.options
                if isinstance(value, str) and value and value not in options:
                    options = (*options, value)
                if isinstance(value, str):
                    return Select(
                        ((option, option) for option in options),
                        prompt=f'选择{field.title}',
                        value=value,
                        id=field_id,
                        classes=classes,
                        compact=True,
                    )
                return Select(
                    ((option, option) for option in options),
                    prompt=f'选择{field.title}',
                    id=field_id,
                    classes=classes,
                    compact=True,
                )
            case 2:
                return Input(
                    self._display_value(value),
                    placeholder='0–10',
                    type='integer',
                    id=field_id,
                    classes=classes,
                    validators=None,
                    compact=True,
                )
            case 1:
                return Input(self._display_value(value), id=field_id, classes=classes, compact=True)
            case _:
                return Static('')

    def _updated_at_control(self) -> Horizontal:
        value = (
            ''
            if self._record.mod_updated_at is None
            else self._record.mod_updated_at.date().isoformat()
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
        for values in self._record.record_values.values():
            if title in values:
                return values[title]
        return None

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
            manual_values = self._manual_values()
            if manual_values is None:
                return
            try:
                updated_at = self._updated_at()
            except ValueError:
                self.notify('更新时间请使用 YYYY-MM-DD 格式。', severity='warning')
                return
            record_values = {
                table: dict(values) for table, values in self._record.record_values.items()
            }
            record_values.setdefault('主表', {}).update(manual_values)
            self.dismiss(
                self._record.model_copy(
                    update={'mod_updated_at': updated_at, 'record_values': record_values}
                )
            )

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
        self.call_after_refresh(
            self.app.push_screen,
            DatePickerScreen(selected),
            lambda value: self._set_date_value(field_id, value),
        )

    def _set_date_value(self, field_id: str, value: str | None) -> None:
        if value is not None:
            self.query_one(f'#{field_id}', Input).value = value

    def _updated_at(self) -> datetime | None:
        value = self.query_one('#record-updated-at', Input).value.strip()
        if not value:
            return None
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)

    def _manual_field_id(self, field: ManualRecordField) -> str:
        """Return a DOM-safe ID for a selected inspected field."""
        return f'record-manual-{self._manual_fields.index(field)}'

    def _manual_values(self) -> dict[str, int | str] | None:
        """Validate optional inputs and return only fields the user filled in."""
        values: dict[str, int | str] = {}
        for field in self._manual_fields:
            widget_id = self._manual_field_id(field)
            if field.field_type == 4:
                value = self.query_one(f'#{widget_id}', Input).value.strip()
                if not value:
                    continue
                try:
                    date.fromisoformat(value)
                except ValueError:
                    self.notify(f'{field.title}请使用 YYYY-MM-DD 格式。', severity='warning')
                    return None
                values[field.title] = value
            elif field.field_type == 17:
                value = self.query_one(f'#{widget_id}', Select).value
                if isinstance(value, str):
                    values[field.title] = value
            elif field.field_type == 2:
                value = self.query_one(f'#{widget_id}', Input).value.strip()
                if not value:
                    continue
                try:
                    rating = int(value)
                except ValueError:
                    self.notify('评分必须是 0–10 的整数。', severity='warning')
                    return None
                if not 0 <= rating <= 10:
                    self.notify('评分必须在 0–10 之间。', severity='warning')
                    return None
                values[field.title] = rating
            elif field.field_type == 1:
                value = self.query_one(f'#{widget_id}', Input).value.strip()
                if value:
                    values[field.title] = value
        return values


class RecordSyncModeScreen(ModalScreen[bool | None]):
    """Ask whether a saved local record should add or update a table record."""

    def compose(self) -> ComposeResult:
        with Vertical(id='record-sync-mode'):
            yield Static('本地记录已保存。是否同步到腾讯表格？')
            yield Static('更新会按 Mod 元数据名和地图名查找唯一记录；不唯一时会拒绝写入。')
            with Horizontal(id='record-sync-actions'):
                yield Button('仅保存本地记录', id='record-sync-cancel')
                yield Button('新增表格记录', id='record-sync-add')
                yield Button('更新匹配记录', id='record-sync-update', variant='primary')

    @on(Button.Pressed)
    def choose_mode(self, event: Button.Pressed) -> None:
        if event.button.id is None:
            return
        choice = {'record-sync-add': False, 'record-sync-update': True}.get(event.button.id)
        self.dismiss(choice)


class AuthorSelectionScreen(ModalScreen[tuple[str, ...] | None]):
    """Let the player select author entries from raw GameBanana Credits."""

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
                for index, (name, group, role) in enumerate(self._choices):
                    label = f'{name}  [{group}]'
                    if role:
                        label += f'  [{role}]'
                    yield Checkbox(
                        Text(label),
                        value=name in self._selected_authors,
                        id=f'record-author-{index}',
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
                for index, (name, _, _) in enumerate(self._choices)
                if self.query_one(f'#record-author-{index}', Checkbox).value
            )
        )
        self.dismiss(authors)


class DialogAuthorSelectionScreen(ModalScreen[tuple[str, ...] | None]):
    """Select one or more arbitrary author-name spans from Dialog text."""

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
                    Input(author, id=f'dialog-author-{index}', compact=True),
                    Button('×', id=f'dialog-author-remove-{index}', compact=True),
                    classes='dialog-author-row',
                )
                for index, author in enumerate(self._authors)
            )
        )

    def _sync_authors(self, *, skip_index: int | None = None) -> None:
        """Keep edits made in the selected-author fields before rebuilding or saving."""
        self._authors = list(
            dict.fromkeys(
                value
                for index in range(len(self._authors))
                if index != skip_index
                and (value := self.query_one(f'#dialog-author-{index}', Input).value.strip())
            )
        )
