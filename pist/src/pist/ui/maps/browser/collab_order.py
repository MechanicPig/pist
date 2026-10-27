"""Coordinate Collab journal ordering, caching, and save-aware list refreshes."""

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from zipfile import BadZipFile

from textual.dom import DOMNode

from berries.game import collab, mods
from berries.game.binmap import BadMapBin, MapStringDecodeWarning
from berries.game.content import BadContentEntry, ContentPath
from berries.game.levels import Level, LevelSide
from berries.game.maps import Map, ModMap
from pist import catalog_fingerprint, save_progress
from pist.catalog_cache import CatalogCache, CollabJournalIconPaths
from pist.saves import SaveProgress

from .collab_list import CollabMapList
from .map_list import MapItem, MapList, SideMapItem


@dataclass(slots=True)
class _ModMapGroup:
    """Active Collab maps whose files come from one physical Mod."""

    mod: mods.InstalledMod
    maps: list[tuple[Level, LevelSide]] = field(default_factory=list)


def maps_by_progress(
    maps: Iterable[tuple[Level, LevelSide]],
    save_slot: SaveProgress | None,
) -> tuple[tuple[Level, LevelSide], ...]:
    """Order Collab maps by this slot's progress, preserving icon order within ties."""
    if save_slot is None:
        return tuple(maps)
    return tuple(
        sorted(
            maps,
            key=lambda item: save_progress.map_progress(
                save_slot.get_map_stats(item[0].sid, item[1])
            ),
        )
    )


def maps_by_campaign_icon_order(
    map_groups: Iterable[Iterable[tuple[Level, LevelSide]]],
    icons: CollabJournalIconPaths,
) -> tuple[tuple[Level, LevelSide], ...]:
    """Order each referenced Campaign independently, then concatenate the groups."""
    content_icons = {ContentPath(path): icon for path, icon in icons.items()}
    result: list[tuple[Level, LevelSide]] = []
    for group in map_groups:
        group_maps = tuple(group)
        maps_by_file = {level[side].file_path: (level, side) for level, side in group_maps}
        result.extend(
            maps_by_file[map_info]
            for map_info in collab.collab_journal_map_order_from_icons(
                (level[side].file_path for level, side in group_maps), content_icons
            )
        )
    return tuple(result)


