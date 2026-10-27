"""Save-directory access and a read-only slot range with explicit cache invalidation."""

from collections.abc import Iterable, Iterator, Sequence
from functools import cached_property
from os import PathLike
from pathlib import Path
from stat import S_ISLNK, S_ISREG
from types import EllipsisType
from typing import Self, overload

from .paths import save_slot_path, settings_dir
from .slot import SaveSlot


class SaveSlots(Sequence[SaveSlot]):
    """Cached numbered positions, including holes, with debug outside the sequence.

    Integer indexes are nonnegative and bounded by the cached length. Slices
    use standard slice bounds and return an immutable snapshot of locations.
    Ellipsis selects debug without including it in normal iteration or length.
    The range is discovered on first use and rediscovered after invalidate_cache(). No
    documents are loaded; each slot still checks current files when accessed.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)

    def __len__(self) -> int:
        return self._length

    @cached_property
    def _length(self) -> int:
        highest = 2
        try:
            for path in self._path.iterdir():
                stem = path.stem
                if not stem.isascii() or not stem.isdecimal():
                    continue
                number = int(stem)
                if path == save_slot_path(self._path, number):
                    try:
                        info = path.lstat()
                    except FileNotFoundError:
                        continue
                    if S_ISREG(info.st_mode) or S_ISLNK(info.st_mode):
                        highest = max(highest, number)
        except FileNotFoundError:
            if self._path.exists():
                raise
        return highest + 1

    def invalidate_cache(self) -> None:
        """Forget the cached length so it will be recomputed on next access."""
        try:
            del self._length
        except AttributeError:
            pass

    def iter(self, *, filter_empty: bool = False, reverse: bool = False) -> Iterator[SaveSlot]:
        numbers: Iterable[int] = range(len(self))
        if reverse:
            numbers = reversed(numbers)

        for number in numbers:
            slot = SaveSlot(self._path, number)
            if not filter_empty or slot:
                yield slot

    def __iter__(self) -> Iterator[SaveSlot]:
        return self.iter()

    def __reversed__(self) -> Iterator[SaveSlot]:
        return self.iter(reverse=True)

    @overload
    def __getitem__(self, index: int | EllipsisType, /) -> SaveSlot: ...
    @overload
    def __getitem__(self, index: slice, /) -> tuple[SaveSlot, ...]: ...
    def __getitem__(self, index: int | EllipsisType | slice) -> SaveSlot | tuple[SaveSlot, ...]:
        if isinstance(index, EllipsisType):
            return SaveSlot(self._path, -1)
        if isinstance(index, slice):
            return tuple(SaveSlot(self._path, i) for i in range(*index.indices(len(self))))
        if index < 0 or index >= len(self):
            raise IndexError('Slot index is outside the ordinary range; use slots[...] for debug.')
        return SaveSlot(self._path, index)


class SavesDir:
    """A save directory; locating slots never creates or reserves files."""

    def __init__(self, path: str | PathLike[str]) -> None:
        self._path = Path(path)
        self._slots = SaveSlots(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def slots(self) -> SaveSlots:
        return self._slots

    @classmethod
    def from_game_dir(cls, game_dir: str | PathLike[str]) -> Self:
        return cls(settings_dir(Path(game_dir)))

    def find_empty_slot(self, start: int = 0) -> SaveSlot:
        """Find the first missing main file at or after start, defaulting to zero.

        Companion files do not occupy a slot; corrupt files and symbolic links
        do. Access errors and invalid main-file types propagate. Nothing is
        created or reserved, so callers decide when and how to write documents.
        """
        if start < 0:
            raise ValueError('The starting slot number must be nonnegative.')
        number = start
        while slot := SaveSlot(self.path, number):
            number += 1
        return slot
