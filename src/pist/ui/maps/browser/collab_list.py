"""Collab journal-list loading widget."""

from collections.abc import Iterable, Mapping

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import ListItem, LoadingIndicator, Static

from pist.game.levels import Level, LevelSide
from pist.game.saves import SaveSlot

from .map_list import LEFT_MOUSE_BUTTON, MapItem, MapSideButton, SideGroup


class CollabMapList(Vertical):
    """A Collab map list that shows progress until its journal order is available."""

    def __init__(
        self,
        map_groups: Iterable[Iterable[tuple[Level, LevelSide]]],
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
        *,
        extra_items: Iterable[ListItem] = (),
    ) -> None:
        super().__init__(classes='collab-map-list is-loading')
        self.map_groups = tuple(tuple(group) for group in map_groups)
        self.maps = tuple(map_ref for group in self.map_groups for map_ref in group)
        self.languages = tuple(languages)
        self.dialogs = dialogs
        self.save_slot = save_slot
        self.extra_items = tuple(extra_items)
        self.icon_order: tuple[tuple[Level, LevelSide], ...] | None = None
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

    @on(events.Click)
    def stop_click_bubbling(self, event: events.Click) -> None:
        """Keep nested map gestures from selecting the containing Collab lobby."""
        event.stop()


class LobbyMapToggle(Static):
    """The independent expand/collapse control for a Collab lobby map item."""

    class Toggled(Message):
        """The user requested that the containing lobby change expansion state."""

    def __init__(self) -> None:
        super().__init__('▸', classes='lobby-map-toggle')

    @on(events.Click)
    def toggle(self, event: events.Click) -> None:
        event.stop()
        if event.button == LEFT_MOUSE_BUTTON and event.chain == 1:
            self.post_message(self.Toggled())


class LobbyMapItem(MapItem):
    """A normal map item whose dedicated icon reveals its Collab submission maps."""

    collapsed = reactive(True)

    class ExpansionRequested(Message):
        """The current lobby side needs its journal map list projected."""

        def __init__(self, item: LobbyMapItem) -> None:
            self.item = item
            super().__init__()

    def __init__(
        self,
        level: Level,
        side: LevelSide,
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
        submission_maps: Widget,
        *children: Widget,
    ) -> None:
        self._toggle = LobbyMapToggle()
        self.submission_maps = submission_maps
        super().__init__(
            level,
            side,
            languages,
            dialogs,
            save_slot,
            submission_maps,
            *children,
            leading=self._toggle,
            classes='lobby-map-item -collapsed',
            marker='  ',
        )

    def watch_collapsed(self, collapsed: bool) -> None:
        self._toggle.update('▸' if collapsed else '▾')
        self.set_class(collapsed, '-collapsed')

    @on(LobbyMapToggle.Toggled)
    def toggle_collapsed(self, event: LobbyMapToggle.Toggled) -> None:
        event.stop()
        self.collapsed = not self.collapsed
        if not self.collapsed:
            self.post_message(self.ExpansionRequested(self))


class SideLobbyMapItem(LobbyMapItem):
    """A Collab lobby that retains its submission list while switching map sides."""

    def __init__(
        self,
        level: Level,
        sides: SideGroup,
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
        submission_maps: Widget,
    ) -> None:
        self._sides = tuple(sorted(sides))
        self._index = 0
        self._save_slot = save_slot
        self._previous = MapSideButton(-1)
        self._next = MapSideButton(1)
        controls = Horizontal(self._previous, self._next, classes='map-side-controls')
        super().__init__(
            level,
            self._sides[0],
            languages,
            dialogs,
            save_slot,
            submission_maps,
            controls,
        )
        self.add_class('side-map-item')
        self._update_side_buttons()

    @on(MapSideButton.Clicked)
    def switch_side(self, event: MapSideButton.Clicked) -> None:
        next_index = self._index + event.direction
        if 0 <= next_index < len(self._sides):
            self._index = next_index
            self.set_side(self._sides[self._index], self._save_slot)
            self._update_side_buttons()
            if not self.collapsed:
                self.post_message(self.ExpansionRequested(self))

    def _update_side_buttons(self) -> None:
        self._previous.set_class(self._index == 0, '-hidden')
        self._next.set_class(self._index == len(self._sides) - 1, '-hidden')

    def refresh_stats(self, save_slot: SaveSlot | None) -> None:
        """Refresh the current side and retain its save slot for later side switches."""
        self._save_slot = save_slot
        super().refresh_stats(save_slot)