class CollabMapOrderController:
    """Own the asynchronous journal-order lifecycle for mounted Collab lists."""

    def __init__(
        self,
        cache: CatalogCache,
        *,
        on_map_warning: Callable[[Map, MapStringDecodeWarning], None] | None = None,
    ) -> None:
        self._cache = cache
        self._on_map_warning = on_map_warning
        self._task: asyncio.Task[None] | None = None

    def start(self, root: DOMNode) -> None:
        """Restart journal-order loading for every Collab list below *root*."""
        self.cancel()
        if root.query(CollabMapList):
            self._task = asyncio.create_task(self._load_all(root))

    def cancel(self) -> None:
        """Cancel the active loading pass, if any."""
        if self._task is not None:
            self._task.cancel()  # pyright: ignore[reportUnusedCallResult]
            self._task = None

    async def _load_all(self, root: DOMNode) -> None:
        """Load every list, including lists mounted by an earlier result."""
        loaded_lists: set[int] = set()
        try:
            while True:
                map_list = next(
                    (
                        candidate
                        for candidate in root.query(CollabMapList)
                        if candidate.is_mounted and id(candidate) not in loaded_lists
                    ),
                    None,
                )
                if map_list is None:
                    break
                loaded_lists.add(id(map_list))
                await self._load_one(map_list)
        finally:
            if asyncio.current_task() is self._task:
                self._task = None

    async def _load_one(self, map_list: CollabMapList) -> None:
        groups: dict[int, _ModMapGroup] = {}
        for level, side in map_list.maps:
            source = level[side]
            if not isinstance(source, ModMap):
                map_list.icon_order = map_list.maps
                await self._finish_loading(map_list)
                return
            mod = source.mod
            group = groups.setdefault(id(mod), _ModMapGroup(mod))
            group.maps.append((level, side))
        icons: CollabJournalIconPaths = {}
        completed = 0
        try:
            for group in groups.values():
                group_infos = tuple(level[side].file_path for level, side in group.maps)
                content_paths = tuple(sorted(map_info for map_info in group_infos))
                map_files = tuple(path.as_posix() for path in content_paths)
                fingerprint = catalog_fingerprint.collab_journal_icon_fingerprint(
                    group.mod, group_infos
                )
                cached = self._cache.load_icons(str(group.mod.path), map_files, fingerprint)
                if cached is not None:
                    icons.update(cached)
                    completed += len(group_infos)
                    map_list.set_progress(completed)
                    continue
                group_icons: CollabJournalIconPaths = {}
                loaded_maps = {level[side].file_path: level[side] for level, side in group.maps}

                for map_file, icon in collab.iter_collab_journal_map_icons(
                    group.mod,
                    group_infos,
                    on_warning=partial(self._report_icon_warning, loaded_maps)
                    if self._on_map_warning is not None
                    else None,
                ):
                    group_icons[map_file.as_posix()] = icon
                    completed += 1
                    map_list.set_progress(completed)
                    await asyncio.sleep(0)
                icons.update(group_icons)
                self._cache.save_icons(str(group.mod.path), map_files, fingerprint, group_icons)
        except BadMapBin, BadContentEntry, BadZipFile, FileNotFoundError, KeyError, OSError:
            ordered_maps = map_list.maps
        else:
            ordered_maps = maps_by_campaign_icon_order(map_list.map_groups, icons)
        map_list.icon_order = ordered_maps
        await self._finish_loading(map_list)

    def _report_icon_warning(
        self,
        loaded_maps: Mapping[ContentPath, Map],
        map_info: ContentPath,
        warning: MapStringDecodeWarning,
    ) -> None:
        if self._on_map_warning is not None:
            self._on_map_warning(loaded_maps[map_info], warning)

    async def _finish_loading(self, map_list: CollabMapList) -> None:
        """Mount the initially ordered list and apply the latest save slot."""
        assert map_list.icon_order is not None
        await map_list.remove_children()
        await map_list.mount(
            MapList(
                maps_by_progress(map_list.icon_order, map_list.save_slot),
                map_list.lang,
                map_list.dialogs,
                map_list.save_slot,
                map_list.source_name_for,
                extra_items=map_list.extra_items,
            )
        )
        await self.refresh_list(map_list, map_list.save_slot)  # pyright: ignore[reportUnusedCallResult]
        map_list.finish_loading()

    async def refresh_list(
        self,
        collab_map_list: CollabMapList,
        save_slot: SaveProgress | None,
    ) -> MapList | None:
        """Reorder a loaded Collab list for one save slot while retaining UI state."""
        collab_map_list.save_slot = save_slot
        if collab_map_list.icon_order is None:
            return None
        ordered_maps = maps_by_progress(collab_map_list.icon_order, save_slot)
        if not (map_lists := tuple(collab_map_list.query(MapList))):
            return None
        current_list = map_lists[0]
        if current_list.maps == ordered_maps:
            return None
        highlighted = current_list.highlighted_child
        highlighted_key = highlighted.state_key if isinstance(highlighted, MapItem) else None
        selected_side_files = {
            item.state_key: item.file_path
            for item in current_list.map_items
            if isinstance(item, SideMapItem)
        }
        replacement = MapList(
            ordered_maps,
            collab_map_list.lang,
            collab_map_list.dialogs,
            save_slot,
            collab_map_list.source_name_for,
            extra_items=collab_map_list.extra_items,
        )
        await collab_map_list.remove_children()
        await collab_map_list.mount(replacement)
        for item in replacement.map_items:
            if (
                isinstance(item, SideMapItem)
                and (map_file := selected_side_files.get(item.state_key)) is not None
            ):
                item.restore_selected_side(map_file)
        if highlighted_key is not None:
            replacement.index = next(
                (
                    i
                    for i, item in enumerate(replacement.map_items)
                    if item.state_key == highlighted_key
                ),
                None,
            )
        return replacement
