"""Factories for assembled Level sides in tests."""

from pathlib import Path
from typing import Literal

from berries.game.content import ContentPath
from berries.game.levels import (
    Level,
    LevelSide,
    local_sid_for_map_file,
)
from berries.game.maps import ModMap, VanillaMap, split_map_side_suffix
from berries.game.mods import InstalledMod


def make_level_side(
    *,
    file_path: str,
    sid: str | None = None,
    side: Literal['B', 'C'] | None = None,
    mod: InstalledMod | None = None,
) -> tuple[Level, LevelSide]:
    """Build one simplified Level/Side selection without running catalog assembly."""
    path = ContentPath(file_path)
    base_file, local_side = split_map_side_suffix(path)
    resolved_side = side or local_side
    level_side = LevelSide.A if resolved_side is None else LevelSide[resolved_side]
    loaded_map = VanillaMap(path, Path('Content')) if mod is None else ModMap(path, mod)
    base_map = VanillaMap(base_file, Path('Content')) if mod is None else ModMap(base_file, mod)
    level = Level(
        sid=sid or local_sid_for_map_file(base_file),
        maps=(base_map,) * level_side + (loaded_map,),
    )
    return level, level_side
