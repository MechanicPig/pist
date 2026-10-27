"""Reusable map-list widgets for the map browser catalog."""

from collections.abc import Callable, Iterable, Mapping

from textual import events, on
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ListItem, ListView, Static

from berries.game import maps as backend
from berries.game.content import ContentPath
from berries.game.levels import Level, LevelSide
from berries.game.maps import Map
from pist.saves import SaveProgress
from pist.ui.mouse import LEFT_MOUSE_BUTTON

from .catalog import map_detail_lines, map_save_stats, map_title

type SideGroup = tuple[LevelSide, *tuple[LevelSide, ...]]

type MapItemFactory = Callable[
    [
        Level,
        SideGroup,
        str,
        Mapping[str, Mapping[str, str]],
        SaveProgress | None,
        Callable[[Map], str],
    ],
    'MapItem',
]


def _map_side_groups(
    maps: Iterable[tuple[Level, LevelSide]],
) -> tuple[tuple[Level, SideGroup], ...]:
    """Group selected sides by their owning Level without creating a domain wrapper."""
    grouped: dict[int, tuple[Level, list[LevelSide]]] = {}
    for level, side in maps:
        grouped.setdefault(id(level), (level, []))[1].append(side)
    return tuple((level, (sides[0], *sides[1:])) for level, sides in grouped.values())


class MapItem(ListItem):
    """One selectable active map item in the campaign detail pane."""

    class Mounted(Message):
        """Request the current save-loading label for a newly mounted map row."""

        def __init__(self, item: MapItem) -> None:
            super().__init__()
            self.item = item

    class Clicked(Message):
        """A map item was clicked with a specific mouse button and click count."""

        def __init__(
            self,
            item: MapItem,
            button: int,
            chain: int,
            screen_x: int,
            screen_y: int,
        ) -> None:
            super().__init__()
            self.item = item
            self.button = button
            self.chain = chain
            self.screen_x = screen_x
            self.screen_y = screen_y

    def __init__(
        self,
        level: Level,
        side: LevelSide,
        lang: str,
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveProgress | None,
        source_name_for: Callable[[Map], str],
        *children: Widget,
        leading: Widget | None = None,
        classes: str | None = None,
        marker: str = '• ',
    ) -> None:
        self.level = level
        self.side = side
        self._lang = lang
        self._dialogs = dialogs
        self._marker = marker
        self._source_name_for = source_name_for
        self._title = Static(
            map_title(level, side, lang, dialogs, marker=marker), classes='map-item-title'
        )
        self._content = Static(
            map_detail_lines(level, side, lang, dialogs, source_name_for(level[side])),
            classes='map-item-content',
        )
        self._save_stats = Static(classes='map-item-content map-item-save-stats')
        self._save_loading = Static(classes='map-item-save-loading')
        self._update_save_stats(save_slot)
        leading_children = () if leading is None else (leading,)
        super().__init__(
            *leading_children,
            self._title,
            self._content,
            self._save_stats,
            self._save_loading,
            *children,
            classes=classes,
        )

    def on_mount(self) -> None:
        self.post_message(self.Mounted(self))  # pyright: ignore[reportUnusedCallResult]

    def set_save_loading_number(self, number: int | None) -> None:
        """Update the loading placeholder without exposing the previous slot's data."""
        self._save_loading.update('' if number is None else f'  存档 {number}：加载中…')

    @property
    def file_path(self) -> ContentPath:
        """Return the current side's exact virtual path without reading its source."""
        return self.map.file_path

    @property
    def map(self) -> Map:
        """Return the concrete map asset for the currently selected Side."""
        return self.level[self.side]

    @property
    def state_key(self) -> str:
        """Return a stable map-family key for an intentional list replacement."""
        if self.side is not LevelSide.A:
            return self.file_path.as_posix()
        return self.level.sid

    def refresh_stats(self, save_slot: SaveProgress | None) -> None:
        """Update only this item's dynamic native save statistics."""
        self._update_save_stats(save_slot)

    def _update_save_stats(self, save_slot: SaveProgress | None) -> None:
        self._save_stats.update(map_save_stats(self.level.sid, self.side, save_slot))
        self._save_stats.set_class(save_slot is None, 'no-save-slot')  # pyright: ignore[reportUnusedCallResult]

    def set_side(self, side: LevelSide, save_slot: SaveProgress | None) -> None:
        """Switch this row to another available side of the same map."""
        self.side = side
        self._title.update(
            map_title(self.level, side, self._lang, self._dialogs, marker=self._marker)
        )
        self._content.update(
            map_detail_lines(
                self.level, side, self._lang, self._dialogs, self._source_name_for(self.map)
            )
        )
        self.refresh_stats(save_slot)

    @on(events.Click)
    def map_clicked(self, event: events.Click) -> None:
        """Expose map-specific mouse gestures after ListItem handles selection."""
        screen_x = event.x if event.screen_x is None else event.screen_x
        screen_y = event.y if event.screen_y is None else event.screen_y
        self.post_message(self.Clicked(self, event.button, event.chain, screen_x, screen_y))  # pyright: ignore[reportUnusedCallResult]


