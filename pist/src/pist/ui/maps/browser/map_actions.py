"""Map actions and explicit Collab lobby projection dialogs."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList, Static

from berries.game import campaigns, mods
from berries.game.content import MAPS_DIR
from pist.ui.context_menu import ContextMenuScreen


def campaign_ref(camp: campaigns.Campaign) -> str:
    """Return one Campaign directory in CollabUtils2 journal-reference form."""
    return camp.directory.relative_to(MAPS_DIR).as_posix()


def lobby_campaign_choices(
    camps: Iterable[campaigns.Campaign],
    hidden_campaigns: Iterable[campaigns.Campaign],
    current_mod: mods.InstalledMod,
    catalog: campaigns.CampaignCatalog,
    dialogs: Mapping[str, Mapping[str, str]],
    languages: Iterable[str],
    initial: Iterable[str],
) -> tuple[tuple[str, str], ...]:
    """Prioritize hidden Campaigns supplied by the current lobby's physical Mod."""
    hidden_campaign_ids = {id(camp) for camp in hidden_campaigns}
    candidates = sorted(
        camps,
        key=lambda camp: (
            not (id(camp) in hidden_campaign_ids and catalog.campaign_uses_mod(camp, current_mod)),
            camp.directory.as_posix().casefold(),
        ),
    )
    choices = {campaign_ref(camp): camp.display_name(dialogs, languages) for camp in candidates}
    for ref in initial:
        choices.setdefault(ref, f'{ref}（当前未找到）')
    return tuple(choices.items())


class MapAction(StrEnum):
    """An operation selected from one map row's context menu."""

    EDIT_ROUTE = 'edit_route'
    PREVIEW = 'preview'
    EDIT_LOBBY = 'edit_lobby'


class MapActionScreen(ContextMenuScreen[MapAction | None]):
    """Small context menu for actions on one map row."""

    def __init__(self, screen_x: int, screen_y: int, *, can_edit_lobby: bool) -> None:
        super().__init__(screen_x, screen_y)
        self._can_edit_lobby = can_edit_lobby

    def compose(self) -> ComposeResult:
        with Vertical(id='map-action-dialog', classes='context-menu-dialog'):
            yield Button('编辑路线', id='map-action-edit-route')
            yield Button('预览地图', id='map-action-preview')
            if self._can_edit_lobby:
                yield Button('编辑地图集', id='map-action-edit-lobby')

    @on(Button.Pressed)
    def choose_action(self, event: Button.Pressed) -> None:
        match event.button.id:
            case 'map-action-edit-route':
                action = MapAction.EDIT_ROUTE
            case 'map-action-preview':
                action = MapAction.PREVIEW
            case 'map-action-edit-lobby':
                action = MapAction.EDIT_LOBBY
            case _:
                return
        self.dismiss(action)


class LobbyCampaignEditTarget(StrEnum):
    """The configuration layer changed by the lobby editor."""

    LOCAL = 'local'
    SHARED = 'shared'
    RESET_LOCAL = 'reset_local'
    RESET_SHARED = 'reset_shared'


@dataclass(frozen=True, slots=True)
class LobbyCampaignEdit:
    """A lobby projection edit and its persistence target."""

    target: LobbyCampaignEditTarget
    campaigns: tuple[str, ...] = ()


class LobbyCampaignEditorScreen(ModalScreen[LobbyCampaignEdit | None]):
    """Select the Campaigns projected beneath one Collab lobby side."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(
        self,
        lobby: str,
        side: str,
        choices: Iterable[tuple[str, str]],
        selected: Iterable[str],
        *,
        can_reset_local: bool,
        can_reset_shared: bool,
        can_write_shared: bool,
        using_scanned_initial: bool,
    ) -> None:
        super().__init__()
        self._lobby = lobby
        self._side = side
        self._choices = tuple(choices)
        selected_set = frozenset(selected)
        self._selected = tuple(ref for ref, _ in self._choices if ref in selected_set)
        self._can_reset_local = can_reset_local
        self._can_reset_shared = can_reset_shared
        self._can_write_shared = can_write_shared
        self._using_scanned_initial = using_scanned_initial

    def compose(self) -> ComposeResult:
        with Vertical(id='lobby-campaign-editor-dialog'):
            yield Static(f'{self._lobby} · {self._side} 面', classes='dialog-title')
            if self._using_scanned_initial:
                yield Static('当前未配置；已用日志扫描结果作为初始选择。', classes='dim')
            yield SelectionList[str](
                *(
                    (
                        Text.assemble(name, (f'  ({ref})', 'dim')),
                        ref,
                        ref in self._selected,
                    )
                    for ref, name in self._choices
                ),
                id='lobby-campaign-selection',
            )
            with Horizontal(id='lobby-campaign-editor-actions'):
                yield Button('保存', id='lobby-campaign-save-local', variant='primary')
                if self._can_write_shared:
                    yield Button('保存到共享配置', id='lobby-campaign-save-shared')
                yield Button(
                    '重置',
                    id='lobby-campaign-remove',
                    disabled=not self._can_reset_local,
                )
                if self._can_write_shared:
                    yield Button(
                        '重置共享配置',
                        id='lobby-campaign-remove-shared',
                        disabled=not self._can_reset_shared,
                    )
                yield Button('取消', id='lobby-campaign-cancel')

    @on(Button.Pressed)
    def finish(self, event: Button.Pressed) -> None:
        match event.button.id:
            case 'lobby-campaign-save-local' | 'lobby-campaign-save-shared':
                selected = frozenset(self.query_one(SelectionList).selected)
                camps = tuple(ref for ref, _ in self._choices if ref in selected)
                target = (
                    LobbyCampaignEditTarget.LOCAL
                    if event.button.id == 'lobby-campaign-save-local'
                    else LobbyCampaignEditTarget.SHARED
                )
                self.dismiss(LobbyCampaignEdit(target, camps))
            case 'lobby-campaign-remove':
                self.dismiss(LobbyCampaignEdit(LobbyCampaignEditTarget.RESET_LOCAL))
            case 'lobby-campaign-remove-shared':
                self.dismiss(LobbyCampaignEdit(LobbyCampaignEditTarget.RESET_SHARED))
            case _:
                self.dismiss(None)
