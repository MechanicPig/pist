"""Read map assets and Dialog from original Game Content."""

from collections.abc import Mapping
from pathlib import Path

from pist.game import dialog
from pist.game.maps import MapInfo


def load_maps(game_dir: Path) -> tuple[MapInfo, ...]:
    """Read original Game Content map paths."""
    content_dir = game_dir / 'Content'
    maps_dir = content_dir / 'Maps'
    if not maps_dir.is_dir():
        return ()
    return tuple(
        MapInfo(file_path=path.relative_to(content_dir).as_posix())
        for path in sorted(maps_dir.rglob('*.bin'), key=lambda path: path.as_posix().casefold())
    )


def load_dialogs(game_dir: Path) -> Mapping[str, Mapping[str, str]]:
    """Read the original Game Dialog entries used as Mod Dialog merge defaults."""
    dialog_dir = game_dir / 'Content' / 'Dialog'
    return {
        language: dialog.parse_dialog(path.read_text(encoding='utf-8'))
        for language, filename in dialog.DIALOG_FILENAMES.items()
        if (path := dialog_dir / filename).is_file()
    }
