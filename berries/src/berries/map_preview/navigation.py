"""Map entry resolution and page history, independent of application overlays."""

import asyncio
from collections.abc import Mapping

from pydantic import Field

from berries import map_layout
from berries.game import dialog, maps
from berries.game.binmap import AttrValue
from berries.game.levels import Level, LevelSide
from berries.game.maps import MapInfo
from berries.models import FrozenModel, StrictModel


class OpenMapReq(StrictModel):
    target_sid: str = Field(alias='targetSid')


class EntranceResp(FrozenModel):
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
    ) -> None:
        self._pages = [page]
        self._index = 0
        self.dialogs = dialogs
        self.maps = {} if maps is None else dict(maps)

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
        return tuple(
            EntranceResp(
                room=item.room,
                target_sid=item.target_sid,
                x=item.x,
                y=item.y,
                width=item.width,
                height=item.height,
                target_title=(
                    target[0].display_name(target[1], self.dialogs, ('zh-cn', 'en'))
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
        return level, side, await asyncio.to_thread(map_layout.load_map_layout, level[side])


def map_title(map_info: MapInfo, dialogs: Mapping[str, Mapping[str, str]]) -> str:
    """Resolve a raw map title using the same Dialog fallback as linked previews."""
    base_file, side = maps.split_map_side_suffix(map_info.file_path)
    key = dialog.dialog_key_for_map_file(base_file)
    names = {
        lang: f'{name} {side}' if side is not None else name
        for lang, entries in dialogs.items()
        if (name := entries.get(key)) is not None
    }
    fallback = dialog.default_map_name(base_file)
    return dialog.localized_name(names, ('zh-cn', 'en')) or (
        f'{fallback} {side}' if side is not None else fallback
    )
