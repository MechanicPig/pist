"""Textual UI for browsing Everest-active maps by campaign."""

import asyncio
import re
import sqlite3
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import AsyncExitStack, contextmanager
from html import unescape
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
from textual.worker import Worker

from berries import map_layout
from berries.entities.classification import (
    CollectedEntityRuleIssue,
    CollectedEntityRuleIssueStatus,
    map_mode_metadata,
)
from berries.game import campaigns, collab
from berries.game.binmap import AttrValue, MapStringDecodeWarning, map_string_warnings
from berries.game.content import MAPS_DIR, ContentPath
from berries.game.levels import Level, LevelSide, MapKey
from berries.game.localization import Localization, select_translation
from berries.game.maps import Map, ModMap, VanillaMap
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.rooms import room_key
from berries.gamebanana import GameBananaClient, GameBananaLookupError, GameBananaSubmission
from berries.map_preview.session import MapPreviewError
from pist import entity_stats, map_names
from pist.campaign_catalog import journal_messages, missing_ref_messages
from pist.data_stores import DataStores
from pist.entities.audit import LOCAL_AUDIT_DB_PATH, EntityAuditStore
from pist.entities.audit.collection import collected_issues_report
from pist.entities.audit.models import AuditSource
from pist.entities.collected import CollectedEntityReviewIssue, UnreviewedCollectedEntity
from pist.entities.config import load_entity_rule_layers
from pist.entity_stats import SelectConflict
from pist.lobby_overrides import (
    DEFAULT_COLLAB_LOBBY_OVERRIDES,
    SHARED_COLLAB_LOBBIES_WRITABLE,
    CollabLobbyOverrides,
    CollabLobbyOverrideStore,
)
from pist.map_entrances import load_map_entrance_rule_layers
from pist.map_preview.server import MapPreview, MapPreviewMode
from pist.records import entities as record_entities
from pist.records.lock import record_writer_lock
from pist.records.models import MapRecord, create_map_record, map_record_progress
from pist.routes import models as routes
from pist.save_progress import is_recorded
from pist.saves import SaveProgress, SaveProgressReader
from pist.settings import PistSettings, RecordListMaxWidths, SettingsStore
from pist.smartsheet.client import TencentSmartSheetClient
from pist.smartsheet.models import InspectionReport
from pist.smartsheet.report import ManualRecordField, manual_record_fields
from pist.types import CellValue
from pist.ui.mouse import DOUBLE_CLICK_COUNT, LEFT_MOUSE_BUTTON, RIGHT_MOUSE_BUTTON
from pist.ui.records import editor as records
from pist.ui.records.controller import RecordEditorController
from pist.ui.records.list import RecordListScreen
from pist.ui.theme import configure_theme
from pist.ui.tui import RefreshableCssApp

from . import campaign_list, catalog, collab_list, map_actions, map_list
from .collab_order import CollabMapOrderController
from .save_switch import SaveSlotSwitch

CAMPAIGN_LIST_ID = 'campaign-list'
CAMPAIGN_LIST_HEADER_ID = 'campaign-list-header'
CAMPAIGN_LIST_TITLE_ID = 'campaign-list-title'
BROWSER_WARNINGS_BUTTON_ID = 'browser-warnings-button'
DETAIL_ID = 'detail'
MAP_DETAIL_ID = 'map-detail'
DETAIL_SCROLL_ID = 'detail-scroll'


def _is_collab_submission_map(map_info: ContentPath) -> bool:
    """Apply Pist's path heuristic for preferring per-map Dialog author candidates.

    This excludes paths containing lobby/gym-like names; it is not a CollabUtils2
    identity predicate and must not be used to assign lobby or campaign identities.
    """
    parts = (part.casefold() for part in map_info.parts)
    return not any('lobb' in part or 'gym' in part for part in parts)


def _plain_html(value: str) -> str:
    """Convert the small HTML fragment returned by GameBanana into readable text."""
    value = re.sub(r'<(?:br|/p|/div|/li)\s*/?>', '\n', value, flags=re.IGNORECASE)
    value = re.sub(r'<[^>]+>', '', value)
    value = unescape(value).replace('\u00a0', ' ')
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def _select_conflict_hints(conflicts: tuple[SelectConflict, ...]) -> dict[str, str]:
    """Return visible field hints for select-stat values that conflict in one map."""
    return {
        conflict.rule.field: f'多个结果：{"、".join(sorted(conflict.values))}'
        for conflict in conflicts
    }


