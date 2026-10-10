"""Regress notifications over hidden widget boundaries, not just their source text."""

import asyncio

import pytest
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.geometry import Region
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.widgets._toast import Toast

from pist.ui.toast_compositor import ToastCompositor
from pist.ui.tui import RefreshableCssApp


def columns() -> Horizontal:
    widgets = []
    for _ in range(40):
        widget = Static('')
        widget.styles.width = 3
        widget.styles.height = '100%'
        widget.styles.border = ('solid', 'red')
        widgets.append(widget)
    return Horizontal(*widgets)


class ToastTestApp(RefreshableCssApp[None]):
    def compose(self) -> ComposeResult:
        yield columns()


class ToastTestModal(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        yield columns()


@pytest.mark.parametrize('modal', (False, True))
def test_cjk_toast_preserves_glyphs_and_background_after_refresh_and_resize(modal: bool) -> None:
    async def check() -> None:
        app = ToastTestApp()
        async with app.run_test(size=(100, 24), notifications=True) as pilot:
            if modal:
                await app.push_screen(ToastTestModal())
            await pilot.pause()
            compositor = app.screen._compositor
            assert isinstance(compositor, ToastCompositor)
            message = '本地记录 75 已保存并同步。'
            for width in (100, 86, 111):
                await pilot.resize_terminal(width, 24)
                await pilot.pause()
                before = compositor.render_strips()
                app.notify(message, title='完成', timeout=30, markup=False)
                await pilot.pause()
                toast = app.screen.query_one(Toast)
                region = toast.region
                original = toast.render_lines(Region(0, 0, region.width, region.height))
                composed = compositor.render_strips()
                assert all(strip.cell_length == width for strip in composed)
                assert message in ''.join(strip.text for strip in original)
                for y, expected in zip(region.line_range, original, strict=True):
                    assert composed[y].crop(region.x, region.right).text == expected.text
                    assert composed[y].crop(0, region.x).text == before[y].crop(0, region.x).text
                    assert composed[y].crop(region.right).text == before[y].crop(region.right).text
                background = app.screen.query_one(Horizontal)
                background.refresh()
                compositor.update_widgets({background})
                partial = compositor.render_partial_update()
                assert partial is not None and message in partial.render_segments(app.console)
                # A one-cell damage span ending halfway through the first CJK glyph.
                compositor._dirty_regions.add(Region(region.x + 2, region.y + 2, 1, 1))
                partial = compositor.render_partial_update()
                assert partial is not None and message in partial.render_segments(app.console)
                await pilot.click(toast)
                await pilot.pause()
                assert not app.screen.query(Toast)
                assert [strip.text for strip in compositor.render_strips()] == [
                    strip.text for strip in before
                ]

    asyncio.run(check())


def test_translucent_toast_keeps_normal_composition_boundaries() -> None:
    async def check() -> None:
        app = ToastTestApp()
        async with app.run_test(size=(100, 24), notifications=True) as pilot:
            app.notify('透明提示', timeout=30)
            await pilot.pause()
            toast = app.screen.query_one(Toast)
            toast.styles.opacity = 0.5
            await pilot.pause()
            compositor = app.screen._compositor
            row = toast.region.y + 1
            assert any(toast.region.x < cut < toast.region.right for cut in compositor.cuts[row])

    asyncio.run(check())
