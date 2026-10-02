"""Textual UI for browsing Everest-active maps by campaign."""

import asyncio
import re
from collections.abc import Callable, Iterable, Mapping
from html import unescape
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    ListItem,
    ListView,
    Static,
)

from berries import map_layout
from berries.entities.classification import (
    CollectedEntityRuleIssue,
    CollectedEntityRuleIssueStatus,
)
from berries.entities.rules import load_entity_rule_layers
from berries.game import campaigns, collab, enders_blender, levels, mods
from berries.game.binmap import AttrValue
from berries.game.content import MAPS_DIR
from berries.game.dialog import localized_name
from berries.game.map_hiders import MapHiderRules
from berries.game.map_source import MapSource
from berries.game.maps import MapInfo
from berries.game.saves import SaveReader, SaveSlot
from berries.gamebanana import GameBananaClient, GameBananaLookupError, GameBananaSubmission
from berries.map_preview.session import MapPreviewError
from pist import entity_stats, record_entities, routes
from pist.app_data import AppDataStores
from pist.collab_lobbies import (
    DEFAULT_COLLAB_LOBBY_OVERRIDES,
    SHARED_COLLAB_LOBBIES_WRITABLE,
    CollabLobbyOverrides,
    CollabLobbyOverrideStore,
)
from pist.entities.audit import LOCAL_AUDIT_DB_PATH, EntityAuditStore
from pist.entity_stats import SelectConflict
from pist.map_preview.server import MapPreview, MapPreviewMode
from pist.paths import PIST_DIR
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
from . import campaign_list, catalog, collab_list, map_actions, map_list, records
from .collab_order import CollabMapOrderController

CAMPAIGN_LIST_ID = 'campaign-list'
CAMPAIGN_LIST_HEADER_ID = 'campaign-list-header'
CAMPAIGN_LIST_TITLE_ID = 'campaign-list-title'
BROWSER_WARNINGS_BUTTON_ID = 'browser-warnings-button'
DETAIL_ID = 'detail'
MAP_DETAIL_ID = 'map-detail'
DETAIL_SCROLL_ID = 'detail-scroll'
LEFT_MOUSE_BUTTON = 1
RIGHT_MOUSE_BUTTON = 3
DOUBLE_CLICK_COUNT = 2


def _plain_html(value: str) -> str:
    """Convert the small HTML fragment returned by GameBanana into readable text."""
    value = re.sub(r'<(?:br|/p|/div|/li)\s*/?>', '\n', value, flags=re.IGNORECASE)
    value = re.sub(r'<[^>]+>', '', value)
    value = unescape(value).replace('\u00a0', ' ')
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def _scan_warning_text(warning: mods.ModScanWarning) -> str:
    """Format one scanner warning for the consolidated warning window."""
    return f'Mod 扫描 {warning.mod_filename}/{warning.file_path}：{warning.message}'


