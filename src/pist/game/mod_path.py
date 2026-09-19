"""Unified, read-only access to directory and ZIP Mods."""

from collections.abc import Iterator
from dataclasses import dataclass
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Literal, Self, cast
from zipfile import BadZipFile, ZipFile

type StrPath = str | PathLike[str]
ROOT_PATH = PurePosixPath()


@dataclass(frozen=True, slots=True)
class _ZipPathIndex:
    """Cached ZIP file and implicit-directory paths for traversal."""

    files: frozenset[str]
    dirs: frozenset[str]
    children: dict[str, tuple[str, ...]]


@dataclass(slots=True)
class _ZipPathState:
    """Mutable traversal cache shared by all paths in one ZIP Mod."""

    index: _ZipPathIndex | None = None


class BadModPath(Exception):
    pass


class BadZipModPath(BadModPath, BadZipFile):
    pass


class ModPath:
    """A path within a directory Mod or a ZIP Mod.

    Directory paths use the host filesystem's case rules. ZIP entry paths are
    always exact, matching Everest's archive lookups on every platform.
    """

    def __new__(cls, root: StrPath, *_: object, **__: object) -> Self:
        if cls is not ModPath:
            return object.__new__(cls)

        path = Path(root)
        if path.is_dir():
            return cast(Self, object.__new__(DirModPath))
        if path.is_file() and path.suffix == '.zip':
            return cast(Self, object.__new__(ZipModPath))
        raise BadModPath('Invalid Mod path, expected a directory or ZIP file.')

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    @property
    def source(self) -> Literal['zip', 'directory']:
        raise NotImplementedError

    @property
    def at(self) -> PurePosixPath:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    def exists(self) -> bool:
        raise NotImplementedError

    def is_dir(self) -> bool:
        raise NotImplementedError

    def is_file(self) -> bool:
        raise NotImplementedError

    def iterdir(self) -> Iterator[Self]:
        raise NotImplementedError

    def joinpath(self, *_: StrPath) -> Self:
        raise NotImplementedError

    def __truediv__(self, part: StrPath) -> Self:
        return self.joinpath(part)

    def read_bytes(self) -> bytes:
        raise NotImplementedError

    def read_text(self, *, encoding: str = 'utf-8-sig') -> str:
        return self.read_bytes().decode(encoding)


class DirModPath(ModPath):
    def __init__(self, root: StrPath, at: PurePosixPath = ROOT_PATH) -> None:
        self.root = Path(root)
        self._at = at

    @property
    def source(self) -> Literal['directory']:
        return 'directory'

    @property
    def at(self) -> PurePosixPath:
        return self._at

    def _path(self) -> Path:
        return self.root.joinpath(*self.at.parts)

    @property
    def name(self) -> str:
        return self._path().name

    def exists(self) -> bool:
        return self._path().exists()

    def is_dir(self) -> bool:
        return self._path().is_dir()

    def is_file(self) -> bool:
        return self._path().is_file()

    def iterdir(self) -> Iterator[Self]:
        if not self.is_dir():
            raise NotADirectoryError(self._path())
        for path in self._path().iterdir():
            yield type(self)(self.root, self.at / path.name)

    def joinpath(self, *parts: StrPath) -> Self:
        return type(self)(self.root, self.at.joinpath(*map(str, parts)))

    def read_bytes(self) -> bytes:
        return self._path().read_bytes()


class ZipModPath(ModPath):
    def __init__(self, root: StrPath) -> None:
        try:
            self._archive = ZipFile(root)
        except BadZipFile:
            raise BadZipModPath(f'{root!r} is not a ZIP file.') from None
        self.root = Path(root)
        self._at = ROOT_PATH
        self._state = _ZipPathState()

    def __exit__(self, *_: object) -> None:
        self._archive.close()

    @property
    def source(self) -> Literal['zip']:
        return 'zip'

    @property
    def at(self) -> PurePosixPath:
        return self._at

    def _next(self, at: PurePosixPath) -> Self:
        result = object.__new__(type(self))
        result._archive = self._archive
        result.root = self.root
        result._at = at
        result._state = self._state
        return result

    def _path_index(self) -> _ZipPathIndex:
        """Build one shared index when a ZIP directory must be traversed."""
        if self._state.index is not None:
            return self._state.index
        files: set[str] = set()
        dirs: set[str] = set()
        child_sets: dict[str, set[str]] = {}
        for info in self._archive.infolist():
            path = PurePosixPath(info.filename.rstrip('/'))
            if not path.parts:
                continue
            path_string = path.as_posix()
            if info.is_dir():
                dirs.add(path_string)
            else:
                files.add(path_string)
            parent = ''
            for part in path.parts:
                child = f'{parent}/{part}' if parent else part
                child_sets.setdefault(parent, set()).add(child)
                if parent:
                    dirs.add(parent)
                parent = child
        self._state.index = _ZipPathIndex(
            files=frozenset(files),
            dirs=frozenset(dirs),
            children={parent: tuple(sorted(children)) for parent, children in child_sets.items()},
        )
        return self._state.index

    def _has_explicit_file(self) -> bool:
        try:
            return not self._archive.getinfo(self.at.as_posix()).is_dir()
        except KeyError:
            return False

    def _path_key(self) -> str:
        """Return the archive key used by the shared traversal index."""
        return self.at.as_posix() if self.at.parts else ''

    def _has_explicit_dir(self) -> bool:
        try:
            return self._archive.getinfo(f'{self.at.as_posix()}/').is_dir()
        except KeyError:
            return False

    @property
    def name(self) -> str:
        return self.at.name or self.root.name

    def exists(self) -> bool:
        if not self.at.parts:
            return True
        if (index := self._state.index) is not None:
            return self._path_key() in index.files or self._path_key() in index.dirs
        return (
            self._has_explicit_file()
            or self._has_explicit_dir()
            or self._path_key() in self._path_index().dirs
        )

    def is_dir(self) -> bool:
        if not self.at.parts:
            return True
        if (index := self._state.index) is not None:
            return self._path_key() in index.dirs
        return self._has_explicit_dir() or self._path_key() in self._path_index().dirs

    def is_file(self) -> bool:
        if (index := self._state.index) is not None:
            return self._path_key() in index.files
        return self._has_explicit_file()

    def iterdir(self) -> Iterator[Self]:
        if not self.is_dir():
            raise NotADirectoryError(self.at)
        for path in self._path_index().children.get(self._path_key(), ()):
            yield self._next(PurePosixPath(path))

    def joinpath(self, *parts: StrPath) -> Self:
        return self._next(self.at.joinpath(*map(str, parts)))

    def read_bytes(self) -> bytes:
        return self._archive.read(self.at.as_posix())


def iter_files(root: ModPath) -> Iterator[ModPath]:
    for path in root.iterdir():
        if path.is_dir():
            yield from iter_files(path)
        elif path.is_file():
            yield path
