"""Factories for physical Mod packages in tests."""

import shutil
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, Unpack
from zipfile import ZIP_DEFLATED, ZipFile

from berries.game.everest import Dependency, ModMetadata, Version
from berries.game.maps import MapInfo
from berries.game.mods import DirMod, ScannedMod, ZipMod

TEST_DATA_DIR = Path(__file__).parent / 'data'
MOD_SCAN_GAME_DATA_DIR = TEST_DATA_DIR / 'mod_scan_game'


class InstalledModArgs(TypedDict):
    """The physical-package fields unrelated to Everest metadata."""

    source: Literal['zip', 'directory']
    filename: str
    path: str | Path
    collab_id: NotRequired[str | None]
    dialogs: NotRequired[dict[str, dict[str, str]]]
    maps: NotRequired[list[MapInfo]]


def make_installed_mod(
    *,
    metadata_name: str,
    metadata_version: str | None,
    dependencies: list[Dependency] | None = None,
    optional_dependencies: list[Dependency] | None = None,
    **kwargs: Unpack[InstalledModArgs],
) -> ScannedMod:
    """Build a one-entry manifest for tests unrelated to manifest parsing."""
    source = kwargs.pop('source')
    kwargs['path'] = Path(kwargs['path'])
    fields = dict(kwargs)
    fields['manifest'] = (
        ModMetadata(
            name=metadata_name,
            version=None if metadata_version is None else Version.parse(metadata_version),
            dependencies=[] if dependencies is None else dependencies,
            optional_dependencies=[] if optional_dependencies is None else optional_dependencies,
        ),
    )
    if source == 'zip':
        return ZipMod.model_validate(fields)
    return DirMod.model_validate(fields)


def materialize_mod_scan_game(destination: Path) -> Path:
    """Materialize the shared readable Mod scanner fixture under ``destination``."""
    game_dir = destination / 'Celeste'
    mods_dir = game_dir / 'Mods'
    mods_dir.mkdir(parents=True)
    shutil.copy2(MOD_SCAN_GAME_DATA_DIR / 'blacklist.txt', mods_dir / 'blacklist.txt')
    for source in (MOD_SCAN_GAME_DATA_DIR / 'archives').iterdir():
        with ZipFile(mods_dir / source.name, 'w', compression=ZIP_DEFLATED) as archive:
            for path in source.rglob('*'):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
    for source in (MOD_SCAN_GAME_DATA_DIR / 'directories').iterdir():
        shutil.copytree(source, mods_dir / source.name)
    return game_dir
