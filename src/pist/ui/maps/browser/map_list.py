"""Reusable map-list widgets for the map browser catalog."""

from collections.abc import Callable, Iterable, Mapping

from textual import events, on
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ListItem, ListView, Static

from pist.game import maps as game_maps
from pist.game.levels import Level, LevelSide, LoadedMap
from pist.game.saves import SaveSlot

from .catalog import map_detail_lines, map_title

LEFT_MOUSE_BUTTON = 1
type SideGroup = tuple[LevelSide, *tuple[LevelSide, ...]]

type MapItemFactory = Callable[
    [
        Level,
        SideGroup,
        Iterable[str],
        Mapping[str, Mapping[str, str]],
        SaveSlot | None,
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
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
        *children: Widget,
        leading: Widget | None = None,
        classes: str | None = None,
        marker: str = '• ',
    ) -> None:
        self.level = level
        self.side = side
        self._languages = languages
        self._dialogs = dialogs
        self._marker = marker
        self._title = Static(
            map_title(level, side, languages, dialogs, marker=marker), classes='map-item-title'
        )
        self._content = Static(
            map_detail_lines(level, side, languages, dialogs, save_slot),
            classes='map-item-content',
        )
        leading_children = () if leading is None else (leading,)
        super().__init__(*leading_children, self._title, self._content, *children, classes=classes)

    @property
    def map_info(self) -> game_maps.MapInfo:
        """Return the map data for callers that do not need its source package."""
        return self.loaded_map.map_info

    @property
    def loaded_map(self) -> LoadedMap:
        """Return the concrete map asset for the currently selected Side."""
        return self.level.maps_by_side[self.side]

    @property
    def state_key(self) -> str:
        """Return a stable map-family key for an intentional list replacement."""
        if self.side is not LevelSide.A:
            return self.map_info.file_path.as_posix()
        return self.level.sid

    def refresh_stats(self, save_slot: SaveSlot | None) -> None:
        """Update only this item's dynamic native save statistics."""
        self._content.update(
            map_detail_lines(self.level, self.side, self._languages, self._dialogs, save_slot)
        )

    def set_side(self, side: LevelSide, save_slot: SaveSlot | None) -> None:
        """Switch this row to another available side of the same map."""
        self.side = side
        self._title.update(
            map_title(self.level, side, self._languages, self._dialogs, marker=self._marker)
        )
        self.refresh_stats(save_slot)

    @on(events.Click)
    def map_clicked(self, event: events.Click) -> None:
        """Expose map-specific mouse gestures after ListItem handles selection."""
        screen_x = event.x if event.screen_x is None else event.screen_x
        screen_y = event.y if event.screen_y is None else event.screen_y
        self.post_message(self.Clicked(self, event.button, event.chain, screen_x, screen_y))


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
        event.stop()
        if event.button == LEFT_MOUSE_BUTTON:
            self.post_message(self.Clicked(self.direction))


class SideMapItem(MapItem):
    """A map row that switches between the available A/B/C sides in place."""

    def __init__(
        self,
        level: Level,
        sides: SideGroup,
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
    ) -> None:
        self._sides = tuple(sorted(sides))
        self._index = 0
        self._save_slot = save_slot
        self._previous = MapSideButton(-1)
        self._next = MapSideButton(1)
        self._controls = Horizontal(self._previous, self._next, classes='map-side-controls')
        super().__init__(level, self._sides[0], languages, dialogs, save_slot, self._controls)
        self.add_class('side-map-item')
        self._update_buttons()

    @on(MapSideButton.Clicked)
    def switch_side(self, event: MapSideButton.Clicked) -> None:
        next_index = self._index + event.direction
        if 0 <= next_index < len(self._sides):
            self._index = next_index
            self.set_side(self._sides[self._index], self._save_slot)
            self._update_buttons()

    def _update_buttons(self) -> None:
        self._previous.set_class(self._index == 0, '-hidden')
        self._next.set_class(self._index == len(self._sides) - 1, '-hidden')

    def refresh_stats(self, save_slot: SaveSlot | None) -> None:
        """Refresh the current side and retain its save slot for later side switches."""
        self._save_slot = save_slot
        super().refresh_stats(save_slot)

    @property
    def state_key(self) -> str:
        """Keep all switchable sides under their shared A-side identity."""
        return self.level.sid

    def restore_selected_side(self, map_file: game_maps.MapFilePath) -> None:
        """Restore the selected side after replacing a Collab map list."""
        for index, side in enumerate(self._sides):
            if self.level.maps_by_side[side].map_info.file_path == map_file:
                self._index = index
                self.set_side(side, self._save_slot)
                self._update_buttons()
                break


class MapList(ListView):
    """A map list with a caller-defined stable display order."""

    def __init__(
        self,
        maps: Iterable[tuple[Level, LevelSide]],
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
        *,
        item_factory: MapItemFactory | None = None,
        extra_items: Iterable[ListItem] = (),
    ) -> None:
        self.maps = tuple(maps)
        self._dialogs = dialogs
        self._side_groups = _map_side_groups(self.maps)
        self._item_factory = item_factory
        super().__init__(
            *(
                self.item_for(level, sides, languages, dialogs, save_slot)
                for level, sides in self._side_groups
            ),
            *extra_items,
            classes='map-list',
        )

    def item_for(
        self,
        level: Level,
        sides: SideGroup,
        languages: Iterable[str],
        dialogs: Mapping[str, Mapping[str, str]],
        save_slot: SaveSlot | None,
    ) -> MapItem:
        """Build the list item while preserving a specialized lobby item factory."""
        if self._item_factory is None:
            if len(sides) > 1:
                return SideMapItem(level, sides, languages, dialogs, save_slot)
            return MapItem(level, sides[0], languages, dialogs, save_slot)
        return self._item_factory(level, sides, languages, dialogs, save_slot)

    @property
    def map_items(self) -> tuple[MapItem, ...]:
        """Return this list's direct map rows, excluding maps nested in lobby rows."""
        return tuple(child for child in self.children if isinstance(child, MapItem))
