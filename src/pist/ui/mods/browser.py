"""Textual UI for browsing locally enabled Mods."""

from calendar import monthrange
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time
from html import unescape
from pathlib import Path, PurePosixPath
from re import IGNORECASE, sub
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Footer,
    Header,
    Input,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
    Tree,
)
from textual.widgets._tree import TreeNode

from pist.entities.analysis import SelectConflict
from pist.game.dialog import DIALOG_LANGUAGES, localized_name
from pist.game.mods import (
    Dependency,
    InstalledMod,
    LocalCampaign,
    LocalMap,
    ModScanReport,
    collab_journal_map_order,
    is_collab_submission_map,
    is_mod_dependency,
)
from pist.game.routes import (
    EndersBlenderReader,
    EndersBlenderSave,
    MapRoute,
    load_map_entity_stats_from_path,
    load_map_layout,
)
from pist.game.saves import SaveReader, SaveSlot
from pist.gamebanana import GameBananaClient, GameBananaLookupError, GameBananaSubmission
from pist.local_data import LocalDataStore
from pist.map_preview import MapPreview, MapPreviewError
from pist.records import MapRecord, create_map_record, merge_saved_record
from pist.settings import PistSettings, SettingsStore
from pist.sheet_report import ManualRecordField, manual_record_fields
from pist.smartsheet import InspectionReport, TencentSmartSheetClient, extract_file_id
from pist.types import CellValue, RecordValues

from ..tui import RefreshableCssApp

MOD_TREE_ID = 'mod-tree'
DETAIL_ID = 'detail'
MAP_DETAIL_ID = 'map-detail'
DETAIL_SCROLL_ID = 'detail-scroll'
LEFT_MOUSE_BUTTON = 1
type AuthorSource = GameBananaSubmission | str | None
RIGHT_MOUSE_BUTTON = 3
DOUBLE_CLICK_COUNT = 2
RECORD_DIFFICULTY_ROWS = (
    ('体感难度', '难度子阶'),
    ('标注难度', '标注难度子阶'),
)


def _plain_html(value: str) -> str:
    """Convert the small HTML fragment returned by GameBanana into readable text."""
    value = sub(r'<(?:br|/p|/div|/li)\s*/?>', '\n', value, flags=IGNORECASE)
    value = sub(r'<[^>]+>', '', value)
    value = unescape(value).replace('\u00a0', ' ')
    return sub(r'\n{3,}', '\n\n', value).strip()


def _select_conflict_hints(conflicts: tuple[SelectConflict, ...]) -> dict[str, str]:
    """Return visible field hints for select-stat values that conflict in one map."""
    return {
        conflict.table_field.field: f'多个结果：{"、".join(sorted(conflict.values))}'
        for conflict in conflicts
    }


def _reference_summary(title: str, content: str, *, limit: int = 120) -> str:
    """Return one compact, single-line preview of supplemental record information."""
    value = ' '.join(content.split())
    if len(value) > limit:
        value = f'{value[:limit].rstrip()}…'
    return f'[b]{title}[/b]\n{value}'


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
                yield Static(self._month_label(), id='date-picker-month')
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

    def _month_label(self) -> str:
        return f'{self._year} 年 {self._month} 月'

    @on(Button.Pressed)
    def select_date(self, event: Button.Pressed) -> None:
        """Navigate months or return the clicked calendar day."""
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


def _add_line(text: Text, label: str, value: object) -> None:
    text.append(f'{label}: ', style='bold')
    text.append(str(value))
    text.append('\n')


def _alternate_name_lines(names: dict[str, str], languages: Iterable[str], *, indent: str) -> Text:
    text = Text()
    display = localized_name(names, languages)
    for lang in languages:
        if (name := names.get(lang)) is not None and name != display:
            label = '英文' if lang == 'en' else lang
            text.append(f'{indent}{label}: {name}\n', style='dim')
    return text