class MapSideButton(Static):
    """One non-selecting direction control for a map's available sides."""

    class Clicked(Message):
        def __init__(self, direction: int) -> None:
            self.direction = direction
            super().__init__()

    def __init__(self, direction: int) -> None:
        self.direction = direction
        super().__init__('◂' if direction < 0 else '▸', classes='map-side-button')

    @on(events.Click)
    def click(self, event: events.Click) -> None:
        event.stop()  # pyright: ignore[reportUnusedCallResult]
        if event.button == LEFT_MOUSE_BUTTON:
            self.post_message(self.Clicked(self.direction))  # pyright: ignore[reportUnusedCallResult]


class SideMapItem(MapItem):
    """A map row that switches between the available A/B/C sides in place."""

    def __init__(
        self,
        level: Level,
        sides: SideGroup,
        lang: str,
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveProgress | None,
        source_name_for: Callable[[Map], str],
    ) -> None:
        self._sides = tuple(sorted(sides))
        self._index = 0
        self._save_slot = save_slot
        self._previous = MapSideButton(-1)
        self._next = MapSideButton(1)
        self._controls = Horizontal(self._previous, self._next, classes='map-side-controls')
        super().__init__(
            level,
            self._sides[0],
            lang,
            dialogs,
            save_slot,
            source_name_for,
            self._controls,
        )
        self.add_class('side-map-item')  # pyright: ignore[reportUnusedCallResult]
        self._update_buttons()

    @on(MapSideButton.Clicked)
    def switch_side(self, event: MapSideButton.Clicked) -> None:
        next_index = self._index + event.direction
        if 0 <= next_index < len(self._sides):
            self._index = next_index
            self.set_side(self._sides[self._index], self._save_slot)
            self._update_buttons()

    def _update_buttons(self) -> None:
        self._previous.set_class(self._index == 0, '-hidden')  # pyright: ignore[reportUnusedCallResult]
        self._next.set_class(self._index == len(self._sides) - 1, '-hidden')  # pyright: ignore[reportUnusedCallResult]

    def refresh_stats(self, save_slot: SaveProgress | None) -> None:
        """Refresh the current side and retain its save slot for later side switches."""
        self._save_slot = save_slot
        super().refresh_stats(save_slot)

    @property
    def state_key(self) -> str:
        """Keep all switchable sides under their shared A-side identity."""
        return self.level.sid

    def restore_selected_side(self, map_file: backend.MapFilePath) -> None:
        """Restore the selected side after replacing a Collab map list."""
        for i, side in enumerate(self._sides):
            if self.level[side].file_path == map_file:
                self._index = i
                self.set_side(side, self._save_slot)
                self._update_buttons()
                break


class MapList(ListView):
    """A map list with a caller-defined stable display order."""

    def __init__(
        self,
        maps: Iterable[tuple[Level, LevelSide]],
        lang: str,
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveProgress | None,
        source_name_for: Callable[[Map], str],
        *,
        item_factory: MapItemFactory | None = None,
        extra_items: Iterable[ListItem] = (),
    ) -> None:
        self.maps = tuple(maps)
        self._dialogs = dialogs
        self._side_groups = _map_side_groups(self.maps)
        self._item_factory = item_factory
        self.source_name_for = source_name_for
        super().__init__(
            *(
                self.item_for(level, sides, lang, dialogs, save_slot, source_name_for)
                for level, sides in self._side_groups
            ),
            *extra_items,
            classes='map-list',
        )

    def item_for(
        self,
        level: Level,
        sides: SideGroup,
        lang: str,
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveProgress | None,
        source_name_for: Callable[[Map], str],
    ) -> MapItem:
        """Build the list item while preserving a specialized lobby item factory."""
        if self._item_factory is None:
            if len(sides) > 1:
                return SideMapItem(level, sides, lang, dialogs, save_slot, source_name_for)
            return MapItem(level, sides[0], lang, dialogs, save_slot, source_name_for)
        return self._item_factory(level, sides, lang, dialogs, save_slot, source_name_for)

    @property
    def map_items(self) -> tuple[MapItem, ...]:
        """Return this list's direct map rows, excluding maps nested in lobby rows."""
        return tuple(child for child in self.children if isinstance(child, MapItem))
