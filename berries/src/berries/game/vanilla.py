"""Read map assets and Dialog from original Game Content."""

from collections.abc import Callable
from pathlib import Path

from berries.containers import CaseFoldDict

from . import dialog
from .content import (
    CONTENT_DIRNAME,
    DIALOG_DIR,
    MAPS_DIR,
    ContentPath,
    DirContentEntry,
)
from .maps import VanillaMap

# Original-game AreaData.Load order (including interludes and Farewell), not
# the numeric chapter label. Numeric Area IDs are indices into this tuple.
VANILLA_SIDS = (
    'Celeste/0-Intro',
    'Celeste/1-ForsakenCity',
    'Celeste/2-OldSite',
    'Celeste/3-CelestialResort',
    'Celeste/4-GoldenRidge',
    'Celeste/5-MirrorTemple',
    'Celeste/6-Reflection',
    'Celeste/7-Summit',
    'Celeste/8-Epilogue',
    'Celeste/9-Core',
    'Celeste/LostLevels',
)


def load_maps(game_dir: Path) -> tuple[VanillaMap, ...]:
    """Read original Game Content map paths."""
    content_dir = game_dir / CONTENT_DIRNAME
    maps_dir = content_dir / MAPS_DIR
    if not maps_dir.is_dir():
        return ()

    return tuple(
        VanillaMap(ContentPath(path.relative_to(content_dir)), content_dir)
        for path in sorted(maps_dir.rglob('*.bin'), key=lambda path: path.as_posix().casefold())
    )


def load_dialogs(
    game_dir: Path, *, on_error: Callable[[dialog.DialogReadError], None] | None = None
) -> dict[str, CaseFoldDict[str]]:
    """Read original Dialog defaults; on_error may handle failures and allow partial results."""
    with DirContentEntry(game_dir / CONTENT_DIRNAME) as root:
        return dialog.read_dialogs(
            root / DIALOG_DIR,
            encoding='utf-8',
            strict=True,
            on_error=on_error,
        )