def _recorded_maps_first(maps: Iterable[LocalMap], save_slot: SaveSlot | None) -> list[LocalMap]:
    """Return completed maps, then recorded maps, then unrecorded maps, stably."""
    map_list = list(maps)
    if save_slot is None:
        return map_list
    return sorted(
        map_list,
        key=lambda map_info: (
            0
            if (stats := save_slot.get_map_stats(map_info)) is not None and stats.completed
            else 1
            if stats is not None and stats.is_recorded
            else 2
        ),
    )


def _map_lines(
    maps: Iterable[LocalMap],
    languages: Iterable[str],
    save_slot: SaveSlot | None = None,
    *,
    trim: bool = False,
) -> Text:
    text = Text()
    for map_info in _recorded_maps_first(maps, save_slot):
        display_name = localized_name(map_info.names, languages) or map_info.fallback_name
        text.append(f'• {display_name}\n', style='cyan')
        text.append_text(_alternate_name_lines(map_info.names, languages, indent='  '))
        text.append(f'  文件: {map_info.file_path}\n', style='dim')
        if save_slot is not None:
            stats = save_slot.get_map_stats(map_info)
            if stats is None or not stats.is_recorded:
                text.append(f'  存档 {save_slot.number}: 无记录\n', style='dim')
            else:
                time_played = stats.time_played.ingame_format('seconds')
                completed = '，已通关' if stats.completed else ''
                text.append(
                    f'  存档 {save_slot.number}: 用时 {time_played}，死亡 {stats.deaths}{completed}\n',
                    style='green',
                )
    if trim:
        text.rstrip()
    return text


def _campaign_lines(
    campaigns: Iterable[LocalCampaign], languages: Iterable[str], save_slot: SaveSlot | None = None
) -> Text:
    text = Text()
    for campaign in campaigns:
        display_name = localized_name(campaign.names, languages) or campaign.fallback_name
        text.append(f'\n{display_name}\n', style='bold cyan')
        text.append(f'  路径: {campaign.directory}\n', style='dim')
        text.append_text(_map_lines(campaign.maps, languages, save_slot))
    return text


def _ungrouped_maps(mod: InstalledMod) -> list[LocalMap]:
    """Return maps that do not belong to a local campaign, such as ``Maps/Foo.bin``."""
    grouped = {map_info.file_path for campaign in mod.campaigns for map_info in campaign.maps}
    lobbies = {map_info.file_path for map_info in _collab_lobby_maps(mod)}
    return [
        map_info
        for map_info in mod.maps
        if map_info.file_path not in grouped
        and map_info.file_path not in lobbies
        and (mod.collab_id is None or is_collab_submission_map(map_info))
    ]


def _collab_lobby_maps(mod: InstalledMod) -> list[LocalMap]:
    """Return non-campaign Lobby maps that are available for route previewing."""
    if mod.collab_id is None:
        return []
    grouped = {map_info.file_path for campaign in mod.campaigns for map_info in campaign.maps}
    lobby_dir = PurePosixPath('Maps', mod.collab_id, '0-Lobbies')
    return [
        map_info
        for map_info in mod.maps
        if map_info.file_path not in grouped
        and PurePosixPath(map_info.file_path).parent == lobby_dir
    ]


def format_mod_summary(mod: InstalledMod) -> Text:
    """Render the always-visible metadata for one selected Mod."""
    text = Text()
    _add_line(text, '名称', mod.metadata_name)
    _add_line(text, '版本', mod.metadata_version or '(未声明)')
    _add_line(text, '包文件', mod.filename)
    if mod.collab_id:
        _add_line(text, '合集 ID', mod.collab_id)
    _add_line(text, '地图数', len(mod.maps))
    return text


def format_mod_maps(
    mod: InstalledMod,
    languages: Iterable[str] = DIALOG_LANGUAGES,
    save_slot: SaveSlot | None = None,
) -> Text:
    """Render local map names and Dialog keys for one selected Mod."""
    text = Text()
    text.append('地图\n', style='bold underline')
    if mod.campaigns:
        text.append_text(_map_lines(_ungrouped_maps(mod), languages, save_slot))
        text.append_text(_campaign_lines(mod.campaigns, languages, save_slot))
    elif mod.maps:
        text.append_text(_map_lines(mod.maps, languages, save_slot))
    else:
        text.append('此 Mod 不包含 Maps 目录下的 .bin 地图文件。\n', style='dim')
    return text


