"""Shared Textual application helpers."""

from typing import ClassVar

from textual.app import App
from textual.css.errors import StylesheetError


class RefreshableCssApp[ReturnType](App[ReturnType]):
    """An app whose external TCSS can be safely refreshed with ``Ctrl+R``."""

    BINDINGS: ClassVar = [('ctrl+r', 'refresh_css', '刷新样式')]

    def action_refresh_css(self) -> None:
        """Reload TCSS only after a copy parses successfully."""
        stylesheet = self.stylesheet.copy()
        try:
            stylesheet.read_all(self.css_path)
            stylesheet.parse()
        except StylesheetError as error:
            self.notify(f'刷新样式失败：{error}', severity='warning')
            return
        self.stylesheet = stylesheet
        self.stylesheet.update(self)
        for screen in self.screen_stack:
            self.stylesheet.update(screen)
        self.notify('已刷新样式。')
