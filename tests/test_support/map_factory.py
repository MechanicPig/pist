"""Factories for assembled Level sides in tests."""

from pathlib import Path
from typing import Literal

from berries.game.content import ContentPath, GameContent
from berries.game.dialog import dialog_key_for_map_file
from berries.game.levels import (
    Level,
    LevelSide,
    Map,
    local_sid_for_map_file,
)
from berries.game.maps import MapInfo, split_map_side_suffix
from berries.game.mods import InstalledMod


def make_level_side(
    *,
    file_path: str,
    dialog_key: str | None = None,
    sid: str | None = None,
    side: Literal['B', 'C'] | None = None,
    mod: InstalledMod | None = None,
) -> tuple[Level, LevelSide]:
    """Build one simplified Level/Side selection without running catalog assembly."""
    info = MapInfo(file_path=ContentPath(file_path))
    base_file, local_side = split_map_side_suffix(info.file_path)
    resolved_side = side or local_side
    level_side = LevelSide.A if resolved_side is None else LevelSide(resolved_side)
    loaded_map = Map(info, GameContent(Path('Content'))) if mod is None else Map(info, mod)
    level = Level(
        sid=sid or local_sid_for_map_file(base_file),
        dialog_key=dialog_key or dialog_key_for_map_file(base_file),
        maps=(loaded_map,) * (level_side.position + 1),
    )
    return level, level_side
