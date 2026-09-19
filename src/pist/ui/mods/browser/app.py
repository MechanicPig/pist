"""Textual UI for browsing locally enabled Mods."""

import asyncio
import re
from collections.abc import Callable, Iterable, Mapping
from html import unescape
from itertools import chain
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import ClassVar
from zipfile import BadZipFile

from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    ListItem,
    ListView,
    LoadingIndicator,
    Static,
    Tree,
)
from textual.widgets._tree import TreeNode

from pist.entities.audit import LOCAL_AUDIT_DB_PATH, EntityAuditStore
from pist.entities.classification import (
    CollectedEntityRuleIssue,
    CollectedEntityRuleIssueStatus,
    SelectConflict,
)
from pist.game import mods as game_mods
from pist.game import routes
from pist.game.binmap import AttrValue, BadMapBin
from pist.game.dialog import DIALOG_LANGUAGES, localized_name
from pist.game.mod_path import BadModPath
from pist.game.saves import SaveReader, SaveSlot
from pist.gamebanana import GameBananaClient, GameBananaLookupError, GameBananaSubmission
from pist.local_data import LocalDataStore
from pist.map_preview import MapPreview, MapPreviewError
from pist.records import (
    MapRecord,
    create_map_record,
    map_record_progress,
    merge_saved_record,
)
from pist.settings import PistSettings, SettingsStore
from pist.sheet_report import manual_record_fields
from pist.smartsheet import InspectionReport, TencentSmartSheetClient, extract_file_id
from pist.types import RecordValues

from ...tui import RefreshableCssApp
from . import records

MOD_TREE_ID = 'mod-tree'
DETAIL_ID = 'detail'
MAP_DETAIL_ID = 'map-detail'
DETAIL_SCROLL_ID = 'detail-scroll'
LEFT_MOUSE_BUTTON = 1
RIGHT_MOUSE_BUTTON = 3
DOUBLE_CLICK_COUNT = 2
DEPENDENCY_STATUS_STYLES = MappingProxyType(
    {'enabled': 'green', 'disabled': 'dim', 'missing': 'red'}
)
DEPENDENCY_STATUS_LABELS = MappingProxyType(
    {'enabled': '已启用', 'disabled': '已禁用', 'missing': '缺失'}
)


def _plain_html(value: str) -> str:
    """Convert the small HTML fragment returned by GameBanana into readable text."""
    value = re.sub(r'<(?:br|/p|/div|/li)\s*/?>', '\n', value, flags=re.IGNORECASE)
    value = re.sub(r'<[^>]+>', '', value)
    value = unescape(value).replace('\u00a0', ' ')
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def _select_conflict_hints(conflicts: tuple[SelectConflict, ...]) -> dict[str, str]:
    """Return visible field hints for select-stat values that conflict in one map."""
    return {
        conflict.table_field.field: f'多个结果：{"、".join(sorted(conflict.values))}'
        for conflict in conflicts
    }


def _blocking_collected_entity_issues(
    issues: tuple[CollectedEntityRuleIssue, ...],
) -> tuple[CollectedEntityRuleIssue, ...]:
    """Return collected-entity issues that must be resolved before recording."""
    return tuple(
        issue
        for issue in issues
        if issue.status
        in {
            CollectedEntityRuleIssueStatus.UNMATCHED,
            CollectedEntityRuleIssueStatus.EXCLUDED,
            CollectedEntityRuleIssueStatus.UNREVIEWED_VARIANT,
        }
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


def _recorded_maps_first(
    maps: Iterable[game_mods.LocalMap], save_slot: SaveSlot | None
) -> list[game_mods.LocalMap]:
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
    maps: Iterable[game_mods.LocalMap],
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
    campaigns: Iterable[game_mods.LocalCampaign],
    languages: Iterable[str],
    save_slot: SaveSlot | None = None,
) -> Text:
    text = Text()
    for campaign in campaigns:
        display_name = localized_name(campaign.names, languages) or campaign.fallback_name
        text.append(f'\n{display_name}\n', style='bold cyan')
        text.append(f'  路径: {campaign.directory}\n', style='dim')
        text.append_text(_map_lines(campaign.maps, languages, save_slot))
    return text


