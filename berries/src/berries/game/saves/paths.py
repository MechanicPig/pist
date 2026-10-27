"""Celeste and Everest save-directory and filename conventions."""

import os
import sys
from pathlib import Path
from typing import Annotated

from pydantic import Field

type SlotNumber = Annotated[int, Field(strict=True, ge=-1)]

SAVES_DIRNAME = 'Saves'
SAVE_EXT = '.celeste'
MOD_SAVE_DATA_SUFFIX = '-modsavedata'


def settings_dir(game_dir: Path) -> Path:
    """Return Everest's ``PathSettings`` directory for this Game installation.

    Everest lets ``EVEREST_SAVEPATH`` relocate saves. Otherwise it follows the
    platform-specific ``UserIO`` convention, with the Game directory as the
    Windows fallback.
    """
    if path := os.environ.get('EVEREST_SAVEPATH'):
        return Path(path) / SAVES_DIRNAME
    if sys.platform.startswith('linux'):
        if path := os.environ.get('XDG_DATA_HOME'):
            return Path(path) / 'Celeste' / SAVES_DIRNAME
        return Path.home() / '.local' / 'share' / 'Celeste' / SAVES_DIRNAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Celeste' / SAVES_DIRNAME
    return game_dir / SAVES_DIRNAME


def _save_path(saves_dir: Path, name: str) -> Path:
    """Return the on-disk path for one extensionless ``UserIO`` save name."""
    return saves_dir / f'{name}{SAVE_EXT}'


def _save_slot_name(number: int) -> str:
    """Mirror Everest's ``SaveData.GetFilename`` slot-name mapping."""
    return 'debug' if number == -1 else str(number)


def save_slot_path(saves_dir: Path, number: int) -> Path:
    """Return the native save path for one regular or debug slot."""
    return _save_path(saves_dir, _save_slot_name(number))


def mod_save_data_path(saves_dir: Path, number: int) -> Path:
    """Return Everest's backup path for modded data embedded in a save slot."""
    return _save_path(saves_dir, f'{_save_slot_name(number)}{MOD_SAVE_DATA_SUFFIX}')


def mod_save_path(saves_dir: Path, number: int, mod_name: str) -> Path:
    """Return the default per-module save path used by Everest."""
    _validate_mod_name(mod_name)
    return _save_path(saves_dir, f'{_save_slot_name(number)}-modsave-{mod_name}')


def mod_session_path(saves_dir: Path, number: int, mod_name: str) -> Path:
    """Locate an opaque module session using Everest's default filename.

    This only constructs a path. Modules overriding Everest's file location
    are outside this convention and must be coordinated by the caller.
    """
    _validate_mod_name(mod_name)
    return _save_path(saves_dir, f'{_save_slot_name(number)}-modsession-{mod_name}')


def parse_mod_session_name(name: str, number: int) -> str | None:
    """Extract the Mod name from this slot's session filename using host Path rules."""
    prefix = f'{_save_slot_name(number)}-modsession-'
    if Path(name).name != name or not Path(name).match(f'{prefix}*{SAVE_EXT}'):
        return None
    return name[len(prefix) : -len(SAVE_EXT)] or None


def _validate_mod_name(mod_name: str) -> None:
    if (
        not mod_name
        or '\0' in mod_name
        or Path(mod_name).name != mod_name
        or mod_name in ('.', '..')
    ):
        raise ValueError(f'Expected a single Mod name: {mod_name!r}.')