def _select_conflict_hints(conflicts: tuple[SelectConflict, ...]) -> dict[str, str]:
    """Return visible field hints for select-stat values that conflict in one map."""
    return {
        conflict.rule.field: f'多个结果：{"、".join(sorted(conflict.values))}'
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


def _unique_campaign_map_groups(
    camps: Iterable[campaigns.Campaign],
) -> tuple[tuple[tuple[levels.Level, levels.LevelSide], ...], ...]:
    """Preserve journal Campaign groups while removing duplicate Level sides."""
    result: list[tuple[tuple[levels.Level, levels.LevelSide], ...]] = []
    seen: set[tuple[str, levels.LevelSide]] = set()
    for camp in camps:
        group: list[tuple[levels.Level, levels.LevelSide]] = []
        for level, side in camp.iter_sides():
            key = (level.sid, side)
            if key not in seen:
                seen.add(key)
                group.append((level, side))
        if group:
            result.append(tuple(group))
    return tuple(result)


def _is_source_collab_map(
    catalog: campaigns.CampaignCatalog,
    level: levels.Level,
    side: levels.LevelSide,
) -> bool:
    """Return whether a map is inside its source Mod's declared Collab root."""
    map_file = level[side]
    mod = catalog.mod_for(map_file)
    return (
        mod is not None
        and mod.collab_id is not None
        and map_file.file_path.is_relative_to(MAPS_DIR / mod.collab_id)
    )


class MapBrowserApp(RefreshableCssApp[None]):
    """Browse a fixed scan result through keyboard-friendly panes."""

    CSS_PATH = '../../styles/maps_browser.tcss'
    TITLE = 'Pist · 地图浏览'
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
        report: mods.ModScanReport,
        *,
        settings: PistSettings | None = None,
        settings_store: SettingsStore | None = None,
        save_reader: SaveReader | None = None,
        save_slot: SaveSlot | None = None,
        route_reader: enders_blender.EndersBlenderReader | None = None,
        data_stores: AppDataStores | None = None,
        gamebanana_client: GameBananaClient | None = None,
        inspection_report: InspectionReport | None = None,
        sheet_client: TencentSmartSheetClient | None = None,
        sheet_source: str | None = None,
        entity_audit_store: EntityAuditStore | None = None,
        collab_lobby_overrides: CollabLobbyOverrides = DEFAULT_COLLAB_LOBBY_OVERRIDES,
        collab_lobby_store: CollabLobbyOverrideStore | None = None,
    ) -> None:
        """Use explicit settings, otherwise load the supplied store or use defaults.

        Theme changes are persisted only when ``settings_store`` is supplied.
        """
        super().__init__()
        if settings is None:
            settings = settings_store.load() if settings_store is not None else PistSettings()
        self.theme = settings.theme
        self._dialog_languages = settings.dialog_languages
        self._settings_store = settings_store
        self._save_reader = save_reader
        self._save_slot = save_slot
        self._route_reader = route_reader
        self._data_stores = data_stores or AppDataStores()
        self._gamebanana_client = gamebanana_client
        self._sheet_client = sheet_client
        self._sheet_source = sheet_source
        self._entity_audit_store = entity_audit_store
        self._collab_lobby_overrides = collab_lobby_overrides
        self._collab_lobby_store = collab_lobby_store
        self._manual_record_fields = (
            () if inspection_report is None else manual_record_fields(inspection_report)
        )
        game_dir = Path(report.mods_dir).parent
        vanilla_dialogs = campaigns.load_vanilla_dialogs(game_dir)
        vanilla_maps = campaigns.load_vanilla_maps(game_dir)
        self._campaign_catalog = campaigns.load_campaigns(
            report.mods,
            vanilla_maps=vanilla_maps,
            base_dialogs=vanilla_dialogs,
            map_hider_rules=MapHiderRules(game_dir),
        )
        self._campaigns = self._campaign_catalog.campaigns
        self._hidden_campaigns = self._campaign_catalog.hidden_campaigns
        self._campaigns_by_directory = {
            str(camp.directory): camp
            for camp in (*self._campaigns, *self._hidden_campaigns)
            if camp.source is MapSource.MOD
        }
        self._lobby_campaign_cache: dict[
            tuple[str, levels.LevelSide], tuple[campaigns.Campaign, ...]
        ] = {}
        self._routable_maps = {
            level.sid: (level, levels.LevelSide.A)
            for camp in (*self._campaigns, *self._hidden_campaigns)
            for level in camp.levels
        }
        self._dialogs = self._campaign_catalog.dialogs
        self._browser_warnings = (
            *(_scan_warning_text(warning) for warning in report.warnings),
            *self._campaign_catalog.diagnostics,
        )
        self._showing_hidden_campaigns = not self._campaigns and bool(self._hidden_campaigns)
        self._selected_campaign = next(iter(self._displayed_campaigns), None)
        self._collab_order = CollabMapOrderController(
            self._data_stores.catalog, self._campaign_catalog
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id='campaign-panel'):
                with Horizontal(id=CAMPAIGN_LIST_HEADER_ID):
                    yield Static(self._campaign_collection_title, id=CAMPAIGN_LIST_TITLE_ID)
                    yield Button(
                        f'警告（{self.diagnostic_count}）',
                        id=BROWSER_WARNINGS_BUTTON_ID,
                        variant='warning',
                        disabled=not self.diagnostic_count,
                    )
                    with Horizontal(classes='campaign-list-controls'):
                        yield self._campaign_collection_button(-1)
                        yield self._campaign_collection_button(1)
                yield campaign_list.CampaignList(
                    (
                        campaign_list.CampaignItem(camp, self._campaign_label(camp))
                        for camp in self._displayed_campaigns
                    ),
                    id=CAMPAIGN_LIST_ID,
                )
            with VerticalScroll(id=DETAIL_SCROLL_ID):
                if self._selected_campaign is not None:
                    initial_campaign = self._selected_campaign
                    yield Static(
                        catalog.format_campaign_summary(
                            initial_campaign,
                            self._dialog_languages,
                            self._dialogs,
                            self._campaign_source_names(initial_campaign),
                        ),
                        id=DETAIL_ID,
                    )
                    yield Vertical(*self._map_widgets(initial_campaign), id=MAP_DETAIL_ID)
                else:
                    yield Static(Text('没有可加载的 campaign 地图。'), id=DETAIL_ID)
        yield Footer()

    def _campaign_label(self, camp: campaigns.Campaign) -> Text:
        name = camp.display_name(self._dialogs, self._dialog_languages)
        label = Text(f'• {name}')
        label.append(f'  ({camp.map_count} 图)', style='dim')
        source_count = len(set(self._campaign_source_names(camp)))
        if source_count > 1:
            label.append(f'  [{source_count} 个 Mod]', style='yellow')
        return label

    def _campaign_source_names(self, camp: campaigns.Campaign) -> tuple[str, ...]:
        """Return physical source names in stable first-map order."""
        return tuple(
            self._campaign_catalog.source_name_for(map_file) for map_file in camp.iter_maps()
        )

    @property
    def _displayed_campaigns(self) -> tuple[campaigns.Campaign, ...]:
        """Return the currently selected campaign collection for the left list."""
        return self._hidden_campaigns if self._showing_hidden_campaigns else self._campaigns

    @property
    def _campaign_collection_title(self) -> str:
        """Return the title for the currently displayed campaign collection."""
        return '隐藏地图集' if self._showing_hidden_campaigns else '地图集'

    def _campaign_collection_button(self, direction: int) -> campaign_list.CampaignListToggle:
        """Build one fixed-position control for moving between campaign collections."""
        button = campaign_list.CampaignListToggle(direction)
        button.set_class(self._campaign_button_is_hidden(direction), '-hidden')
        return button

    def _campaign_button_is_hidden(self, direction: int) -> bool:
        """Return whether a collection direction has no destination."""
        if direction < 0:
            return not self._showing_hidden_campaigns or not self._campaigns
        return self._showing_hidden_campaigns or not self._hidden_campaigns

    def _update_campaign_collection_buttons(self) -> None:
        """Show only the fixed-position controls whose destination exists."""
        for button in self.query(campaign_list.CampaignListToggle):
            button.set_class(self._campaign_button_is_hidden(button.direction), '-hidden')

    @on(campaign_list.CampaignListToggle.Clicked)
    async def toggle_campaign_collection(
        self, event: campaign_list.CampaignListToggle.Clicked
    ) -> None:
        """Replace the left list with the collection selected by a direction control."""
        showing_hidden = event.direction > 0
        if showing_hidden == self._showing_hidden_campaigns:
            return
        camps = self._hidden_campaigns if showing_hidden else self._campaigns
        if not camps:
            return
        self._showing_hidden_campaigns = showing_hidden
        self._selected_campaign = camps[0]
        campaign_list_widget = self.query_one(f'#{CAMPAIGN_LIST_ID}', campaign_list.CampaignList)
        campaign_list_widget.index = None
        await campaign_list_widget.remove_children()
        await campaign_list_widget.mount(
            *(
                campaign_list.CampaignItem(camp, self._campaign_label(camp))
                for camp in self._displayed_campaigns
            )
        )
        campaign_list_widget.index = 0
        campaign_list_widget.focus()
        self.query_one(f'#{CAMPAIGN_LIST_TITLE_ID}', Static).update(self._campaign_collection_title)
        self._update_campaign_collection_buttons()
        self.query_one(f'#{DETAIL_ID}', Static).update(
            catalog.format_campaign_summary(
                self._selected_campaign,
                self._dialog_languages,
                self._dialogs,
                self._campaign_source_names(self._selected_campaign),
            )
        )
        await self._refresh_map_detail()

    def _map_list(
        self,
        maps: Iterable[tuple[levels.Level, levels.LevelSide]],
        *,
        item_factory: map_list.MapItemFactory | None = None,
        extra_items: Iterable[ListItem] = (),
    ) -> map_list.MapList:
        return map_list.MapList(
            catalog.maps_by_path(maps),
            self._dialog_languages,
            self._dialogs,
            self._save_slot,
            self._campaign_catalog.source_name_for,
            item_factory=item_factory,
            extra_items=extra_items,
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._start_collab_order_loading)

    @on(Button.Pressed, f'#{BROWSER_WARNINGS_BUTTON_ID}')
    def show_browser_warnings(self) -> None:
        """Open the details behind the warning count shown in the campaign header."""
        self.action_show_messages()

    @property
    def loading_warnings(self) -> tuple[str, ...]:
        return self._browser_warnings

    def diagnostics_changed(self) -> None:
        for screen in self.screen_stack:
            for button in screen.query(f'#{BROWSER_WARNINGS_BUTTON_ID}').results(Button):
                button.label = f'警告（{self.diagnostic_count}）'
                button.disabled = not self.diagnostic_count

    def on_unmount(self) -> None:
        self._collab_order.cancel()

    def _start_collab_order_loading(self) -> None:
        self._collab_order.start(self)

    def _map_widgets(self, camp: campaigns.Campaign) -> list[Widget]:
        widgets: list[Widget] = [Static(Text('地图\n', style='bold underline'))]
        if self._campaign_overrides(camp):
            widgets.append(
                Static(
                    Text(
                        '此 campaign 含有被后加载 Mod 覆盖的同路径地图；当前显示生效版本。',
                        style='yellow',
                    ),
                    classes='map-override-warning',
                )
            )
        if any(level.is_lobby for level in camp.levels):
            widgets.append(self._collab_lobby_list(camp))
        else:
            widgets.append(
                self._campaign_map_list(
                    camp.iter_sides(),
                    use_collab_order=camp.collab_id is not None,
                )
            )
        return widgets

    def _collab_lobby_list(self, camp: campaigns.Campaign) -> map_list.MapList:
        """Render 0-Lobbies maps, adding lazy expansion only to actual lobbies."""

        def lobby_item(
            level: levels.Level,
            sides: map_list.SideGroup,
            languages: Iterable[str],
            dialogs: Mapping[str, Mapping[str, str]],
            save_slot: SaveSlot | None,
            source_name_for: Callable[[levels.Map], str],
        ) -> map_list.MapItem:
            if not level.is_lobby:
                if len(sides) > 1:
                    return map_list.SideMapItem(
                        level, sides, languages, dialogs, save_slot, source_name_for
                    )
                return map_list.MapItem(
                    level, sides[0], languages, dialogs, save_slot, source_name_for
                )
            submission_maps = Vertical(classes='lobby-maps')
            if len(sides) > 1:
                return collab_list.SideLobbyMapItem(
                    level,
                    sides,
                    languages,
                    dialogs,
                    save_slot,
                    source_name_for,
                    submission_maps,
                )
            return collab_list.LobbyMapItem(
                level,
                sides[0],
                languages,
                dialogs,
                save_slot,
                source_name_for,
                submission_maps,
            )

        return self._map_list(camp.iter_sides(), item_factory=lobby_item)

    def _campaign_overrides(self, camp: campaigns.Campaign) -> tuple[campaigns.MapOverride, ...]:
        """Return overrides whose replacement remains active in this campaign."""
        active_maps = {id(map_file) for map_file in camp.iter_maps()}
        return tuple(
            override
            for override in self._campaign_catalog.overrides
            if id(override.replacement) in active_maps
        )

    def _load_lobby_journal_refs(self, loaded_map: levels.Map) -> collab.JournalReferences:
        """Load one lobby map's journal references through the rebuildable cache."""
        mod = self._campaign_catalog.mod_for(loaded_map)
        if mod is None:
            return collab.journal_references(loaded_map)
        mod_path = str(mod.path)
        map_file = loaded_map.file_path.as_posix()
        fingerprint = collab.journal_fingerprint(loaded_map)
        cached = self._data_stores.catalog.load_references(mod_path, map_file, fingerprint)
        if cached is not None:
            return cached
        references = collab.journal_references(loaded_map)
        self._data_stores.catalog.save_references(mod_path, map_file, fingerprint, references)
        return references

    @on(collab_list.LobbyMapItem.ExpansionRequested)
    async def load_lobby_maps(self, event: collab_list.LobbyMapItem.ExpansionRequested) -> None:
        """Resolve and render the current lobby side only when it is expanded."""
        item = event.item
        lobby = item.level
        if not item.level.is_lobby:
            return
        side = item.side
        camps = await self._resolve_lobby_campaigns(lobby, side, item.map)
        if item.level is not lobby or item.side is not side:
            return
        map_groups = _unique_campaign_map_groups(camps)
        await item.submission_maps.remove_children()
        if map_groups:
            await item.submission_maps.mount(
                collab_list.CollabMapList(
                    map_groups,
                    self._dialog_languages,
                    self._dialogs,
                    self._save_slot,
                    self._campaign_catalog.source_name_for,
                )
            )
            self._collab_order.start(self.query_one(f'#{MAP_DETAIL_ID}', Vertical))
        else:
            await item.submission_maps.mount(Static('未发现有效的大厅地图集。', classes='dim'))

    async def _resolve_lobby_campaigns(
        self,
        lobby: levels.Level,
        side: levels.LevelSide,
        loaded_map: levels.Map,
    ) -> tuple[campaigns.Campaign, ...]:
        """Resolve one lobby projection from config or its lazily scanned journals."""
        cache_key = (lobby.sid, side)
        camps = self._lobby_campaign_cache.get(cache_key)
        if camps is None:
            configured_refs = self._collab_lobby_overrides.campaigns_for(lobby.sid, side)
            if configured_refs is not None:
                camps, diagnostics = campaigns.resolve_campaign_refs(
                    self._campaigns_by_directory,
                    configured_refs,
                    reference_name='配置',
                )
                for diagnostic in diagnostics:
                    self.notify(diagnostic, severity='warning')
            else:
                try:
                    references = await asyncio.to_thread(self._load_lobby_journal_refs, loaded_map)
                except (OSError, ValueError) as error:
                    self.notify(f'无法读取大厅日志：{error}', severity='warning')
                    return ()
                else:
                    camps, diagnostics = campaigns.resolve_campaign_refs(
                        self._campaigns_by_directory, references.campaign_refs
                    )
                    for diagnostic in (*references.diagnostics, *diagnostics):
                        self.notify(diagnostic, severity='warning')
            self._lobby_campaign_cache[cache_key] = camps
        return camps

    async def _edit_lobby_campaigns(self, item: collab_list.LobbyMapItem) -> None:
        """Edit and persist the explicit Campaign projection for one lobby side."""
        if self._collab_lobby_store is None:
            self.notify('当前浏览会话未提供大厅地图集配置存储。', severity='warning')
            return
        lobby = item.level
        if not item.level.is_lobby:
            return
        side = item.side
        configured = self._collab_lobby_overrides.campaigns_for(lobby.sid, side)
        if configured is None:
            scanned = await self._resolve_lobby_campaigns(lobby, side, item.map)
            initial = tuple(map_actions.campaign_ref(camp) for camp in scanned)
        else:
            initial = configured
        mod = self._campaign_catalog.mod_for(item.map)
        if mod is None:
            return
        choices = map_actions.lobby_campaign_choices(
            self._campaigns_by_directory.values(),
            self._hidden_campaigns,
            mod,
            self._campaign_catalog,
            self._dialogs,
            self._dialog_languages,
            initial,
        )
        result = await self.push_screen_wait(
            map_actions.LobbyCampaignEditorScreen(
                lobby.sid,
                side.value,
                choices,
                initial,
                can_reset_local=self._collab_lobby_store.has_local_override(lobby.sid, side),
                can_reset_shared=(
                    self._collab_lobby_store.can_write_shared
                    and self._collab_lobby_store.has_shared_override(lobby.sid, side)
                ),
                can_write_shared=self._collab_lobby_store.can_write_shared,
                using_scanned_initial=configured is None,
            )
        )
        if result is None:
            return
        try:
            match result.target:
                case map_actions.LobbyCampaignEditTarget.LOCAL:
                    self._collab_lobby_overrides = self._collab_lobby_store.save_local(
                        lobby.sid, side, result.campaigns
                    )
                    message = '已保存大厅地图集到本地配置。'
                case map_actions.LobbyCampaignEditTarget.SHARED:
                    self._collab_lobby_overrides = self._collab_lobby_store.save_shared(
                        lobby.sid, side, result.campaigns
                    )
                    message = '已保存大厅地图集到共享配置。'
                case map_actions.LobbyCampaignEditTarget.RESET_LOCAL:
                    self._collab_lobby_overrides = self._collab_lobby_store.remove_local(
                        lobby.sid, side
                    )
                    message = '已重置大厅地图集本地配置。'
                case map_actions.LobbyCampaignEditTarget.RESET_SHARED:
                    self._collab_lobby_overrides = self._collab_lobby_store.remove_shared(
                        lobby.sid, side
                    )
                    message = '已重置大厅地图集共享配置。'
        except (OSError, ValueError) as error:
            self.notify(f'无法保存大厅地图集：{error}', severity='error')
            return
        self._lobby_campaign_cache.pop((lobby.sid, side), None)
        self.notify(message)
        if not item.collapsed:
            item.post_message(collab_list.LobbyMapItem.ExpansionRequested(item))

    def _campaign_map_list(
        self,
        maps: Iterable[tuple[levels.Level, levels.LevelSide]],
        *,
        extra_items: Iterable[ListItem] = (),
        use_collab_order: bool = False,
    ) -> Widget:
        """Use Collab's journal ordering for known Collab map collections."""
        loaded_maps = tuple(maps)
        if any(self._campaign_catalog.mod_for(level[side]) is None for level, side in loaded_maps):
            return self._map_list(loaded_maps, extra_items=extra_items)
        if use_collab_order or all(
            _is_source_collab_map(self._campaign_catalog, *item) for item in loaded_maps
        ):
            return collab_list.CollabMapList(
                (loaded_maps,),
                self._dialog_languages,
                self._dialogs,
                self._save_slot,
                self._campaign_catalog.source_name_for,
                extra_items=extra_items,
            )
        return self._map_list(loaded_maps, extra_items=extra_items)

    @on(ListView.Highlighted)
    async def on_campaign_highlighted(self, event: ListView.Highlighted) -> None:
        if not isinstance(event.item, campaign_list.CampaignItem):
            return
        if event.item.campaign is self._selected_campaign:
            return
        self._selected_campaign = event.item.campaign
        self.query_one(f'#{DETAIL_ID}', Static).update(
            catalog.format_campaign_summary(
                event.item.campaign,
                self._dialog_languages,
                self._dialogs,
                self._campaign_source_names(event.item.campaign),
            )
        )
        await self._refresh_map_detail()

    async def _refresh_map_detail(self) -> None:
        if self._selected_campaign is None:
            return
        self._collab_order.cancel()
        map_detail = self.query_one(f'#{MAP_DETAIL_ID}', Vertical)
        await map_detail.remove_children()
        await map_detail.mount(*self._map_widgets(self._selected_campaign))
        self.call_after_refresh(self._start_collab_order_loading)
        self.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll).scroll_home(immediate=True)

    async def _refresh_save_stats(self) -> None:
        """Refresh native save statistics and the selected slot's Collab ordering."""
        detail = self.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll)
        refreshed_map_list_ids: set[int] = set()
        for collab_map_list in detail.query(collab_list.CollabMapList):
            replacement = await self._collab_order.refresh_list(collab_map_list, self._save_slot)
            if replacement is not None:
                refreshed_map_list_ids.add(id(replacement))
        for visible_map_list in detail.query(map_list.MapList):
            if id(visible_map_list) in refreshed_map_list_ids:
                continue
            for map_item in visible_map_list.map_items:
                map_item.refresh_stats(self._save_slot)

    def on_map_item_clicked(self, event: map_list.MapItem.Clicked) -> None:
        """Open a record with double-click or the map action menu with right-click."""
        if event.button == LEFT_MOUSE_BUTTON and event.chain == DOUBLE_CLICK_COUNT:
            self.run_worker(
                self._preview_record(event.item.level, event.item.side),
                name='record-preview',
                group='record-preview',
                exclusive=True,
            )
        elif event.button == RIGHT_MOUSE_BUTTON:
            item = event.item
            self.push_screen(
                map_actions.MapActionScreen(
                    event.screen_x,
                    event.screen_y,
                    can_edit_lobby=item.level.is_lobby,
                ),
                lambda action: self._run_map_action(item, action),
            )

    def _run_map_action(self, item: map_list.MapItem, action: map_actions.MapAction | None) -> None:
        """Dispatch one context-menu action against the captured map side."""
        match action:
            case map_actions.MapAction.EDIT_ROUTE:
                self._start_route_editor(item.level, item.side)
            case map_actions.MapAction.PREVIEW:
                self._start_map_preview(item.level, item.side)
            case map_actions.MapAction.EDIT_LOBBY if isinstance(item, collab_list.LobbyMapItem):
                self.run_worker(
                    self._edit_lobby_campaigns(item),
                    name='lobby-campaign-editor',
                    group='lobby-campaign-editor',
                    exclusive=True,
                )

    async def _preview_record(self, level: levels.Level, side: levels.LevelSide) -> None:
        """Edit a local record for one map after an explicit mouse gesture."""
        active_map = level[side]
        mod = self._campaign_catalog.mod_for(active_map)
        map_info = active_map.map_info
        if mod is None:
            self.notify('原版地图不支持创建本地初见记录。', severity='warning')
            return
        assert self._save_slot is not None
        record_source = self._record_source(level, side)
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
        stats = self._save_slot.get_map_stats(level, side)
        if stats is None or not stats.is_recorded:
            self.notify('当前存档没有该地图的有效记录，不能保存本地记录。', severity='warning')
            return
        gamebanana = None
        if self._gamebanana_client is not None:
            try:
                gamebanana = await self._gamebanana_client.lookup(mod.metadata_name)
            except GameBananaLookupError as error:
                self.notify(str(error), severity='warning')
            else:
                if gamebanana is None:
                    self.notify(
                        '未找到与 Everest 元数据名精确匹配的 GameBanana 提交。', severity='warning'
                    )
        author_text = localized_name(
            level.dialog_texts(self._dialogs, 'author'),
            self._dialog_languages,
        )
        author_source: records.AuthorSource = gamebanana
        if mod.collab_id and mods.is_collab_submission_map(map_info) and author_text is not None:
            author_source = author_text
        record = create_map_record(
            level,
            side,
            mod=mod,
            save_slot=self._save_slot,
            gamebanana=gamebanana,
            languages=self._dialog_languages,
            dialogs=self._dialogs,
            record_values=record_values,
        )
        existing_record_id = self._data_stores.records.existing_id(record)
        if existing_record_id is not None:
            record = merge_saved_record(record, self._data_stores.records.load(existing_record_id))
        self.push_screen(
            records.RecordEditorScreen(
                record,
                manual_fields=self._manual_record_fields,
                reference=self._record_reference(map_info, gamebanana),
                author_source=author_source,
                collab_tags=self._collab_tags(level),
                field_hints=field_hints,
                edit_route=lambda: self._start_route_editor(level, side),
                progress=map_record_progress(
                    self._save_slot.get_map_stats(level, side),
                    review.stats,
                    is_in_progress=self._save_slot.is_in_progress(level, side),
                ),
            ),
            self._save_record,
        )

    def _record_reference(
        self, map_info: MapInfo, gamebanana: GameBananaSubmission | None
    ) -> tuple[str, str] | None:
        """Return the human-maintained reference text relevant to one record."""
        if gamebanana is None:
            return None
        description = _plain_html(gamebanana.description)
        return ('简介', description) if description else None

    def _collab_tags(self, level: levels.Level) -> str | None:
        """Return localized collab tags for their dedicated record-grid field."""
        tags = localized_name(
            level.dialog_texts(self._dialogs, 'collabcreditstags'),
            self._dialog_languages,
        )
        return tags if tags is not None and tags.strip() else None

    def _record_source(
        self, level: levels.Level, side: levels.LevelSide
    ) -> tuple[record_entities.MapEntityRecordSource, routes.MapRoute | None] | None:
        """Read one map and retain it while the user refreshes entity rules."""
        loaded_map = level[side]
        map_info = loaded_map.map_info
        try:
            route = self._data_stores.routes.load(map_info.file_path.as_posix())
            saved_stats = (
                None if self._save_slot is None else self._save_slot.get_map_stats(level, side)
            )
            source = record_entities.load_map_entity_record_source(
                loaded_map,
                frozenset() if saved_stats is None else saved_stats.collected_strawberries,
                excluded_entities=frozenset() if route is None else route.excluded_entities,
            )
        except ValueError as error:
            self.notify(f'无法读取地图实体统计：{error}', severity='warning')
            return None
        return source, route

    def _record_values(
        self, source: record_entities.MapEntityRecordSource, route: routes.MapRoute | None
    ) -> tuple[record_entities.MapEntityRecordReview, RecordValues, dict[str, str]]:
        """Summarize a retained map with the current rules and saved route corrections."""
        review = source.eval_rules(variant_review_loader=self._variant_review_checker)
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
        existing_record_id = self._data_stores.records.existing_id(record)
        record_id = self._data_stores.records.save(record)
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
        if not isinstance(item, map_list.MapItem):
            self.notify('请先在地图列表中选中一张地图。', severity='warning')
            return
        self._start_map_preview(item.level, item.side)

    def _start_map_preview(self, level: levels.Level, side: levels.LevelSide) -> None:
        """Start a read-only browser map preview without blocking the map browser."""
        self.run_worker(
            self._preview_map(level, side, initial_mode=MapPreviewMode.PREVIEW),
            name='map-preview',
            group='map-preview',
            exclusive=False,
        )

    def _start_route_editor(self, level: levels.Level, side: levels.LevelSide) -> None:
        """Start an editable route preview without blocking the map browser."""
        self.run_worker(
            self._preview_map(level, side, initial_mode=MapPreviewMode.EDIT_ROUTE),
            name='route-editor',
            group='map-preview',
            exclusive=False,
        )

    async def _preview_map(
        self,
        level: levels.Level,
        side: levels.LevelSide,
        *,
        initial_mode: MapPreviewMode,
    ) -> None:
        """Open one selected map in the requested initial interaction mode."""
        active_map = level[side]
        map_info = active_map.map_info
        try:
            layout = map_layout.load_map_layout(active_map)
            saved_route = self._data_stores.routes.load(map_info.file_path.as_posix())
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        first_clear_rooms: tuple[str, ...] = ()
        enders_blender_save: enders_blender.EndersBlenderSave | None = None
        if self._route_reader is not None and self._save_slot is not None:
            try:
                enders_blender_save = self._route_reader.load(self._save_slot.number)
                first_clear_rooms = enders_blender_save.first_clear_room_order(level, side)
            except ValueError as error:
                self.notify(str(error), severity='warning')
        try:
            classification_rules = load_entity_rule_layers(local_path=PIST_DIR / 'entities.toml')
            await MapPreview(
                active_map,
                layout,
                first_clear_rooms=first_clear_rooms,
                saved_route=saved_route,
                route_store=self._data_stores.routes,
                enders_blender_save=enders_blender_save,
                initial_mode=initial_mode,
                title=level.display_name(side, self._dialogs, self._dialog_languages),
                dialogs=self._dialogs,
                level_side=(level, side),
                routable_maps=self._routable_maps,
                classification_rules=classification_rules,
                statistic_rules=entity_stats.load_entity_stat_rules(classification_rules),
            ).preview()
        except MapPreviewError as error:
            self.notify(str(error), severity='warning')

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
        """Switch light/dark themes, saving only to an explicitly supplied store."""
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


async def browse_maps(
    report: mods.ModScanReport,
    settings_store: SettingsStore | None = None,
    save_reader: SaveReader | None = None,
    save_slot: SaveSlot | None = None,
    route_reader: enders_blender.EndersBlenderReader | None = None,
    inspection_report: InspectionReport | None = None,
    sheet_client: TencentSmartSheetClient | None = None,
    sheet_source: str | None = None,
) -> None:
    """Run the map browser, loading and saving settings only with a supplied store."""
    collab_lobby_store = CollabLobbyOverrideStore(can_write_shared=SHARED_COLLAB_LOBBIES_WRITABLE)
    await MapBrowserApp(
        report,
        settings_store=settings_store,
        save_reader=save_reader,
        save_slot=save_slot,
        route_reader=route_reader,
        gamebanana_client=GameBananaClient(),
        inspection_report=inspection_report,
        sheet_client=sheet_client,
        sheet_source=sheet_source,
        collab_lobby_overrides=collab_lobby_store.load(),
        collab_lobby_store=collab_lobby_store,
    ).run_async()
