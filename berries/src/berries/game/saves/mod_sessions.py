"""Explicit native file operations for a slot's opaque Mod session group.

These functions do not load saves, select an active session, or write main or
Collab documents. Call them only when the associated active session is copied,
moved or discarded. I/O errors propagate; previous changes are not rolled back.
"""

import shutil

from .paths import mod_session_path
from .slot import SaveSlot


def clear_mod_sessions(slot: SaveSlot) -> None:
    """Delete the discovered group, leaving every other slot/file untouched."""
    for path in slot.mod_session_paths().values():
        path.unlink()


def _transfer(source: SaveSlot, target: SaveSlot, *, move: bool) -> None:
    # Same-slot replacement would otherwise delete or move its own inputs.
    # Compare directory identities, but do not add file locks or race protection.
    if source.number == target.number:
        try:
            same_directory = source.saves_dir.samefile(target.saves_dir)
        except FileNotFoundError:
            same_directory = False
        if same_directory:
            raise ValueError('Source and target Mod session slots must be distinct.')
    incoming = source.mod_session_paths()
    destinations = {
        name: mod_session_path(target.saves_dir, target.number, name) for name in incoming
    }
    target.saves_dir.mkdir(parents=True, exist_ok=True)
    previous = target.mod_session_paths()
    for name, path in incoming.items():
        destination = destinations[name]
        # shutil.move accepts directories as destinations. Here a destination
        # always means a file, not a directory to move the file into.
        if destination.is_dir():
            raise IsADirectoryError(f'Expected a Mod session file: {destination!r}.')
        if move:
            shutil.move(path, destination)  # pyright: ignore[reportUnusedCallResult]
        else:
            shutil.copyfile(path, destination)  # pyright: ignore[reportUnusedCallResult]
    # Replace the complete group: an absent source module means the target must
    # not restore its stale session. Cleanup occurs after the incoming files.
    installed = set(destinations.values())
    for path in previous.values():
        if path not in installed:
            path.unlink()


def copy_mod_sessions(source: SaveSlot, target: SaveSlot) -> None:
    """Copy bytes and remove target-only files; leave source files unchanged.

    Uses shutil.copyfile, not copy2: metadata is not copied. No content decoding,
    backup, transaction, retry or implicit save-document write is performed.
    A missing source directory raises; an existing empty group clears target.
    """
    _transfer(source, target, move=False)


def move_mod_sessions(source: SaveSlot, target: SaveSlot) -> None:
    """Move each file natively, then remove target-only files.

    Uses shutil.move, including its cross-filesystem fallback. A failure can
    leave some files moved and others at source; no rollback is attempted.
    """
    _transfer(source, target, move=True)