def _ungrouped_maps(mod: game_mods.InstalledMod) -> list[game_mods.LocalMap]:
    """Return maps that do not belong to a local campaign, such as ``Maps/Foo.bin``."""
    grouped = {map_info.file_path for campaign in mod.campaigns for map_info in campaign.maps}
    lobbies = {map_info.file_path for map_info in _collab_lobby_maps(mod)}
    return [
        map_info
        for map_info in mod.maps
        if map_info.file_path not in grouped
        and map_info.file_path not in lobbies
        and (mod.collab_id is None or game_mods.is_collab_submission_map(map_info))
    ]


def _collab_lobby_maps(mod: game_mods.InstalledMod) -> list[game_mods.LocalMap]:
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


def format_mod_summary(mod: game_mods.InstalledMod) -> Text:
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
    mod: game_mods.InstalledMod,
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


def _campaign_header(campaign: game_mods.LocalCampaign, languages: Iterable[str]) -> Text:
    """Render the non-selectable metadata preceding a campaign's map list."""
    text = Text()
    text.append_text(_alternate_name_lines(campaign.names, languages, indent=''))
    text.append(f'路径: {campaign.directory}\n', style='dim')
    return text


def dependency_roots(mods: Iterable[game_mods.InstalledMod]) -> list[game_mods.InstalledMod]:
    """Return case-insensitively sorted roots, including disconnected dependency cycles."""
    mod_list = sorted(mods, key=lambda mod: mod.metadata_name.casefold())
    mods_by_name = {
        metadata_name.casefold(): mod for mod in mod_list for metadata_name in mod.metadata_names
    }
    referenced = {
        target.metadata_name.casefold()
        for mod in mod_list
        for dependency in chain(mod.iter_dependencies(), mod.iter_optional_dependencies())
        if game_mods.is_mod_dependency(dependency.name)
        if (target := mods_by_name.get(dependency.name.casefold())) is not None
        and target is not mod
    }
    roots = [mod for mod in mod_list if mod.metadata_name.casefold() not in referenced]
    reachable: set[str] = set()

    def visit(mod: game_mods.InstalledMod) -> None:
        name = mod.metadata_name.casefold()
        if name in reachable:
            return
        reachable.add(name)
        for dependency in chain(mod.iter_dependencies(), mod.iter_optional_dependencies()):
            if not game_mods.is_mod_dependency(dependency.name):
                continue
            child = mods_by_name.get(dependency.name.casefold())
            if child is not None and child is not mod:
                visit(child)

    for root in roots:
        visit(root)
    roots.extend(mod for mod in mod_list if mod.metadata_name.casefold() not in reachable)
    return roots


class ModTree(Tree[game_mods.InstalledMod]):
    """Dependency tree with compact expand/collapse symbols."""

    ICON_NODE = '▸ '
    ICON_NODE_EXPANDED = '▾ '
    ICON_LEAF = '∗ '

    def render_label(
        self, node: TreeNode[game_mods.InstalledMod], base_style: Style, style: Style
    ) -> Text:
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
        map_info: game_mods.LocalMap,
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
        maps: Iterable[game_mods.LocalMap],
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


class CollabMapList(Vertical):
    """A Collab map list that shows progress until its journal order is available."""

    def __init__(
        self,
        mod: game_mods.InstalledMod,
        maps: list[game_mods.LocalMap],
        languages: Iterable[str],
        save_slot: SaveSlot | None,
    ) -> None:
        super().__init__(classes='collab-map-list is-loading')
        self.mod = mod
        self.maps = maps
        self.languages = tuple(languages)
        self.save_slot = save_slot
        self._progress = Static('正在读取日志图标…')

    def compose(self) -> ComposeResult:
        yield LoadingIndicator()
        yield self._progress

    def set_progress(self, completed: int) -> None:
        """Show the number of map metadata entries already read."""
        self._progress.update(f'正在读取日志图标：{completed} / {len(self.maps)}')

    def finish_loading(self) -> None:
        """Switch from the compact loading row to a normally sized map list."""
        self.remove_class('is-loading')


