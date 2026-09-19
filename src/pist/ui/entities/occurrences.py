"""Occurrence grouping and map lookup for the entity-audit UI."""

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import ClassVar

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, ListItem, ListView, Static

from pist.entities.audit import AuditMapOccurrences, AuditSource, RawEntityOccurrence
from pist.game.dialog import dialog_key_for_map_file
from pist.game.map_source import MapSource
from pist.game.mods import LocalMap
from pist.game.routes import MapLayout, MapPreviewEntity, load_map_layout_from_path

OCCURRENCE_MAP_LIST_ID = 'occurrence-map-list'
OCCURRENCE_ROOM_LIST_ID = 'occurrence-room-list'


class MapOccurrenceItem(ListItem):
    """One map row in the occurrence popup."""

    class PreviewRequested(Message):
        """Request a read-only browser preview for this map row."""

        def __init__(self, item: MapOccurrenceItem) -> None:
            self.item = item
            super().__init__()

    def __init__(self, data: AuditMapOccurrences) -> None:
        self.data = data
        text = Text(data.source.map_name, style='bold')
        if data.source.mod_name is not None:
            text.append(f' [{data.source.mod_name}]', style='dark_orange')
        text.append(f'\n{data.count} instances · {data.source.map_file}', style='dim')
        super().__init__(Static(text))

    @on(events.Click)
    def preview_on_double_click(self, event: events.Click) -> None:
        """Request a preview without overriding ListItem's selection callback."""
        if event.chain == 2:
            self.post_message(self.PreviewRequested(self))


class OccurrenceScreen(ModalScreen[None]):
    """Two-level table of maps and rooms containing the selected entity."""

    BINDINGS: ClassVar = [('escape', 'dismiss', '关闭')]

    def __init__(
        self,
        entity_name: str,
        maps: tuple[AuditMapOccurrences, ...],
        map_progress: Callable[[str], int],
        preview: Callable[[AuditMapOccurrences], None] | None = None,
    ) -> None:
        super().__init__()
        self._entity_name = entity_name
        self._maps = tuple(
            sorted(
                maps,
                key=lambda data: (
                    map_progress(data.source.map_file),
                    -data.count,
                    data.source.map_name.casefold(),
                    data.source.map_file.casefold(),
                ),
            )
        )
        self._preview = preview

    def compose(self) -> ComposeResult:
        with VerticalScroll(id='occurrence-dialog'):
            yield Static(
                f'{self._entity_name} 的出现位置（双击地图预览）',
                classes='audit-section-title',
            )
            with Horizontal():
                yield ListView(
                    *(MapOccurrenceItem(data) for data in self._maps),
                    id=OCCURRENCE_MAP_LIST_ID,
                )
                yield ListView(id=OCCURRENCE_ROOM_LIST_ID)
            yield Button('关闭', id='occurrence-close')

    @on(ListView.Selected, f'#{OCCURRENCE_MAP_LIST_ID}')
    def select_map(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, MapOccurrenceItem):
            return
        rooms = self.query_one(f'#{OCCURRENCE_ROOM_LIST_ID}', ListView)
        rooms.clear()
        rooms.extend(
            ListItem(Static(f'{room or "未命名房间"} · {count} instances'))
            for room, count in event.item.data.rooms
        )

    @on(MapOccurrenceItem.PreviewRequested)
    def preview_map(self, event: MapOccurrenceItem.PreviewRequested) -> None:
        if self._preview is not None:
            self._preview(event.item.data)

    @on(Button.Pressed, '#occurrence-close')
    def close(self) -> None:
        self.dismiss()


def load_occurrence_map(game_dir: Path, source: AuditSource) -> tuple[LocalMap, MapLayout]:
    """Locate one immutable audit source without rescanning or decoding other maps."""
    map_file = source.map_file
    match source.scope:
        case MapSource.VANILLA:
            root = game_dir / 'Content'
        case MapSource.MOD:
            if not source.mod_file:
                raise ValueError(f'审计报告缺少 Mod 文件名：{source.map_file}')
            root = game_dir / 'Mods' / source.mod_file
        case _:
            raise ValueError(f'不支持预览此审计来源：{source.scope}')
    if not root.exists():
        raise ValueError(f'地图包已不存在：{root}')
    try:
        dialog_key = dialog_key_for_map_file(map_file)
    except ValueError:
        dialog_key = PurePosixPath(map_file).stem
    map_info = LocalMap(file_path=map_file, dialog_key=dialog_key, names={'en': source.map_name})
    return map_info, load_map_layout_from_path(root, map_file)


def occurrence_preview_entities(
    occurrences: tuple[RawEntityOccurrence, ...],
) -> dict[str, tuple[MapPreviewEntity, ...]]:
    """Project every stored raw entity position alongside standard preview entities."""
    entities: dict[str, list[MapPreviewEntity]] = defaultdict(list)
    for occurrence in occurrences:
        x = occurrence.attrs.get('x')
        y = occurrence.attrs.get('y')
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            entities[occurrence.room].append(MapPreviewEntity(x, y, 'audit'))
    return {room: tuple(values) for room, values in entities.items()}