def _blocking_collected_entity_issues(
    issues: tuple[CollectedEntityReviewIssue, ...],
) -> tuple[CollectedEntityReviewIssue, ...]:
    """Return collected-entity issues that must be resolved before recording."""
    return tuple(
        issue
        for issue in issues
        if isinstance(issue, UnreviewedCollectedEntity)
        or issue.status
        in {
            CollectedEntityRuleIssueStatus.UNMATCHED,
            CollectedEntityRuleIssueStatus.EXCLUDED,
        }
    )


def _unique_campaign_map_groups(
    camps: Iterable[campaigns.Campaign],
) -> tuple[tuple[tuple[Level, LevelSide], ...], ...]:
    """Preserve journal Campaign groups while removing duplicate Level sides."""
    result: list[tuple[tuple[Level, LevelSide], ...]] = []
    seen: set[MapKey] = set()
    for camp in camps:
        group: list[tuple[Level, LevelSide]] = []
        for level, side in camp.iter_sides():
            key = (level.sid, side)
            if key not in seen:
                seen.add(key)
                group.append((level, side))
        if group:
            result.append(tuple(group))
    return tuple(result)


def _is_source_collab_map(
    level: Level,
    side: LevelSide,
) -> bool:
    """Return whether a map is inside its source Mod's declared Collab root."""
    map_file = level[side]
    return (
        isinstance(map_file, ModMap)
        and map_file.mod.collab_id is not None
        and map_file.file_path.is_relative_to(MAPS_DIR / map_file.mod.collab_id)
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
        ('r', 'records', '本地记录'),
    ]

    def __init__(
        self,
        campaign_catalog: campaigns.CampaignCatalog,
        *,
        diagnostics: tuple[str, ...] = (),
        settings: PistSettings | None = None,
        settings_store: SettingsStore | None = None,
        save_reader: SaveProgressReader | None = None,
        save_slot: SaveProgress | None = None,
        data_stores: DataStores | None = None,
        gamebanana_client: GameBananaClient | None = None,
        inspection_report: InspectionReport | None = None,
        sheet_client: TencentSmartSheetClient | None = None,
        sheet_source: str | None = None,
        entity_audit_store: EntityAuditStore | None = None,
        collab_lobby_overrides: CollabLobbyOverrides = DEFAULT_COLLAB_LOBBY_OVERRIDES,
        collab_lobby_store: CollabLobbyOverrideStore | None = None,
    ) -> None:
        """Use an already loaded catalog and explicit or stored UI settings.

        Theme changes are persisted only when ``settings_store`` is supplied.
        """
        super().__init__()
        if settings is None:
            settings = settings_store.load() if settings_store is not None else PistSettings()
        configure_theme(self, settings, settings_store)
        self._dialog_lang = settings.dialog_lang
        self._settings_store = settings_store
        self._record_list_max_widths = settings.record_list_max_widths
        self._save_slot = save_slot
        self._save_switch = (
            None
            if save_reader is None
            else SaveSlotSwitch(
                save_reader,
                None if save_slot is None else save_slot.number,
                on_state=self._save_switch_state,
                on_loaded=self._apply_save_slot,
                on_error=lambda error: self.notify(str(error), severity='error', markup=False),
            )
        )
        self._save_reader = save_reader
        self._data_stores = data_stores or DataStores()
        self._gamebanana_client = gamebanana_client
        self._entity_audit_store = entity_audit_store
        self._collab_lobby_overrides = collab_lobby_overrides
        self._collab_lobby_store = collab_lobby_store
        self._manual_record_fields = (
            () if inspection_report is None else manual_record_fields(inspection_report)
        )
        self._record_editor = RecordEditorController(
            self,
            self._data_stores.records,
            client=sheet_client,
            source=sheet_source,
            manual_fields=self._manual_record_fields,
        )
        self._record_worker: Worker[None] | None = None
        self._campaign_catalog = campaign_catalog
        self._campaigns = self._campaign_catalog.campaigns
        self._hidden_campaigns = self._campaign_catalog.hidden_campaigns
        self._campaigns_by_dir = {
            str(camp.directory): camp
            for camp in (*self._campaigns, *self._hidden_campaigns)
            if isinstance(camp, campaigns.ModCampaign)
        }
        self._lobby_campaign_cache: dict[MapKey, tuple[campaigns.Campaign, ...]] = {}
        self._routable_maps = {
            level.sid: (level, LevelSide.A)
            for camp in (*self._campaigns, *self._hidden_campaigns)
            for level in camp.levels
        }
        self._dialogs = self._campaign_catalog.dialogs
        self._localization = Localization(self._dialogs, self._dialog_lang)
        self._browser_warnings = diagnostics
        self._showing_hidden_campaigns = not self._campaigns and bool(self._hidden_campaigns)
        self._selected_campaign = next(iter(self._displayed_campaigns), None)
        self._collab_order = CollabMapOrderController(
            self._data_stores.catalog,
            on_map_warning=self._notify_map_warning,
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id='campaign-panel'):
                with Horizontal(id=CAMPAIGN_LIST_HEADER_ID):
                    yield Static(self._campaign_collection_title, id=CAMPAIGN_LIST_TITLE_ID)
                    yield Button(
                        f'消息（{self.diagnostic_count}）',
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
                            self._dialog_lang,
                            self._dialogs,
                            self._campaign_source_names(initial_campaign),
                        ),
                        id=DETAIL_ID,
                    )
                    yield Vertical(*self._map_widgets(initial_campaign), id=MAP_DETAIL_ID)
                else:
                    yield Static(Text('没有可加载的 campaign 地图。'), id=DETAIL_ID)
        yield Footer()

    def action_records(self) -> None:
        """Browse local records without reading the selected map or save."""
        self.push_screen(  # pyright: ignore[reportUnusedCallResult]
            RecordListScreen(
                self._record_editor,
                max_widths=self._record_list_max_widths,
                settings_store=self._settings_store,
            ),
            self._record_list_closed,
        )

    def _record_list_closed(self, widths: RecordListMaxWidths | None) -> None:
        if widths is not None:
            self._record_list_max_widths = widths

    def _campaign_label(self, camp: campaigns.Campaign) -> Text:
        name = map_names.campaign_name(camp, self._dialogs, self._dialog_lang)
        label = Text(f'• {name}')
        label.append(f'  ({camp.map_count} 图)', style='dim')  # pyright: ignore[reportUnusedCallResult]
        source_count = len(set(self._campaign_source_names(camp)))
        if source_count > 1:
            label.append(f'  [{source_count} 个 Mod]', style='yellow')  # pyright: ignore[reportUnusedCallResult]
        return label

    def _campaign_source_names(self, camp: campaigns.Campaign) -> tuple[str, ...]:
        """Return physical source names in stable first-map order."""
        return tuple(map_names.source_name(map_file) for map_file in camp.iter_maps())

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
        button.set_class(self._campaign_button_is_hidden(direction), '-hidden')  # pyright: ignore[reportUnusedCallResult]
        return button

    def _campaign_button_is_hidden(self, direction: int) -> bool:
        """Return whether a collection direction has no destination."""
        if direction < 0:
            return not self._showing_hidden_campaigns or not self._campaigns
        return self._showing_hidden_campaigns or not self._hidden_campaigns

    def _update_campaign_collection_buttons(self) -> None:
        """Show only the fixed-position controls whose destination exists."""
        for button in self.query(campaign_list.CampaignListToggle):
            button.set_class(self._campaign_button_is_hidden(button.direction), '-hidden')  # pyright: ignore[reportUnusedCallResult]

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
        campaign_list_widget.focus()  # pyright: ignore[reportUnusedCallResult]
        self.query_one(f'#{CAMPAIGN_LIST_TITLE_ID}', Static).update(self._campaign_collection_title)
        self._update_campaign_collection_buttons()
        self.query_one(f'#{DETAIL_ID}', Static).update(
            catalog.format_campaign_summary(
                self._selected_campaign,
                self._dialog_lang,
                self._dialogs,
                self._campaign_source_names(self._selected_campaign),
            )
        )
        await self._refresh_map_detail()

    def _map_list(
        self,
        maps: Iterable[tuple[Level, LevelSide]],
        *,
        item_factory: map_list.MapItemFactory | None = None,
        extra_items: Iterable[ListItem] = (),
    ) -> map_list.MapList:
        return map_list.MapList(
            catalog.maps_by_path(maps),
            self._dialog_lang,
            self._dialogs,
            self._save_slot,
            map_names.source_name,
            item_factory=item_factory,
            extra_items=extra_items,
        )

    def on_mount(self) -> None:
        self._save_switch_state(None if self._save_slot is None else self._save_slot.number, False)
        self.call_after_refresh(self._start_collab_order_loading)  # pyright: ignore[reportUnusedCallResult]

    @on(Button.Pressed, f'#{BROWSER_WARNINGS_BUTTON_ID}')
    async def show_browser_warnings(self) -> None:
        """Open the details behind the warning count shown in the campaign header."""
        await self.action_show_messages()

    @property
    def loading_warnings(self) -> tuple[str, ...]:
        return self._browser_warnings

    def diagnostics_changed(self) -> None:
        for screen in self.screen_stack:
            for button in screen.query(f'#{BROWSER_WARNINGS_BUTTON_ID}').results(Button):
                button.label = f'消息（{self.diagnostic_count}）'
                button.disabled = not self.diagnostic_count

    def on_unmount(self) -> None:
        self._collab_order.cancel()
        if self._save_switch is not None:
            self._save_switch.close()

    def _start_collab_order_loading(self) -> None:
        self._collab_order.start(self)

    async def action_quit(self) -> None:
        if self._record_editor.background_sync.pending:
            self.notify('后台同步尚未结束，请完成后再退出。', severity='warning')
            return
        self.exit()

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
                    use_collab_order=isinstance(
                        camp, (campaigns.CollabHubCampaign, campaigns.CollabSubCampaign)
                    ),
                )
            )
        return widgets

    def _collab_lobby_list(self, camp: campaigns.Campaign) -> map_list.MapList:
        """Render 0-Lobbies maps, adding lazy expansion only to actual lobbies."""

        def lobby_item(
            level: Level,
            sides: map_list.SideGroup,
            lang: str,
            dialogs: Mapping[str, Mapping[str, str]],
            save_slot: SaveProgress | None,
            source_name_for: Callable[[Map], str],
        ) -> map_list.MapItem:
            if not level.is_lobby:
                if len(sides) > 1:
                    return map_list.SideMapItem(
                        level, sides, lang, dialogs, save_slot, source_name_for
                    )
                return map_list.MapItem(level, sides[0], lang, dialogs, save_slot, source_name_for)
            submission_maps = Vertical(classes='lobby-maps')
            if len(sides) > 1:
                return collab_list.SideLobbyMapItem(
                    level,
                    sides,
                    lang,
                    dialogs,
                    save_slot,
                    source_name_for,
                    submission_maps,
                )
            return collab_list.LobbyMapItem(
                level,
                sides[0],
                lang,
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

    def _load_lobby_journal_refs(self, loaded_map: Map) -> collab.JournalRefs:
        """Collect UI diagnostics while the cache resolves one lobby map."""
        with self._map_string_warnings(loaded_map):
            return self._data_stores.catalog.journal_refs(loaded_map)

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
                    self._dialog_lang,
                    self._dialogs,
                    self._save_slot,
                    map_names.source_name,
                )
            )
            self._collab_order.start(self.query_one(f'#{MAP_DETAIL_ID}', Vertical))
        else:
            await item.submission_maps.mount(Static('未发现有效的大厅地图集。', classes='dim'))

    async def _resolve_lobby_campaigns(
        self,
        lobby: Level,
        side: LevelSide,
        loaded_map: Map,
    ) -> tuple[campaigns.Campaign, ...]:
        """Resolve one lobby projection from config or its lazily scanned journals."""
        cache_key = (lobby.sid, side)
        camps = self._lobby_campaign_cache.get(cache_key)
        if camps is None:
            configured_refs = self._collab_lobby_overrides.campaigns_for(lobby.sid, side)
            if configured_refs is not None:
                camps, missing = campaigns.resolve_campaign_refs(
                    self._campaigns_by_dir,
                    configured_refs,
                )
                for diagnostic in missing_ref_messages(missing, source='配置'):
                    self.notify(diagnostic, severity='warning')
            else:
                try:
                    refs = await asyncio.to_thread(self._load_lobby_journal_refs, loaded_map)
                except (OSError, ValueError) as error:
                    self.notify(f'无法读取大厅日志：{error}', severity='warning')
                    return ()
                else:
                    camps, missing = campaigns.resolve_campaign_refs(
                        self._campaigns_by_dir, refs.campaign_refs
                    )
                    for diagnostic in journal_messages(refs, missing):
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
            initial = tuple(camp.ref for camp in scanned)
        else:
            initial = configured
        map_file = item.map
        if not isinstance(map_file, ModMap):
            return
        mod = map_file.mod
        choices = map_actions.lobby_campaign_choices(
            self._campaigns_by_dir.values(),
            self._hidden_campaigns,
            mod,
            self._dialogs,
            self._dialog_lang,
            initial,
        )
        result = await self.push_screen_wait(
            map_actions.LobbyCampaignEditorScreen(
                lobby.sid,
                side.name,
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
        self._lobby_campaign_cache.pop((lobby.sid, side), None)  # pyright: ignore[reportUnusedCallResult]
        self.notify(message)
        if not item.collapsed:
            item.post_message(collab_list.LobbyMapItem.ExpansionRequested(item))  # pyright: ignore[reportUnusedCallResult]

    def _campaign_map_list(
        self,
        maps: Iterable[tuple[Level, LevelSide]],
        *,
        extra_items: Iterable[ListItem] = (),
        use_collab_order: bool = False,
    ) -> Widget:
        """Use Collab's journal ordering for known Collab map collections."""
        loaded_maps = tuple(maps)
        if any(not isinstance(level[side], ModMap) for level, side in loaded_maps):
            return self._map_list(loaded_maps, extra_items=extra_items)
        if use_collab_order or all(_is_source_collab_map(*item) for item in loaded_maps):
            return collab_list.CollabMapList(
                (loaded_maps,),
                self._dialog_lang,
                self._dialogs,
                self._save_slot,
                map_names.source_name,
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
                self._dialog_lang,
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
        self.call_after_refresh(self._start_collab_order_loading)  # pyright: ignore[reportUnusedCallResult]
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
            if self._record_worker is not None and not self._record_worker.is_finished:
                self.notify('当前记录编辑尚未结束。')
                return
            self._record_worker = self.run_worker(
                self._preview_record(event.item.level, event.item.side),
                name='record-preview',
                group='record-preview',
                exclusive=False,
            )
        elif event.button == RIGHT_MOUSE_BUTTON:
            item = event.item
            self.push_screen(  # pyright: ignore[reportUnusedCallResult]
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
                self.run_worker(  # pyright: ignore[reportUnusedCallResult]
                    self._edit_lobby_campaigns(item),
                    name='lobby-campaign-editor',
                    group='lobby-campaign-editor',
                    exclusive=True,
                )

    async def _preview_record(self, level: Level, side: LevelSide) -> None:
        """Edit a local record for one map after an explicit mouse gesture."""
        if self._save_switch is not None and self._save_switch.pending:
            self.notify('存档加载中，请稍候再编辑记录。', severity='warning')
            return
        active_map = level[side]
        map_info = active_map
        if not isinstance(active_map, ModMap):
            self.notify('原版地图不支持创建本地初见记录。', severity='warning')
            return
        mod = active_map.mod
        assert self._save_slot is not None
        save_slot = self._save_slot
        record_source = self._record_source(level, side)
        if record_source is None:
            return
        source, route = record_source
        while True:
            review, record_fields, field_hints = self._record_fields(source, route)
            collected_issues = review.collected_issues
            blocking_issues = _blocking_collected_entity_issues(collected_issues)
            if not blocking_issues:
                self._notify_missing_collected_entities(collected_issues)
                break
            if any(
                issue.entity_name is not None and issue.attrs is not None
                for issue in blocking_issues
            ):
                try:
                    if self._entity_audit_store is None:
                        self._entity_audit_store = EntityAuditStore()
                    await asyncio.to_thread(  # pyright: ignore[reportUnusedCallResult]
                        self._entity_audit_store.collect_entities,
                        collected_issues_report(
                            blocking_issues,
                            AuditSource(
                                scope='mod',
                                map_file=map_info.file_path.as_posix(),
                                map_name=select_translation(
                                    map_names.localized_names(level, side, self._dialogs),
                                    self._dialog_lang,
                                )
                                or level.sid,
                                mod_name=mod.metadata_name,
                                mod_file=str(mod.path),
                                package=source.map_data.package,
                                meta=map_mode_metadata(source.map_data.root),
                            ),
                        ).entities,
                    )
                except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                    self.notify(f'无法收录待审查实体组合：{error}', severity='error')
                    return
            result = await self.push_screen_wait(
                records.CollectedEntityRulesScreen(blocking_issues)
            )
            if result is not records.CollectedEntityRulesResult.REFRESH:
                return
        stats = save_slot.get_map_stats(level.sid, side)
        if stats is None or not is_recorded(stats):
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
        author_text = self._localization.get(f'{level.dialog_key}_author')
        author_source: records.AuthorSource = gamebanana
        if (
            mod.collab_id
            and _is_collab_submission_map(map_info.file_path)
            and author_text is not None
        ):
            author_source = author_text
        record = create_map_record(
            level,
            side,
            mod=mod,
            save_slot=save_slot,
            gamebanana=gamebanana,
            lang=self._dialog_lang,
            dialogs=self._dialogs,
            statistics=record_fields,
        )

        def make_editor(
            record: MapRecord, fields: tuple[ManualRecordField, ...]
        ) -> records.RecordEditorScreen:
            return records.RecordEditorScreen(
                record,
                manual_fields=fields,
                reference=self._record_ref(gamebanana),
                author_source=author_source,
                collab_tags=self._collab_tags(level),
                field_hints=field_hints,
                edit_route=lambda: self._start_route_editor(level, side),
                progress=map_record_progress(
                    save_slot.get_map_stats(level.sid, side),
                    review.stats,
                    is_in_progress=self._session_in_progress(save_slot, level.sid, side),
                ),
            )

        try:
            await self._record_editor.edit(record, make_editor)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.notify(f'编辑记录失败：{error}', severity='error')

    def _record_ref(self, gamebanana: GameBananaSubmission | None) -> tuple[str, str] | None:
        """Return the human-maintained reference text relevant to one record."""
        if gamebanana is None:
            return None
        description = _plain_html(gamebanana.description)
        return ('简介', description) if description else None

    def _collab_tags(self, level: Level) -> str | None:
        """Return localized collab tags for their dedicated record-grid field."""
        tags = self._localization.get(f'{level.dialog_key}_collabcreditstags')
        return tags if tags is not None and tags.strip() else None

    def _record_source(
        self, level: Level, side: LevelSide
    ) -> tuple[record_entities.MapEntityRecordSource, routes.MapRoute | None] | None:
        """Read one map and retain it while the user refreshes entity rules."""
        loaded_map = level[side]
        map_info = loaded_map
        try:
            route = self._data_stores.routes.load(map_info.file_path.as_posix())
            saved_stats = (
                None if self._save_slot is None else self._save_slot.get_map_stats(level.sid, side)
            )
            with self._map_string_warnings(loaded_map):
                source = record_entities.MapEntityRecordSource.from_map(
                    loaded_map,
                    frozenset() if saved_stats is None else saved_stats.collected_strawberries,
                    excluded_entities=frozenset() if route is None else route.excluded_entities,
                )
        except ValueError as error:
            self.notify(f'无法读取地图实体统计：{error}', severity='warning')
            return None
        return source, route

    def _session_in_progress(self, slot: SaveProgress, sid: str, side: LevelSide) -> bool:
        """Opt to skip invalid retained sessions and retain their diagnostic in the UI."""
        return slot.is_in_progress(
            sid,
            side,
            on_error=lambda error: self.notify(str(error), severity='warning', markup=False),
        )

    @contextmanager
    def _map_string_warnings(self, map_file: Map) -> Generator[None]:
        """Retain map decoding warnings in the UI without swallowing unrelated warnings."""
        with map_string_warnings(lambda warning: self._notify_map_warning(map_file, warning)):
            yield

    def _notify_map_warning(self, map_file: Map, warning: MapStringDecodeWarning) -> None:
        """Retain source-qualified decoding diagnostics in the runtime message list."""
        if isinstance(map_file, ModMap):
            source = map_file.mod.path
        elif isinstance(map_file, VanillaMap):
            source = map_file.content_dir
        else:
            source = type(map_file).__name__
        self.notify(
            f'{source} / {map_file.file_path}：{warning}',
            severity='warning',
            markup=False,
        )

    def _record_fields(
        self, source: record_entities.MapEntityRecordSource, route: routes.MapRoute | None
    ) -> tuple[record_entities.MapEntityRecordReview, dict[str, CellValue], dict[str, str]]:
        """Summarize current rules and route corrections, warning about duplicate entity IDs."""
        review = source.eval_rules(variant_review_loader=self._variant_review_checker)
        if review.stats.duplicate_instance_ids:
            ids = '、'.join(
                str(instance_id)
                for instance_id in sorted(
                    review.stats.duplicate_instance_ids,
                    key=lambda instance_id: (instance_id.room, instance_id.entity_id),
                )
            )
            self.notify(
                f'地图 {source.map_data.package} 存在重复实体 ID：{ids}；'
                '相关收集情况无法自动确认。',
                severity='warning',
                markup=False,
            )
        return (
            review,
            record_entities.record_fields(review.stats, route),
            _select_conflict_hints(review.stats.select_conflicts),
        )

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
        self, issues: tuple[CollectedEntityReviewIssue, ...]
    ) -> None:
        """Warn about saved IDs absent from the current map version without blocking recording."""
        missing = tuple(
            issue
            for issue in issues
            if isinstance(issue, CollectedEntityRuleIssue)
            and issue.status is CollectedEntityRuleIssueStatus.NOT_FOUND
        )
        if missing:
            ids = '、'.join(
                f'{issue.collected_id.room}:{issue.collected_id.entity_id}' for issue in missing
            )
            self.notify(
                f'当前地图未找到已收集实体（可能是地图版本变化）：{ids}', severity='warning'
            )

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

    def _start_map_preview(self, level: Level, side: LevelSide) -> None:
        """Start a read-only browser map preview without blocking the map browser."""
        self.run_worker(  # pyright: ignore[reportUnusedCallResult]
            self._preview_map(level, side, initial_mode=MapPreviewMode.PREVIEW),
            name='map-preview',
            group='map-preview',
            exclusive=False,
        )

    def _start_route_editor(self, level: Level, side: LevelSide) -> None:
        """Start an editable route preview without blocking the map browser."""
        self.run_worker(  # pyright: ignore[reportUnusedCallResult]
            self._preview_map(level, side, initial_mode=MapPreviewMode.EDIT_ROUTE),
            name='route-editor',
            group='map-preview',
            exclusive=False,
        )

    def _route_saved(self, route: routes.MapRoute) -> None:
        """Refresh any still-open editor for the map whose route was persisted."""
        for screen in self.screen_stack:
            if isinstance(screen, records.RecordEditorScreen):
                screen.post_message(records.RecordEditorScreen.RouteSaved(route))  # pyright: ignore[reportUnusedCallResult]

    async def _preview_map(
        self,
        level: Level,
        side: LevelSide,
        *,
        initial_mode: MapPreviewMode,
    ) -> None:
        """Open one selected map in the requested initial interaction mode."""
        if self._save_switch is not None and self._save_switch.pending:
            self.notify('存档加载中，请稍候再预览地图。', severity='warning')
            return
        active_map = level[side]
        map_info = active_map
        try:
            classification_rules = load_entity_rule_layers()
            entrance_rules = load_map_entrance_rule_layers()
            with self._map_string_warnings(active_map):
                layout = map_layout.MapLayout.from_map(
                    active_map, entity_rules=classification_rules, entrance_rules=entrance_rules
                )
            saved_route = self._data_stores.routes.load(map_info.file_path.as_posix())
        except ValueError as error:
            self.notify(str(error), severity='warning')
            return
        first_clear_rooms: tuple[str, ...] = ()
        enders_blender_save: EndersBlenderSave | None = None
        if self._save_reader is not None and self._save_slot is not None:
            try:
                enders_blender_save = self._save_reader.load_first_clear(self._save_slot.number)
                if enders_blender_save is not None:
                    stats = enders_blender_save.first_clear.get(room_key(level.sid, side))
                    first_clear_rooms = () if stats is None else tuple(stats.room_order)
            except ValueError as error:
                enders_blender_save = None
                self.notify(str(error), severity='warning')
        try:
            await MapPreview(
                active_map,
                layout,
                first_clear_rooms=first_clear_rooms,
                saved_route=saved_route,
                route_store=self._data_stores.routes,
                first_clear_lookup=(
                    None
                    if enders_blender_save is None
                    else lambda sid, side: enders_blender_save.first_clear.get(room_key(sid, side))
                ),
                initial_mode=initial_mode,
                title=map_names.display_name(level, side, self._dialogs, self._dialog_lang),
                dialogs=self._dialogs,
                level_side=(level, side),
                routable_maps=self._routable_maps,
                classification_rules=classification_rules,
                entrance_rules=entrance_rules,
                statistic_rules=entity_stats.load_entity_stat_rules(classification_rules),
                on_map_warning=self._notify_map_warning,
                on_route_saved=self._route_saved,
            ).preview()
        except (MapPreviewError, ValueError) as error:
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
        if self._save_switch is None:
            self.notify('未提供存档目录。')
            return
        self._save_switch.request(step)

    def _save_switch_state(self, number: int | None, loading: bool) -> None:
        """Show the target immediately and mask old statistics, including newly mounted rows."""
        self.sub_title = (
            '' if number is None else f'存档 {number}' + (' · 加载中…' if loading else '')
        )
        self.screen_stack[0].set_class(loading, 'save-loading')  # pyright: ignore[reportUnusedCallResult]
        for item in self.query(map_list.MapItem):
            item.set_save_loading_number(number if loading else None)

    def on_map_item_mounted(self, event: map_list.MapItem.Mounted) -> None:
        """Keep new campaign and Collab rows in sync with an in-flight slot selection."""
        if self._save_switch is not None and self._save_switch.pending:
            event.item.set_save_loading_number(self._save_switch.target)

    async def _apply_save_slot(self, slot: SaveProgress) -> None:
        """Refresh statistics in place before revealing the newly selected snapshot."""
        self._save_slot = slot
        await self._refresh_save_stats()


async def browse_maps(
    campaign_catalog: campaigns.CampaignCatalog,
    settings_store: SettingsStore | None = None,
    save_reader: SaveProgressReader | None = None,
    save_slot: SaveProgress | None = None,
    inspection_report: InspectionReport | None = None,
    sheet_client: TencentSmartSheetClient | None = None,
    sheet_source: str | None = None,
    *,
    diagnostics: tuple[str, ...] = (),
) -> None:
    """Run one exclusive record-writing browser; settings require a supplied store."""
    with record_writer_lock():
        collab_lobby_store = CollabLobbyOverrideStore(
            can_write_shared=SHARED_COLLAB_LOBBIES_WRITABLE
        )
        async with AsyncExitStack() as stack:
            gamebanana_client = await stack.enter_async_context(GameBananaClient())
            if isinstance(sheet_client, TencentSmartSheetClient):
                await stack.enter_async_context(sheet_client)  # pyright: ignore[reportUnusedCallResult]
            await MapBrowserApp(
                campaign_catalog,
                diagnostics=diagnostics,
                settings_store=settings_store,
                save_reader=save_reader,
                save_slot=save_slot,
                gamebanana_client=gamebanana_client,
                inspection_report=inspection_report,
                sheet_client=sheet_client,
                sheet_source=sheet_source,
                collab_lobby_overrides=collab_lobby_store.load(),
                collab_lobby_store=collab_lobby_store,
            ).run_async()