def _campaign_detail(
    campaign: LocalCampaign, languages: Iterable[str], save_slot: SaveSlot | None = None
) -> Text:
    text = Text()
    text.append_text(_alternate_name_lines(campaign.names, languages, indent=''))
    text.append(f'路径: {campaign.directory}\n', style='dim')
    text.append_text(_map_lines(campaign.maps, languages, save_slot))
    return text


def _campaign_header(campaign: LocalCampaign, languages: Iterable[str]) -> Text:
    """Render the non-selectable metadata preceding a campaign's map list."""
    text = Text()
    text.append_text(_alternate_name_lines(campaign.names, languages, indent=''))
    text.append(f'路径: {campaign.directory}\n', style='dim')
    return text


def format_mod_detail(
    mod: InstalledMod,
    languages: Iterable[str] = DIALOG_LANGUAGES,
    save_slot: SaveSlot | None = None,
) -> Text:
    """Render all Mod detail sections for non-interactive use."""
    text = format_mod_summary(mod)
    text.append_text(format_mod_maps(mod, languages, save_slot))
    return text


def dependency_roots(mods: Iterable[InstalledMod]) -> list[InstalledMod]:
    """Return roots, adding disconnected cycles so no enabled Mod is hidden."""
    mod_list = list(mods)
    mods_by_name = {mod.metadata_name.casefold(): mod for mod in mod_list}
    referenced = {
        dependency.name.casefold()
        for mod in mod_list
        for dependency in [*mod.dependencies, *mod.optional_dependencies]
        if is_mod_dependency(dependency.name)
        if dependency.name.casefold() in mods_by_name
    }
    roots = [mod for mod in mod_list if mod.metadata_name.casefold() not in referenced]
    reachable: set[str] = set()

    def visit(mod: InstalledMod) -> None:
        name = mod.metadata_name.casefold()
        if name in reachable:
            return
        reachable.add(name)
        for dependency in [*mod.dependencies, *mod.optional_dependencies]:
            if not is_mod_dependency(dependency.name):
                continue
            child = mods_by_name.get(dependency.name.casefold())
            if child is not None:
                visit(child)

    for root in roots:
        visit(root)
    roots.extend(mod for mod in mod_list if mod.metadata_name.casefold() not in reachable)
    return roots


class ModTree(Tree[InstalledMod]):
    """Dependency tree with compact expand/collapse symbols."""

    ICON_NODE = '▸ '
    ICON_NODE_EXPANDED = '▾ '
    ICON_LEAF = '∗ '

    def render_label(self, node: TreeNode[InstalledMod], base_style: Style, style: Style) -> Text:
        """Prefix leaf nodes without putting tree decorations in their labels."""
        text = super().render_label(node, base_style, style)
        return Text.assemble((self.ICON_LEAF, base_style), text) if not node.allow_expand else text


class MapItem(ListItem):
    """One selectable map item in the Mod detail pane."""

    class Clicked(Message):
        """A map item was clicked with a specific mouse button and click count."""

        def __init__(self, item: MapItem, button: int, chain: int) -> None:
            super().__init__()
            self.item = item
            self.button = button
            self.chain = chain

    def __init__(
        self,
        map_info: LocalMap,
        languages: Iterable[str],
        save_slot: SaveSlot | None,
    ) -> None:
        super().__init__(Static(_map_lines([map_info], languages, save_slot, trim=True)))
        self.map_info = map_info
        self._languages = languages

    def refresh_stats(self, save_slot: SaveSlot | None) -> None:
        """Update only this item's dynamic native save statistics."""
        self.query_one(Static).update(
            _map_lines([self.map_info], self._languages, save_slot, trim=True)
        )

    @on(events.Click)
    def map_clicked(self, event: events.Click) -> None:
        """Expose map-specific mouse gestures after ListItem handles selection."""
        self.post_message(self.Clicked(self, event.button, event.chain))


