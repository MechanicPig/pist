"""Shared positioning and dismissal for small cursor-anchored context menus."""

from pathlib import Path
from typing import ClassVar

from textual import events, on
from textual.containers import Vertical
from textual.geometry import Offset
from textual.screen import ModalScreen


class ContextMenuScreen[Result](ModalScreen[Result]):
    """Place a context menu inside screen bounds and close it on outside clicks."""

    CSS_PATH = Path(__file__).parent / 'styles/context_menu.tcss'
    BINDINGS: ClassVar = [('escape', 'dismiss', '取消')]

    def __init__(self, screen_x: int, screen_y: int) -> None:
        super().__init__()
        self._screen_x = screen_x
        self._screen_y = screen_y

    def on_mount(self) -> None:
        self.query_one('.context-menu-dialog', Vertical).styles.offset = Offset(
            self._screen_x, self._screen_y
        )
        self.call_after_refresh(self._position_dialog)

    def _position_dialog(self) -> None:
        dialog = self.query_one('.context-menu-dialog', Vertical)
        max_x = max(self.size.width - dialog.outer_size.width, 0)
        max_y = max(self.size.height - dialog.outer_size.height, 0)
        dialog.styles.offset = Offset(
            min(max(self._screen_x, 0), max_x),
            min(max(self._screen_y, 0), max_y),
        )

    @on(events.Click)
    def dismiss_outside(self, event: events.Click) -> None:
        dialog = self.query_one('.context-menu-dialog', Vertical)
        screen_x = event.x if event.screen_x is None else event.screen_x
        screen_y = event.y if event.screen_y is None else event.screen_y
        if not dialog.region.contains(screen_x, screen_y):
            self.dismiss()
