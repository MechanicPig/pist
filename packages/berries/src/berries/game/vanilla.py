"""Read map assets and Dialog from original Game Content."""

from pathlib import Path

from berries.containers import CaseFoldDict
from berries.game import content, dialog
from berries.game.maps import MapInfo


def load_maps(game_dir: Path) -> tuple[MapInfo, ...]:
    """Read original Game Content map paths."""
    content_dir = game_dir / content.CONTENT_DIRNAME
    maps_dir = content_dir / content.MAPS_DIR
    if not maps_dir.is_dir():
        return ()
    return tuple(
        MapInfo(file_path=path.relative_to(content_dir).as_posix())
        for path in sorted(maps_dir.rglob('*.bin'), key=lambda path: path.as_posix().casefold())
    )


def load_dialogs(game_dir: Path) -> dict[str, CaseFoldDict[str]]:
    """Read the original Game Dialog entries used as Mod Dialog merge defaults."""
    dialog_dir = game_dir / content.CONTENT_DIRNAME / content.DIALOG_DIR
    return {
        lang: dialog.parse_dialog(path.read_text(encoding='utf-8'))
        for lang, filename in dialog.DIALOG_FILENAMES.items()
        if (path := dialog_dir / filename).is_file()
    }