class MapList(ListView):
    """A map list that retains its original order across save-slot reordering."""

    def __init__(
        self,
        maps: Iterable[LocalMap],
        languages: Iterable[str],
        save_slot: SaveSlot | None,
    ) -> None:
        self.maps = tuple(maps)
        super().__init__(
            *(
                MapItem(map_info, languages, save_slot)
                for map_info in _recorded_maps_first(self.maps, save_slot)
            ),
            classes='map-list',
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
    ) -> None:
        super().__init__()
        self._record = record
        self._manual_fields = manual_fields
        self._reference = reference
        self._author_source = author_source
        self._collab_tags = collab_tags
        self._field_hints = {} if field_hints is None else field_hints
        self._edit_route = edit_route

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
        yield from self._pair('存档槽', self._record.save_slot, '已通关', self._record.completed)
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
        return '' if value is None else str(value)

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


class ModBrowserApp(RefreshableCssApp[None]):
    """Browse a fixed scan result through keyboard-friendly panes."""

    CSS_PATH = '../styles/mods_browser.tcss'
    TITLE = 'Pist · 已启用 Mod'
    BINDINGS: ClassVar = [
        ('q', 'quit', '退出'),
        ('escape', 'quit', '退出'),
        ('j', 'scroll_detail_down', '详情下滚'),
        ('k', 'scroll_detail_up', '详情上滚'),
        ('[', 'previous_save_slot', '上一存档'),
        (']', 'next_save_slot', '下一存档'),
        ('p', 'preview_map', '预览地图'),
        ('t', 'toggle_theme', '切换亮暗'),
    ]

    def __init__(
        self,
        report: ModScanReport,
        *,
        settings: PistSettings | None = None,
        settings_store: SettingsStore | None = None,
        save_reader: SaveReader | None = None,
        save_slot: SaveSlot | None = None,
        route_reader: EndersBlenderReader | None = None,
        local_data: LocalDataStore | None = None,
        gamebanana_client: GameBananaClient | None = None,
        inspection_report: InspectionReport | None = None,
        sheet_client: TencentSmartSheetClient | None = None,
        sheet_source: str | None = None,
    ) -> None:
        super().__init__()
        settings = settings or PistSettings()
        self.theme = settings.theme
        self._dialog_languages = settings.dialog_languages
        self._settings_store = settings_store
        self._save_reader = save_reader
        self._save_slot = save_slot
        self._route_reader = route_reader
        self._local_data = local_data or LocalDataStore()
        self._gamebanana_client = gamebanana_client
        self._sheet_client = sheet_client
        self._sheet_source = sheet_source
        self._manual_record_fields = (
            () if inspection_report is None else manual_record_fields(inspection_report)
        )
        self._mods = report.mods
        self._mods_by_name = {mod.metadata_name.casefold(): mod for mod in report.mods}
        self._disabled_mod_names = {name.casefold() for name in report.disabled_mod_names}
        self._root_mods = dependency_roots(self._mods)
        self._selected_mod = self._root_mods[0] if self._root_mods else None
        self._collab_map_orders: dict[tuple[str, tuple[str, ...]], tuple[LocalMap, ...]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            tree = ModTree('已启用 Mod', id=MOD_TREE_ID)
            tree.show_root = False
            tree.auto_expand = False
            for mod in self._root_mods:
                self._add_tree_node(tree.root, mod, is_optional=False, ancestry=set())
            yield tree
            with VerticalScroll(id=DETAIL_SCROLL_ID):
                if self._root_mods:
                    initial_mod = self._selected_mod
                    assert initial_mod is not None
                    yield Static(format_mod_summary(initial_mod), id=DETAIL_ID)
                    yield Vertical(*self._map_widgets(initial_mod), id=MAP_DETAIL_ID)
                else:
                    yield Static(Text('没有启用的 Mod。'), id=DETAIL_ID)
        yield Footer()

    @staticmethod
    def _mod_label(mod: InstalledMod) -> Text:
        label = Text(mod.metadata_name)
        if mod.collab_id:
            label.append('  [合集]', style='yellow')
        if mod.maps:
            label.append(f'  ({len(mod.maps)} 图)', style='dim')
        return label

    @staticmethod
    def _dependency_label(
        dependency: Dependency,
        *,
        status: str,
        is_optional: bool,
    ) -> Text:
        styles = {'enabled': 'green', 'disabled': 'dim', 'missing': 'red'}
        labels = {'enabled': '已启用', 'disabled': '已禁用', 'missing': '缺失'}
        text = Text.assemble((f'[{labels[status]}] ', styles[status]), (dependency.name, ''))
        if is_optional:
            text.append(' [可选]', style='yellow')
        if dependency.version is not None:
            text.append(f' ≥ {dependency.version}', style='dim')
        return text

    def _map_list(
        self, maps: list[LocalMap], *, collab_mod: InstalledMod | None = None
    ) -> ListView:
        if collab_mod is not None:
            maps = self._collab_journal_map_order(collab_mod, maps)
        return MapList(maps, self._dialog_languages, self._save_slot)

    def _collab_journal_map_order(self, mod: InstalledMod, maps: list[LocalMap]) -> list[LocalMap]:
        """Return a cached CollabUtils2-compatible initial order for one lobby."""
        key = mod.path, tuple(map_info.file_path for map_info in maps)
        if (ordered := self._collab_map_orders.get(key)) is None:
            ordered = tuple(collab_journal_map_order(mod, maps))
            self._collab_map_orders[key] = ordered
        return list(ordered)

    def _map_widgets(self, mod: InstalledMod) -> list[Widget]:
        if not mod.campaigns:
            if not mod.maps:
                return [Static(format_mod_maps(mod, self._dialog_languages, self._save_slot))]
            return [Static(Text('地图\n', style='bold underline')), self._map_list(mod.maps)]
        widgets: list[Widget] = [Static(Text('地图\n', style='bold underline'))]
        if maps := _ungrouped_maps(mod):
            widgets.append(self._map_list(maps))
        if lobbies := _collab_lobby_maps(mod):
            widgets.append(
                Collapsible(
                    self._map_list(lobbies),
                    title='大厅',
                    collapsed=False,
                    collapsed_symbol='▸',
                    expanded_symbol='▾',
                    classes='campaign-section',
                )
            )
        for campaign in mod.campaigns:
            widgets.append(
                Collapsible(
                    Vertical(
                        Static(
                            _campaign_header(campaign, self._dialog_languages),
                            classes='campaign-header',
                        ),
                        self._map_list(
                            campaign.maps,
                            collab_mod=mod if campaign.kind == 'collab_lobby' else None,
                        ),
                        classes='campaign-detail',
                    ),
                    title=localized_name(campaign.names, self._dialog_languages)
                    or campaign.fallback_name,
                    collapsed=False,
                    collapsed_symbol='▸',
                    expanded_symbol='▾',
                    classes='campaign-section',
                )
            )
        return widgets

    def _add_tree_node(
        self,
        parent: TreeNode[InstalledMod],
        mod: InstalledMod,
        *,
        is_optional: bool,
        ancestry: set[str],
        incoming_dependency: Dependency | None = None,
    ) -> None:
        has_enabled_dependency = any(
            is_mod_dependency(dependency.name) and dependency.name.casefold() in self._mods_by_name
            for dependency in [*mod.dependencies, *mod.optional_dependencies]
        )
        label = (
            self._dependency_label(incoming_dependency, status='enabled', is_optional=is_optional)
            if incoming_dependency is not None
            else self._mod_label(mod)
        )
        node = parent.add(label, data=mod, allow_expand=has_enabled_dependency)
        name = mod.metadata_name.casefold()
        ancestry.add(name)
        try:
            for dependency in mod.dependencies:
                self._add_dependency_node(node, dependency, is_optional=False, ancestry=ancestry)
            for dependency in mod.optional_dependencies:
                self._add_dependency_node(node, dependency, is_optional=True, ancestry=ancestry)
        finally:
            ancestry.remove(name)

    def _add_dependency_node(
        self,
        parent: TreeNode[InstalledMod],
        dependency: Dependency,
        *,
        is_optional: bool,
        ancestry: set[str],
    ) -> None:
        if not is_mod_dependency(dependency.name):
            return
        mod = self._mods_by_name.get(dependency.name.casefold())
        if mod is None:
            status = (
                'disabled' if dependency.name.casefold() in self._disabled_mod_names else 'missing'
            )
            parent.add(self._dependency_label(dependency, status=status, is_optional=is_optional))
            return
        if mod.metadata_name.casefold() in ancestry:
            parent.add(Text(f'↻ {mod.metadata_name} (循环依赖)', style='red'), data=mod)
            return
        self._add_tree_node(
            parent,
            mod,
            is_optional=is_optional,
            ancestry=ancestry,
            incoming_dependency=dependency,
        )

    async def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[InstalledMod]) -> None:
        if event.node.tree.id != MOD_TREE_ID or event.node.data is None:
            return
        self._selected_mod = event.node.data
        self.query_one(f'#{DETAIL_ID}', Static).update(format_mod_summary(self._selected_mod))
        await self._refresh_map_detail()

    async def _refresh_map_detail(self) -> None:
        if self._selected_mod is None:
            return
        map_detail = self.query_one(f'#{MAP_DETAIL_ID}', Vertical)
        await map_detail.remove_children()
        await map_detail.mount(*self._map_widgets(self._selected_mod))
        self.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll).scroll_home(immediate=True)

    async def _refresh_save_stats(self) -> None:
        """Update map stats and ordering without replacing Campaign sections."""
        map_detail = self.query_one(f'#{MAP_DETAIL_ID}', Vertical)
        for map_list in map_detail.query(MapList):
            map_items = list(map_list.query(MapItem))
            ordered_maps = _recorded_maps_first(map_list.maps, self._save_slot)
            if [map_item.map_info for map_item in map_items] == ordered_maps:
                for map_item in map_items:
                    map_item.refresh_stats(self._save_slot)
                continue
            highlighted = map_list.highlighted_child
            highlighted_map = highlighted.map_info if isinstance(highlighted, MapItem) else None
            await map_list.remove_children()
            await map_list.mount(
                *(
                    MapItem(map_info, self._dialog_languages, self._save_slot)
                    for map_info in ordered_maps
                )
            )
            if highlighted_map is not None:
                map_list.index = ordered_maps.index(highlighted_map)

    async def on_map_item_clicked(self, event: MapItem.Clicked) -> None:
        """Open a record with right-click or a route preview with double-click."""
        if self._selected_mod is None:
            return
        if event.button == RIGHT_MOUSE_BUTTON:
            await self._preview_record(event.item.map_info)
        elif event.button == LEFT_MOUSE_BUTTON and event.chain == DOUBLE_CLICK_COUNT:
            self._start_map_preview(event.item.map_info)

    async def _preview_record(self, map_info: LocalMap) -> None:
        """Edit a local record for one map after an explicit mouse gesture."""
        if self._selected_mod is None:
            return
        assert self._save_slot is not None
        record_data = self._record_values(map_info)
        if record_data is None:
            return
        record_values, field_hints = record_data
        record = create_map_record(
            self._selected_mod,
            map_info,
            save_slot=self._save_slot,
            languages=self._dialog_languages,
            record_values=record_values,
        )
        if record.time_played is None:
            self.notify('当前存档没有该地图的有效记录，不能保存本地记录。', severity='warning')
            return
        gamebanana = None
        if self._gamebanana_client is not None:
            self.notify('正在读取 GameBanana 元数据…')
            try:
                gamebanana = await self._gamebanana_client.lookup(self._selected_mod.metadata_name)
            except GameBananaLookupError as error:
                self.notify(str(error), severity='warning')
            else:
                if gamebanana is None:
                    self.notify(
                        '未找到与 Everest 元数据名精确匹配的 GameBanana 提交。', severity='warning'
                    )
        author_text = localized_name(map_info.author_texts, self._dialog_languages)
        author_source: AuthorSource = gamebanana
        if (
            self._selected_mod.collab_id
            and is_collab_submission_map(map_info)
            and author_text is not None
        ):
            author_source = author_text
        record = create_map_record(
            self._selected_mod,
            map_info,
            save_slot=self._save_slot,
            gamebanana=gamebanana,
            languages=self._dialog_languages,
            record_values=record_values,
        )
        existing_record_id = self._local_data.existing_record_id(record)
        if existing_record_id is not None:
            record = merge_saved_record(record, self._local_data.load_record(existing_record_id))
        self.push_screen(
            RecordEditorScreen(
                record,
                manual_fields=self._manual_record_fields,
                reference=self._record_reference(map_info, gamebanana),
                author_source=author_source,
                collab_tags=self._collab_tags(map_info),
                field_hints=field_hints,
                edit_route=lambda: self._start_map_preview(map_info),
            ),
            self._save_record,
        )

    def _record_reference(
        self, map_info: LocalMap, gamebanana: GameBananaSubmission | None
    ) -> tuple[str, str] | None:
        """Return the human-maintained reference text relevant to one record."""
        if gamebanana is None:
            return None
        description = _plain_html(gamebanana.description)
        return ('简介', description) if description else None

    def _collab_tags(self, map_info: LocalMap) -> str | None:
        """Return localized collab tags for their dedicated record-grid field."""
        tags = localized_name(map_info.collab_credit_tags, self._dialog_languages)
        return tags if tags is not None and tags.strip() else None

    def _record_values(self, map_info: LocalMap) -> tuple[RecordValues, dict[str, str]] | None:
        """Read corrected entity values, field hints, and a saved main-room count."""
        if self._selected_mod is None:
            return None
        try:
            route = self._local_data.load_route(map_info.file_path)
            stats = load_map_entity_stats_from_path(
                Path(self._selected_mod.path),
                map_info.file_path,
                excluded_markers=frozenset() if route is None else route.excluded_markers,
            )
        except ValueError as error:
            self.notify(f'无法读取地图实体统计：{error}', severity='warning')
            return None
        record_values = stats.record_values
        if route is not None:
            record_values.setdefault('主表', {})['主房间数'] = route.room_count
        return record_values, _select_conflict_hints(stats.select_conflicts)

    def _save_record(self, record: MapRecord | None) -> None:
        if record is None:
            return
        existing_record_id = self._local_data.existing_record_id(record)
        record_id = self._local_data.save_record(record)
        action = '已更新' if existing_record_id is not None else '已保存'
        self.notify(f'{action}本地记录：{record_id}')
        if self._sheet_client is not None and self._sheet_source is not None:
            self.push_screen(
                RecordSyncModeScreen(),
                lambda update: self._choose_record_sync(record, update),
            )

    def _choose_record_sync(self, record: MapRecord, update: bool | None) -> None:
        """Start an explicit table write after the user selects its safe mode."""
        if update is not None:
            self.run_worker(self._sync_record(record, update), exclusive=False)

    async def _sync_record(self, record: MapRecord, update: bool) -> None:
        """Synchronize a saved record to the configured Smart Sheet."""
        assert self._sheet_client is not None
        assert self._sheet_source is not None
        try:
            remote_record_id = await self._sheet_client.sync_record(
                extract_file_id(self._sheet_source), record, update=update
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.notify(f'提交表格失败：{error}', severity='error')
            return
        action = '更新' if update else '新增'
        self.notify(f'已{action}表格记录：{remote_record_id}')

    async def action_preview_map(self) -> None:
        """Open map preview for the currently highlighted map item."""
        if not isinstance(self.focused, ListView):
            self.notify('请先在地图列表中选中一张地图。', severity='warning')
            return
        item = self.focused.highlighted_child
        if not isinstance(item, MapItem) or self._selected_mod is None:
            self.notify('请先在地图列表中选中一张地图。', severity='warning')
            return
        self._start_map_preview(item.map_info)

    def _start_map_preview(self, map_info: LocalMap) -> None:
        """Start browser map preview without blocking the Mod browser."""
        self.run_worker(
            self._preview_map(map_info),
            name='map-preview',
            group='map-preview',
            exclusive=False,
        )

    async def _preview_map(self, map_info: LocalMap) -> None:
        """Open browser map preview for one selected map."""
        if self._selected_mod is None:
            return
        try:
            layout = load_map_layout(self._selected_mod, map_info)
            saved_route = self._local_data.load_route(map_info.file_path)
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        first_clear_rooms: tuple[str, ...] = ()
        enders_blender_save: EndersBlenderSave | None = None
        if self._route_reader is not None and self._save_slot is not None:
            try:
                enders_blender_save = self._route_reader.load(self._save_slot.number)
                first_clear_rooms = enders_blender_save.first_clear_room_order(map_info)
            except ValueError as error:
                self.notify(str(error), severity='warning')
        try:
            route = await MapPreview(
                map_info,
                layout,
                first_clear_rooms=first_clear_rooms,
                saved_route=saved_route,
                local_data=self._local_data,
                mod=self._selected_mod,
                enders_blender_save=enders_blender_save,
            ).preview()
        except MapPreviewError as error:
            self.notify(str(error), severity='warning')
            return
        self._save_route(route)

    def _save_route(self, route: MapRoute | None) -> None:
        """Persist a confirmed local main-room selection from a finished preview."""
        if route is None:
            return
        self._local_data.save_route(route)

    def action_scroll_detail_down(self) -> None:
        """Scroll the selected Mod's detail pane down one line."""
        self.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll).scroll_down(immediate=True)

    def action_scroll_detail_up(self) -> None:
        """Scroll the selected Mod's detail pane up one line."""
        self.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll).scroll_up(immediate=True)

    async def action_previous_save_slot(self) -> None:
        """Select the preceding available native save slot."""
        await self._change_save_slot(-1)

    async def action_next_save_slot(self) -> None:
        """Select the following available native save slot."""
        await self._change_save_slot(1)

    async def _change_save_slot(self, step: int) -> None:
        if self._save_reader is None:
            self.notify('未提供存档目录。')
            return
        numbers = self._save_reader.available_numbers()
        if not numbers:
            self.notify('未找到存档。')
            return
        if self._save_slot is None:
            number = numbers[0] if step > 0 else numbers[-1]
        else:
            index = (
                numbers.index(self._save_slot.number) if self._save_slot.number in numbers else 0
            )
            number = numbers[(index + step) % len(numbers)]
        try:
            self._save_slot = self._save_reader.load(number)
        except ValueError as error:
            self.notify(str(error), severity='error')
            return
        await self._refresh_save_stats()
        self.notify(f'已切换到存档 {number}')

    def action_toggle_theme(self) -> None:
        """Switch between Textual's built-in light and dark themes."""
        self.theme = 'textual-light' if self.theme == 'textual-dark' else 'textual-dark'
        if self._settings_store is not None:
            settings = self._settings_store.load()
            self._settings_store.save(
                settings.model_copy(
                    update={
                        'theme': self.theme,
                        'dialog_languages': self._dialog_languages,
                    }
                )
            )
        theme_name = '亮色' if self.theme == 'textual-light' else '暗色'
        self.notify(f'已切换为{theme_name}主题')


async def browse_mods(
    report: ModScanReport,
    settings_store: SettingsStore | None = None,
    save_reader: SaveReader | None = None,
    save_slot: SaveSlot | None = None,
    route_reader: EndersBlenderReader | None = None,
    inspection_report: InspectionReport | None = None,
    sheet_client: TencentSmartSheetClient | None = None,
    sheet_source: str | None = None,
) -> None:
    """Run the Mod browser within the caller's asyncio event loop."""
    settings_store = settings_store or SettingsStore()
    await ModBrowserApp(
        report,
        settings=settings_store.load(),
        settings_store=settings_store,
        save_reader=save_reader,
        save_slot=save_slot,
        route_reader=route_reader,
        gamebanana_client=GameBananaClient(),
        inspection_report=inspection_report,
        sheet_client=sheet_client,
        sheet_source=sheet_source,
    ).run_async()
