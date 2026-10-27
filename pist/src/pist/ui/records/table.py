"""Record table with captured column resizing and explicit row-move requests."""

from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import DataTable

from pist.ui.mouse import LEFT_MOUSE_BUTTON

MIN_COLUMN_WIDTH = 4
HEADER_TOOLTIPS = {
    'sync_state': '无本地变更只表示与上次确认基准一致，不代表远端当前未变化。',
    'confirmed_at': '核对时间使用本地时区。',
}


@dataclass(slots=True)
class ColumnDrag:
    """Origin of one ongoing column resize in screen coordinates."""

    key: str
    screen_x: int
    width: int
    last_width: int


@dataclass(slots=True)
class RowDrag:
    """Stable source identity and whether a vertical drag has actually started."""

    key: str
    screen_y: int
    original_keys: tuple[str, ...]
    active: bool = False


class RecordTable(DataTable[Text]):
    """Resize headers or request confirmed reordering by dragging numbered rows."""

    class RowMoveRequested(Message):
        """The player released a row over another numbered row; no data has changed yet."""

        def __init__(self, source_key: str, target_key: str, *, before: bool) -> None:
            super().__init__()
            self.source_key = source_key
            self.target_key = target_key
            self.before = before

    class ColumnResized(Message):
        """Preview or commit the width selected by a header drag."""

        def __init__(self, key: str, width: int, *, finished: bool) -> None:
            super().__init__()
            self.key = key
            self.width = width
            self.finished = finished

    def __init__(self, *, id: str) -> None:
        super().__init__(cursor_type='row', id=id)
        self._drag: ColumnDrag | None = None
        self._row_drag: RowDrag | None = None
        self._suppress_click = False
        self.reorder_keys: frozenset[str] = frozenset()
        self.reorder_enabled = True

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._suppress_click = False
        offset = event.get_content_offset(self)
        if event.button != LEFT_MOUSE_BUTTON or offset is None:
            return
        if offset.y >= self.header_height:
            if self.reorder_enabled and (row := self._row_at(event)) is not None:
                self._row_drag = RowDrag(
                    row[1],
                    event.screen_offset.y,
                    tuple(row.key.value for row in self.ordered_rows if row.key.value is not None),
                )
                self.capture_mouse()
            return
        position = offset.x + int(self.scroll_x)
        edge = 0
        for column in self.ordered_columns:
            edge += column.get_render_width(self)
            if edge - 2 <= position <= edge:
                assert column.key.value is not None
                self._drag = ColumnDrag(
                    column.key.value, event.screen_offset.x, column.width, column.width
                )
                self._suppress_click = True
                self.capture_mouse()
                event.stop()  # pyright: ignore[reportUnusedCallResult]
                event.prevent_default()  # pyright: ignore[reportUnusedCallResult]
                return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.tooltip = None
        if self._drag is not None:
            self._resize(event, finished=False)
            return
        if self._row_drag is not None:
            drag = self._row_drag
            if event.screen_offset.y != drag.screen_y:
                drag.active = True
            if drag.active:
                self._preview_row_drag(self._row_at(event))
                event.stop()  # pyright: ignore[reportUnusedCallResult]
                event.prevent_default()  # pyright: ignore[reportUnusedCallResult]
            return
        offset = event.get_content_offset(self)
        if offset is None or offset.y >= self.header_height:
            return
        position = offset.x + int(self.scroll_x)
        edge = 0
        for column in self.ordered_columns:
            edge += column.get_render_width(self)
            if position < edge:
                key = column.key.value
                self.tooltip = None if key is None else HEADER_TOOLTIPS.get(key)
                break

    def on_leave(self, event: events.Leave) -> None:
        self.tooltip = None

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._row_drag is not None and event.button == LEFT_MOUSE_BUTTON:
            drag = self._row_drag
            row = self._row_at(event)
            self.cancel_row_drag()
            if drag.active:
                self._suppress_click = True
                event.stop()  # pyright: ignore[reportUnusedCallResult]
                event.prevent_default()  # pyright: ignore[reportUnusedCallResult]
                keys = [row.key.value for row in self.ordered_rows]
                if (
                    self.reorder_enabled
                    and row is not None
                    and row[1] in self.reorder_keys
                    and row[1] != drag.key
                    and drag.key in self.reorder_keys
                    and drag.key in keys
                ):
                    source_index = keys.index(drag.key)
                    self.move_cursor(row=source_index, scroll=False)
                    self.post_message(  # pyright: ignore[reportUnusedCallResult]
                        self.RowMoveRequested(drag.key, row[1], before=row[0] < source_index)
                    )
            return
        if self._drag is not None and event.button == LEFT_MOUSE_BUTTON:
            self._resize(event, finished=True)
            self._drag = None
            self.release_mouse()

    def on_click(self, event: events.Click) -> None:
        if self._suppress_click:
            event.stop()  # pyright: ignore[reportUnusedCallResult]
            event.prevent_default()  # pyright: ignore[reportUnusedCallResult]
            self._suppress_click = False

    def on_key(self, event: events.Key) -> None:
        if event.key == 'escape' and self._row_drag is not None:
            self.cancel_row_drag()
            self._suppress_click = True
            event.stop()  # pyright: ignore[reportUnusedCallResult]
            event.prevent_default()  # pyright: ignore[reportUnusedCallResult]

    def _row_at(self, event: events.MouseEvent) -> tuple[int, str] | None:
        """Resolve a visible row, excluding headers, blank space and outside drops."""
        offset = event.get_content_offset(self)
        if (
            offset is None
            or not 0 <= offset.x < self.content_size.width
            or not self.header_height <= offset.y < self.content_size.height
        ):
            return None
        # During preview, hit-test the original slots rather than the moved rows:
        # otherwise the source would become its own target and oscillate on hover.
        position = offset.y - self.header_height + int(self.scroll_y)
        for i, row in enumerate(self.ordered_rows):
            if position < row.height:
                key = (
                    self._row_drag.original_keys[i] if self._row_drag is not None else row.key.value
                )
                return (i, key) if key is not None else None
            position -= row.height
        return None

    def _preview_row_drag(self, target: tuple[int, str] | None) -> None:
        assert self._row_drag is not None
        source = self._row_drag.key
        keys = list(self._row_drag.original_keys)
        if (
            self.reorder_enabled
            and source in self.reorder_keys
            and target is not None
            and target[1] in self.reorder_keys
            and target[1] != source
        ):
            keys.remove(source)
            keys.insert(target[0], source)
        self._display_order(keys)
        self.move_cursor(row=keys.index(source), scroll=False)

    def _display_order(self, keys: list[str] | tuple[str, ...]) -> None:
        if list(keys) == [row.key.value for row in self.ordered_rows]:
            return
        # DataTable's public sort API receives cells, not row keys. Match the
        # existing cell objects, never their possibly duplicated/truncated text.
        ranks = {id(self.get_row(key)[0]): i for i, key in enumerate(keys)}
        self.sort(key=lambda cells: ranks[id(cells[0])])  # pyright: ignore[reportUnusedCallResult]

    def cancel_row_drag(self) -> None:
        """Restore display order and release capture without changing stored data."""
        if self._row_drag is not None:
            drag, self._row_drag = self._row_drag, None
            if drag.active:
                self._suppress_click = True
                self._display_order(drag.original_keys)
                self.move_cursor(row=drag.original_keys.index(drag.key), scroll=False)
            self.release_mouse()

    def _resize(self, event: events.MouseEvent, *, finished: bool) -> None:
        assert self._drag is not None
        width = max(
            MIN_COLUMN_WIDTH, self._drag.width + event.screen_offset.x - self._drag.screen_x
        )
        if finished or width != self._drag.last_width:
            self._drag.last_width = width
            self.post_message(self.ColumnResized(self._drag.key, width, finished=finished))  # pyright: ignore[reportUnusedCallResult]
        event.stop()  # pyright: ignore[reportUnusedCallResult]
        event.prevent_default()  # pyright: ignore[reportUnusedCallResult]
