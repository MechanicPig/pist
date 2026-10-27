"""Map entry resolution and page history, independent of application overlays."""

import asyncio
from collections.abc import Callable, Mapping

from pydantic import Field

from berries import map_layout
from berries.entities.rules import EntityRules
from berries.game.binmap import AttrValue, MapStringDecodeWarning, map_string_warnings
from berries.game.levels import Level, LevelSide
from berries.game.localization import Localization
from berries.game.maps import Map
from berries.map_entrances import MapEntranceRules
from berries.models import FrozenModel, StrictModel


class OpenMapReq(StrictModel):
    """Internal browser request; JSON fields may change with the bundled client."""

    target_sid: str = Field(alias='targetSid')


class EntranceResp(FrozenModel):
    """Internal browser entrance representation, not a domain result contract."""

    room: str
    target_sid: str = Field(serialization_alias='targetSid')
    x: int | float | None
    y: int | float | None
    width: int | float | None
    height: int | float | None
    target_title: str = Field(serialization_alias='targetTitle')
    available: bool
    source: str | None = None
    entity_id: str | None = Field(default=None, serialization_alias='entityId')
    attrs: dict[str, AttrValue] | None = None


class PreviewNavigation[Page]:
    """Keep caller-owned pages while sharing map resolution and history semantics."""

    def __init__(
        self,
        page: Page,
        *,
        dialogs: Mapping[str, Mapping[str, str]],
        maps: Mapping[str, tuple[Level, LevelSide]] | None = None,
        on_map_warning: Callable[[Map, MapStringDecodeWarning], None] | None = None,
        entity_rules: EntityRules | None = None,
        entrance_rules: MapEntranceRules | None = None,
    ) -> None:
        self._pages = [page]
        self._index = 0
        self.localization = Localization(dialogs)
        self.maps = {} if maps is None else dict(maps)
        self._on_map_warning = on_map_warning
        self._entity_rules = entity_rules
        self._entrance_rules = entrance_rules

    @property
    def current(self) -> Page:
        return self._pages[self._index]

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return self._index < len(self._pages) - 1

    def append(self, page: Page) -> None:
        del self._pages[self._index + 1 :]
        self._pages.append(page)
        self._index += 1

    def back(self) -> bool:
        if not self.can_back:
            return False
        self._index -= 1
        return True

    def forward(self) -> bool:
        if not self.can_forward:
            return False
        self._index += 1
        return True

    def home(self) -> None:
        self._index = 0

    def entrances(self, layout: map_layout.MapLayout) -> tuple[EntranceResp, ...]:
        """Adapt domain entrances to the bundled browser's internal JSON schema."""
        return tuple(
            EntranceResp(
                room=item.room,
                target_sid=item.target_sid,
                x=item.x,
                y=item.y,
                width=item.width,
                height=item.height,
                target_title=(
                    self.localization.level_name(target[0])
                    if (target := self.maps.get(item.target_sid)) is not None
                    else item.target_sid
                ),
                available=item.target_sid in self.maps,
                source=None if item.source is None else str(item.source),
                entity_id=item.element_name,
                attrs=None if item.attrs is None else dict(item.attrs),
            )
            for item in layout.entrances
        )

    async def load_target(
        self, layout: map_layout.MapLayout, sid: str
    ) -> tuple[Level, LevelSide, map_layout.MapLayout]:
        target = self.maps.get(sid)
        if target is None or sid not in {item.target_sid for item in layout.entrances}:
            raise ValueError('此地图入口目标无法打开。')
        level, side = target

        def load() -> map_layout.MapLayout:
            return map_layout.MapLayout.from_map(
                level[side],
                entity_rules=self._entity_rules,
                entrance_rules=self._entrance_rules,
            )

        if self._on_map_warning is None:
            return level, side, await asyncio.to_thread(load)
        diagnostics: list[MapStringDecodeWarning] = []

        def load_with_warnings() -> map_layout.MapLayout:
            with map_string_warnings(diagnostics.append):
                return load()

        try:
            result = await asyncio.to_thread(load_with_warnings)
        finally:
            # Deliver on the caller's event loop, never from the parsing thread.
            if self._on_map_warning is not None:
                for warning in diagnostics:
                    self._on_map_warning(level[side], warning)
        return level, side, result
