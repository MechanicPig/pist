"""Localized workaround for Textual's double-width notification clipping.

Remove when Textualize/textual#6357 is fixed. The upstream compositor splits
opaque toasts at fully hidden widget edges, replacing split CJK glyphs with spaces.
Keep Textual's normal rendering and damage tracking, but omit those hidden cuts.
"""

from collections.abc import Iterable

from textual._compositor import ChopsUpdate, Compositor
from textual.geometry import Region
from textual.strip import Strip
from textual.widgets._toast import Toast


class ToastCompositor(Compositor):
    """Preserve whole glyphs inside opaque, unobstructed notifications."""

    @property
    def cuts(self) -> list[list[int]]:
        cuts = super().cuts
        protected = list(self._opaque_toast_regions())
        if not protected:
            return cuts
        result = [line.copy() for line in cuts]
        for region in protected:
            for y in region.line_range:
                result[y] = [cut for cut in result[y] if not region.x < cut < region.right]
        return result

    def _opaque_toast_regions(self) -> Iterable[Region]:
        """Find notification areas with no visible widget above them."""
        foreground: list[Region] = []
        # Visible widgets are ordered front to back. Never remove a cut belonging
        # to something above the toast, or assume a translucent toast is opaque.
        for widget, (region, clip) in self.visible_widgets.items():
            if not widget.visible:
                continue
            visible = region.intersection(clip)
            if (
                isinstance(widget, Toast)
                and widget.styles.opacity == 1
                and widget.styles.background.a == 1
                and not any(visible.overlaps(other) for other in foreground)
            ):
                yield visible
            foreground.append(visible)

    def render_partial_update(self) -> ChopsUpdate | None:
        """Repaint intersecting toasts whole instead of stopping inside a wide glyph."""
        for region in self._opaque_toast_regions():
            if any(region.overlaps(dirty) for dirty in self._dirty_regions):
                self._dirty_regions.add(region)
        return super().render_partial_update()

    def _get_renders(
        self, crop: Region | None = None
    ) -> Iterable[tuple[Region, Region, list[Strip]]]:
        """Clip hidden edges so remaining background fragments keep their true cell offsets."""
        cuts = self.cuts
        if cuts is super().cuts:
            yield from super()._get_renders(crop)
            return
        for region, clip, strips in super()._get_renders(crop):
            visible = region.intersection(clip)
            if all(visible.x in cuts[y] and visible.right in cuts[y] for y in visible.line_range):
                yield region, clip, strips
                continue
            for y, strip in zip(visible.line_range, strips, strict=True):
                boundaries = [cut for cut in cuts[y] if visible.x <= cut <= visible.right]
                if len(boundaries) < 2:
                    continue
                left, right = boundaries[0], boundaries[-1]
                fragment = Region(left, y, right - left, 1)
                yield fragment, fragment, [strip.crop(left - visible.x, right - visible.x)]
