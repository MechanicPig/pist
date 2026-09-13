"""Unified, read-only access to directory and ZIP Mods."""

from collections.abc import Iterator
from itertools import chain
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Literal, Self, cast
from zipfile import BadZipFile, ZipFile

from pist.containers import CaseFoldDict

type StrPath = str | PathLike[str]
ROOT_PATH = PurePosixPath()


class BadModPath(Exception):
    pass


class BadZipModPath(BadModPath, BadZipFile):
    pass


class ModPath:
    """A path within either a directory Mod or a ZIP Mod."""

    def __new__(cls, root: StrPath, *_: object, **__: object) -> Self:
        if cls is not ModPath:
            return object.__new__(cls)

        path = Path(root)
        if path.is_dir():
            return cast(Self, object.__new__(DirModPath))
        if path.is_file() and path.suffix.casefold() == '.zip':
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

    def joinpath(self, *parts: StrPath) -> Self:
        raise NotImplementedError

    __truediv__ = joinpath

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
        path = self.root
        for part in self.at.parts:
            if not path.is_dir():
                return path / part
            path = next(
                (item for item in path.iterdir() if item.name.casefold() == part.casefold()),
                path / part,
            )
        return path

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
        self._files: CaseFoldDict[PurePosixPath] = CaseFoldDict()
        self._dirs: CaseFoldDict[PurePosixPath] = CaseFoldDict()
        self._index_paths()

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
        result._files = self._files
        result._dirs = self._dirs
        return result

    def _index_paths(self) -> None:
        for info in self._archive.infolist():
            path = PurePosixPath(info.filename.rstrip('/'))
            if not path.parts:
                continue
            if info.is_dir():
                self._dirs[path.as_posix()] = path
            else:
                self._files[path.as_posix()] = path
            for parent in path.parents:
                if parent.parts:
                    self._dirs[parent.as_posix()] = parent

    @property
    def name(self) -> str:
        return self.at.name or self.root.name

    def exists(self) -> bool:
        return (
            not self.at.parts
            or self.at.as_posix() in self._files
            or self.at.as_posix() in self._dirs
        )

    def is_dir(self) -> bool:
        return not self.at.parts or self.at.as_posix() in self._dirs

    def is_file(self) -> bool:
        return self.at.as_posix() in self._files

    def iterdir(self) -> Iterator[Self]:
        if not self.is_dir():
            raise NotADirectoryError(self.at)
        paths = {
            path
            for path in chain(self._files.values(), self._dirs.values())
            if path.parent == self.at
        }
        for path in sorted(paths):
            yield self._next(path)

    def joinpath(self, *parts: StrPath) -> Self:
        return self._next(self.at.joinpath(*map(str, parts)))

    def read_bytes(self) -> bytes:
        path = self._files[self.at.as_posix()]
        return self._archive.read(path.as_posix())


def iter_files(root: ModPath) -> Iterator[ModPath]:
    for path in root.iterdir():
        if path.is_dir():
            yield from iter_files(path)
        elif path.is_file():
            yield path
