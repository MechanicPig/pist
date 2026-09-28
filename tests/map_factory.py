"""Factories for assembled Level sides in tests."""

from pathlib import Path
from typing import Literal

from berries.game.content import ContentPath, GameContent
from berries.game.dialog import dialog_key_for_map_file, split_map_side_suffix
from berries.game.levels import (
    Level,
    LevelSide,
    Map,
)
from berries.game.maps import MapInfo
from berries.game.mods import InstalledMod


def make_level_side(
    *,
    file_path: str,
    dialog_key: str | None = None,
    sid: str | None = None,
    side: Literal['B', 'C'] | None = None,
    names: dict[str, str] | None = None,
    author_texts: dict[str, str] | None = None,
    collab_credit_tags: dict[str, str] | None = None,
    mod: InstalledMod | None = None,
) -> tuple[Level, LevelSide]:
    """Build one standalone side with an in-memory vanilla-style source."""
    del names, author_texts, collab_credit_tags
    info = MapInfo(file_path=file_path)
    base_file, side_suffix = split_map_side_suffix(ContentPath(file_path))
    resolved_side = side or side_suffix
    level_side = LevelSide.A if resolved_side is None else LevelSide(resolved_side)
    loaded_map = Map(info, GameContent(Path('Content'))) if mod is None else Map(info, mod)
    level = Level(
        sid=sid or '/'.join(base_file.with_suffix('').parts[1:]),
        dialog_key=dialog_key or dialog_key_for_map_file(base_file),
        maps=(loaded_map,) * (level_side.position + 1),
    )
    return level, level_side
