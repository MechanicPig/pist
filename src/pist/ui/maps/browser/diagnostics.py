"""Consolidated startup-warning presentation for the map browser."""

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class BrowserWarningsScreen(ModalScreen[None]):
    """Show every warning collected while scanning and assembling campaigns."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '关闭')]

    def __init__(self, warnings: tuple[str, ...]) -> None:
        super().__init__()
        self.warnings = warnings

    def compose(self) -> ComposeResult:
        with Vertical(id='browser-warnings-dialog'):
            yield Static(f'警告（{len(self.warnings)}）', classes='dialog-title')
            with VerticalScroll(id='browser-warnings-content'):
                for index, warning in enumerate(self.warnings, 1):
                    yield Static(
                        f'{index}. {warning}',
                        classes='browser-warning-entry',
                        markup=False,
                    )
            with Horizontal(id='browser-warnings-actions'):
                yield Button('关闭', id='browser-warnings-close', variant='primary')

    @on(Button.Pressed, '#browser-warnings-close')
    def close(self) -> None:
        self.dismiss(None)