class ModBrowserApp(RefreshableCssApp[None]):
    """Browse a fixed scan result through keyboard-friendly panes."""

    CSS_PATH = '../../styles/mods_browser.tcss'
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
        report: game_mods.ModScanReport,
        *,
        settings: PistSettings | None = None,
        settings_store: SettingsStore | None = None,
        save_reader: SaveReader | None = None,
        save_slot: SaveSlot | None = None,
        route_reader: routes.EndersBlenderReader | None = None,
        local_data: LocalDataStore | None = None,
        gamebanana_client: GameBananaClient | None = None,
        inspection_report: InspectionReport | None = None,
        sheet_client: TencentSmartSheetClient | None = None,
        sheet_source: str | None = None,
        entity_audit_store: EntityAuditStore | None = None,
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
        self._entity_audit_store = entity_audit_store
        self._manual_record_fields = (
            () if inspection_report is None else manual_record_fields(inspection_report)
        )
        self._mods = report.mods
        self._scan_warnings = report.warnings
        self._mods_by_name = {
            metadata_name.casefold(): mod
            for mod in report.mods
            for metadata_name in mod.metadata_names
        }
        self._disabled_mod_names = {name.casefold() for name in report.disabled_mod_names}
        self._root_mods = dependency_roots(self._mods)
        self._selected_mod = self._root_mods[0] if self._root_mods else None
        self._collab_order_task: asyncio.Task[None] | None = None

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
    def _mod_label(mod: game_mods.InstalledMod) -> Text:
        label = Text(mod.metadata_name)
        if mod.collab_id:
            label.append('  [合集]', style='yellow')
        if mod.maps:
            label.append(f'  ({len(mod.maps)} 图)', style='dim')
        return label

    @staticmethod
    def _dependency_label(
        dependency: game_mods.Dependency,
        *,
        status: str,
        is_optional: bool,
    ) -> Text:
        text = Text.assemble(
            (f'[{DEPENDENCY_STATUS_LABELS[status]}] ', DEPENDENCY_STATUS_STYLES[status]),
            (dependency.name, ''),
        )
        if is_optional:
            text.append(' [可选]', style='yellow')
        if dependency.version is not None:
            text.append(f' ≥ {dependency.version}', style='dim')
        return text

    def _map_list(
        self, maps: list[game_mods.LocalMap], *, collab_mod: game_mods.InstalledMod | None = None
    ) -> Widget:
        if collab_mod is not None:
            return CollabMapList(collab_mod, maps, self._dialog_languages, self._save_slot)
        return MapList(maps, self._dialog_languages, self._save_slot)

    def on_mount(self) -> None:
        if self._scan_warnings:
            first = self._scan_warnings[0]
            suffix = (
                '' if len(self._scan_warnings) == 1 else f'等 {len(self._scan_warnings)} 个文件'
            )
            self.notify(
                f'扫描时跳过无法解码的文本：{first.mod_filename}/{first.file_path}{suffix}',
                severity='warning',
                timeout=10,
            )
        self.call_after_refresh(self._start_collab_order_loading)

    def on_unmount(self) -> None:
        self._cancel_collab_order_loading()

    def _cancel_collab_order_loading(self) -> None:
        if self._collab_order_task is not None:
            self._collab_order_task.cancel()
            self._collab_order_task = None

    def _start_collab_order_loading(self) -> None:
        self._cancel_collab_order_loading()
        map_lists = tuple(self.query(CollabMapList))
        if map_lists:
            self._collab_order_task = asyncio.create_task(self._load_collab_map_orders(map_lists))

    async def _load_collab_map_orders(self, map_lists: tuple[CollabMapList, ...]) -> None:
        """Read uncached journal icon metadata without blocking Textual's event loop."""
        try:
            for map_list in map_lists:
                await self._load_collab_map_order(map_list)
        finally:
            if asyncio.current_task() is self._collab_order_task:
                self._collab_order_task = None

    async def _load_collab_map_order(self, map_list: CollabMapList) -> None:
        map_files = tuple(sorted(map_info.file_path for map_info in map_list.maps))
        try:
            fingerprint = game_mods.collab_journal_icon_fingerprint(map_list.mod, map_list.maps)
            icons = self._local_data.load_collab_journal_icons(
                map_list.mod.path, map_files, fingerprint
            )
            if icons is None:
                icons = {}
                for completed, (map_file, icon) in enumerate(
                    game_mods.iter_collab_journal_map_icons(map_list.mod, map_list.maps), start=1
                ):
                    icons[map_file] = icon
                    map_list.set_progress(completed)
                    await asyncio.sleep(0)
                self._local_data.save_collab_journal_icons(
                    map_list.mod.path, map_files, fingerprint, icons
                )
        except BadMapBin, BadModPath, BadZipFile, FileNotFoundError, KeyError, OSError:
            ordered_maps = map_list.maps
        else:
            ordered_maps = game_mods.collab_journal_map_order_from_icons(map_list.maps, icons)
        await map_list.remove_children()
        await map_list.mount(MapList(ordered_maps, map_list.languages, map_list.save_slot))
        map_list.finish_loading()

    def _map_widgets(self, mod: game_mods.InstalledMod) -> list[Widget]:
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
        parent: TreeNode[game_mods.InstalledMod],
        mod: game_mods.InstalledMod,
        *,
        is_optional: bool,
        ancestry: set[str],
        incoming_dependency: game_mods.Dependency | None = None,
    ) -> None:
        has_enabled_dependency = any(
            game_mods.is_mod_dependency(dependency.name)
            and (target := self._mods_by_name.get(dependency.name.casefold())) is not None
            and target is not mod
            for dependency in chain(mod.iter_dependencies(), mod.iter_optional_dependencies())
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
            for dependency in mod.iter_dependencies():
                self._add_dependency_node(
                    node, mod, dependency, is_optional=False, ancestry=ancestry
                )
            for dependency in mod.iter_optional_dependencies():
                self._add_dependency_node(
                    node, mod, dependency, is_optional=True, ancestry=ancestry
                )
        finally:
            ancestry.remove(name)

    def _add_dependency_node(
        self,
        parent: TreeNode[game_mods.InstalledMod],
        owner: game_mods.InstalledMod,
        dependency: game_mods.Dependency,
        *,
        is_optional: bool,
        ancestry: set[str],
    ) -> None:
        if not game_mods.is_mod_dependency(dependency.name):
            return
        mod = self._mods_by_name.get(dependency.name.casefold())
        if mod is None:
            status = (
                'disabled' if dependency.name.casefold() in self._disabled_mod_names else 'missing'
            )
            parent.add(self._dependency_label(dependency, status=status, is_optional=is_optional))
            return
        if mod is owner:
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

    async def on_tree_node_highlighted(
        self, event: Tree.NodeHighlighted[game_mods.InstalledMod]
    ) -> None:
        if event.node.tree.id != MOD_TREE_ID or event.node.data is None:
            return
        self._selected_mod = event.node.data
        self.query_one(f'#{DETAIL_ID}', Static).update(format_mod_summary(self._selected_mod))
        await self._refresh_map_detail()

    async def _refresh_map_detail(self) -> None:
        if self._selected_mod is None:
            return
        self._cancel_collab_order_loading()
        map_detail = self.query_one(f'#{MAP_DETAIL_ID}', Vertical)
        await map_detail.remove_children()
        await map_detail.mount(*self._map_widgets(self._selected_mod))
        self.call_after_refresh(self._start_collab_order_loading)
        self.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll).scroll_home(immediate=True)

    async def _refresh_save_stats(self) -> None:
        """Update map stats and ordering without replacing Campaign sections."""
        map_detail = self.query_one(f'#{MAP_DETAIL_ID}', Vertical)
        for map_list in map_detail.query(CollabMapList):
            map_list.save_slot = self._save_slot
        for map_list in map_detail.query(MapList):
            map_items = list(map_list.query(MapItem))
            ordered_maps = _recorded_maps_first(map_list.maps, self._save_slot)
            if [map_item.map_info for map_item in map_items] == ordered_maps:
                for map_item in map_items:
                    map_item.refresh_stats(self._save_slot)
                continue
            highlighted = map_list.highlighted_child
            highlighted_map = highlighted.map_info if isinstance(highlighted, MapItem) else None
            # Clearing the index before replacing children makes Textual apply the highlight to
            # the new item even when the selected map remains at the same numeric position.
            map_list.index = None
            await map_list.remove_children()
            await map_list.mount(
                *(
                    MapItem(map_info, self._dialog_languages, self._save_slot)
                    for map_info in ordered_maps
                )
            )
            if highlighted_map is not None:
                map_list.index = ordered_maps.index(highlighted_map)

    def on_map_item_clicked(self, event: MapItem.Clicked) -> None:
        """Open a record with right-click or a route preview with double-click."""
        if self._selected_mod is None:
            return
        if event.button == RIGHT_MOUSE_BUTTON:
            self.run_worker(
                self._preview_record(event.item.map_info),
                name='record-preview',
                group='record-preview',
                exclusive=True,
            )
        elif event.button == LEFT_MOUSE_BUTTON and event.chain == DOUBLE_CLICK_COUNT:
            self._start_map_preview(event.item.map_info)

    async def _preview_record(self, map_info: game_mods.LocalMap) -> None:
        """Edit a local record for one map after an explicit mouse gesture."""
        if self._selected_mod is None:
            return
        assert self._save_slot is not None
        record_source = self._record_source(map_info)
        if record_source is None:
            return
        source, route = record_source
        while True:
            review, record_values, field_hints = self._record_values(source, route)
            collected_issues = review.collected_issues
            blocking_issues = _blocking_collected_entity_issues(collected_issues)
            if not blocking_issues:
                self._notify_missing_collected_entities(collected_issues)
                break
            result = await self.push_screen_wait(
                records.CollectedEntityRulesScreen(blocking_issues)
            )
            if result is not records.CollectedEntityRulesResult.REFRESH:
                return
        stats = self._save_slot.get_map_stats(map_info)
        if stats is None or not stats.is_recorded:
            self.notify('当前存档没有该地图的有效记录，不能保存本地记录。', severity='warning')
            return
        gamebanana = None
        if self._gamebanana_client is not None:
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
        author_source: records.AuthorSource = gamebanana
        if (
            self._selected_mod.collab_id
            and game_mods.is_collab_submission_map(map_info)
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
            records.RecordEditorScreen(
                record,
                manual_fields=self._manual_record_fields,
                reference=self._record_reference(map_info, gamebanana),
                author_source=author_source,
                collab_tags=self._collab_tags(map_info),
                field_hints=field_hints,
                edit_route=lambda: self._start_map_preview(map_info),
                progress=map_record_progress(
                    self._save_slot.get_map_stats(map_info),
                    review.stats,
                    is_in_progress=self._save_slot.is_in_progress(map_info),
                ),
            ),
            self._save_record,
        )

    def _record_reference(
        self, map_info: game_mods.LocalMap, gamebanana: GameBananaSubmission | None
    ) -> tuple[str, str] | None:
        """Return the human-maintained reference text relevant to one record."""
        if gamebanana is None:
            return None
        description = _plain_html(gamebanana.description)
        return ('简介', description) if description else None

    def _collab_tags(self, map_info: game_mods.LocalMap) -> str | None:
        """Return localized collab tags for their dedicated record-grid field."""
        tags = localized_name(map_info.collab_credit_tags, self._dialog_languages)
        return tags if tags is not None and tags.strip() else None

    def _record_source(
        self, map_info: game_mods.LocalMap
    ) -> tuple[routes.MapEntityRecordSource, routes.MapRoute | None] | None:
        """Read one map and retain it while the user refreshes entity rules."""
        if self._selected_mod is None:
            return None
        try:
            route = self._local_data.load_route(map_info.file_path)
            saved_stats = (
                None if self._save_slot is None else self._save_slot.get_map_stats(map_info)
            )
            source = routes.load_map_entity_record_source_from_path(
                Path(self._selected_mod.path),
                map_info.file_path,
                frozenset() if saved_stats is None else saved_stats.collected_strawberries,
                excluded_entities=frozenset() if route is None else route.excluded_entities,
            )
        except ValueError as error:
            self.notify(f'无法读取地图实体统计：{error}', severity='warning')
            return None
        return source, route

    def _record_values(
        self, source: routes.MapEntityRecordSource, route: routes.MapRoute | None
    ) -> tuple[routes.MapEntityRecordReview, RecordValues, dict[str, str]]:
        """Summarize a retained map with the current rules and saved route corrections."""
        review = source.review(variant_review_loader=self._variant_review_checker)
        record_values = review.stats.record_values
        if route is not None:
            record_values.setdefault('主表', {})['主房间数'] = route.room_count
        return review, record_values, _select_conflict_hints(review.stats.select_conflicts)

    def _variant_review_checker(
        self, entity_names: frozenset[str]
    ) -> Callable[[str, Mapping[str, AttrValue], Mapping[str, AttrValue]], bool] | None:
        """Load the audit snapshot relevant to the collected map entities."""
        if not entity_names:
            return None
        if self._entity_audit_store is None:
            if not LOCAL_AUDIT_DB_PATH.is_file():
                return None
            self._entity_audit_store = EntityAuditStore()
        return self._entity_audit_store.variant_review_checker(entity_names)

    def _notify_missing_collected_entities(
        self, issues: tuple[CollectedEntityRuleIssue, ...]
    ) -> None:
        """Warn about saved IDs absent from the current map version without blocking recording."""
        missing = tuple(
            issue for issue in issues if issue.status is CollectedEntityRuleIssueStatus.NOT_FOUND
        )
        if missing:
            ids = '、'.join(
                f'{issue.collected_id.room}:{issue.collected_id.entity_id}' for issue in missing
            )
            self.notify(
                f'当前地图未找到已收集实体（可能是地图版本变化）：{ids}', severity='warning'
            )

    def _save_record(self, record: MapRecord | None) -> None:
        if record is None:
            return
        existing_record_id = self._local_data.existing_record_id(record)
        record_id = self._local_data.save_record(record)
        action = '已更新' if existing_record_id is not None else '已保存'
        self.notify(f'{action}本地记录：{record_id}')
        if self._sheet_client is not None and self._sheet_source is not None:
            self.push_screen(
                records.RecordSyncModeScreen(),
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

    def _start_map_preview(self, map_info: game_mods.LocalMap) -> None:
        """Start browser map preview without blocking the Mod browser."""
        self.run_worker(
            self._preview_map(map_info),
            name='map-preview',
            group='map-preview',
            exclusive=False,
        )

    async def _preview_map(self, map_info: game_mods.LocalMap) -> None:
        """Open browser map preview for one selected map."""
        if self._selected_mod is None:
            return
        try:
            layout = routes.load_map_layout(self._selected_mod, map_info)
            saved_route = self._local_data.load_route(map_info.file_path)
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        first_clear_rooms: tuple[str, ...] = ()
        enders_blender_save: routes.EndersBlenderSave | None = None
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

    def _save_route(self, route: routes.MapRoute | None) -> None:
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
    report: game_mods.ModScanReport,
    settings_store: SettingsStore | None = None,
    save_reader: SaveReader | None = None,
    save_slot: SaveSlot | None = None,
    route_reader: routes.EndersBlenderReader | None = None,
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
