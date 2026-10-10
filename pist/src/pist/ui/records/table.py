"""Record table with mouse-captured resizing at header separators."""

from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import DataTable

from pist.ui.mouse import LEFT_MOUSE_BUTTON

MIN_COLUMN_WIDTH = 4


@dataclass(frozen=True)
class ColumnDrag:
    """Origin of one ongoing column resize in screen coordinates."""

    key: str
    screen_x: int
    width: int


class RecordTable(DataTable[Text]):
    """Resize columns by dragging the right edge of their header with the left mouse button."""

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
        self._resizing_click = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._resizing_click = False
        offset = event.get_content_offset(self)
        if event.button != LEFT_MOUSE_BUTTON or offset is None or offset.y >= self.header_height:
            return
        position = offset.x + int(self.scroll_x)
        edge = 0
        for column in self.ordered_columns:
            edge += column.get_render_width(self)
            if edge - 2 <= position <= edge:
                assert column.key.value is not None
                self._drag = ColumnDrag(column.key.value, event.screen_offset.x, column.width)
                self._resizing_click = True
                self.capture_mouse()
                event.stop()
                event.prevent_default()
                return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag is not None:
            self._resize(event, finished=False)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag is not None and event.button == LEFT_MOUSE_BUTTON:
            self._resize(event, finished=True)
            self._drag = None
            self.release_mouse()

    def on_click(self, event: events.Click) -> None:
        if self._resizing_click:
            event.stop()
            event.prevent_default()
            self._resizing_click = False

    def _resize(self, event: events.MouseEvent, *, finished: bool) -> None:
        assert self._drag is not None
        width = max(
            MIN_COLUMN_WIDTH, self._drag.width + event.screen_offset.x - self._drag.screen_x
        )
        self.post_message(self.ColumnResized(self._drag.key, width, finished=finished))
        event.stop()
        event.prevent_default()
