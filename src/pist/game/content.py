"""Read-only access to Game and Mod content trees."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, Self, cast
from zipfile import BadZipFile, ZipFile

from pydantic_core import core_schema

type StrPath = str | PathLike[str]


def _normalized_content_segments(segments: tuple[StrPath, ...]) -> tuple[str, ...]:
    normalized = tuple(str(segment).replace('\\', '/') for segment in segments)
    for segment in normalized:
        if not segment:
            continue
        if PureWindowsPath(segment).drive or segment.startswith('/') or segment.endswith('/'):
            raise ValueError('Content paths must be relative and must not end with a separator.')
        if any(part in {'', '.', '..'} for part in segment.split('/')):
            raise ValueError('Content paths must not contain empty, dot, or parent segments.')
    return normalized


class ContentPath(PurePosixPath):
    """An exact, relative path in Everest's virtual content tree.

    The value follows ``PurePosixPath``'s structural API while rejecting the
    normalization that would otherwise hide invalid empty, dot, parent, or
    absolute segments. Everest's accepted backslash separator is normalized
    before the immutable path value is constructed.
    """

    def __init__(self, *segments: StrPath) -> None:
        super().__init__(*_normalized_content_segments(segments))

    def with_segments(self, *segments: StrPath) -> Self:
        """Construct a derived path without exposing pathlib's ``'.'`` root spelling."""
        return type(self)(
            *(
                '' if isinstance(segment, ContentPath) and not segment.parts else segment
                for segment in segments
            )
        )

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: object, _handler: object
    ) -> core_schema.CoreSchema:
        from_string = core_schema.no_info_after_validator_function(cls, core_schema.str_schema())
        return core_schema.json_or_python_schema(
            json_schema=from_string,
            python_schema=core_schema.union_schema(
                [core_schema.is_instance_schema(cls), from_string]
            ),
            serialization=core_schema.to_string_ser_schema(),
        )


ROOT_PATH = ContentPath()


class ContentSource(Protocol):
    """A physical source that can open one Everest content tree."""

    path: Path

    def open(self) -> ContentEntry: ...


@dataclass(frozen=True, slots=True)
class GameContent:
    """The Game installation's always-loaded Content directory."""

    path: Path

    def open(self) -> DirContentEntry:
        return DirContentEntry(self.path)


@dataclass(frozen=True, slots=True)
class _ZipPathIndex:
    """Cached ZIP file and implicit-directory paths for traversal."""

    files: frozenset[str]
    dirs: frozenset[str]
    children: dict[str, tuple[str, ...]]


@dataclass(slots=True)
class _ZipArchive:
    """One open ZIP content source and its lazily built shared path index."""

    root: Path
    archive: ZipFile
    index: _ZipPathIndex | None = None
    invalid_member_paths: list[str] = field(default_factory=list)

    @classmethod
    def open(cls, root: StrPath) -> Self:
        try:
            archive = ZipFile(root)
        except BadZipFile:
            raise BadZipContentEntry(f'{root!r} is not a ZIP file.') from None
        return cls(Path(root), archive)

    def close(self) -> None:
        self.archive.close()

    def path_index(self) -> _ZipPathIndex:
        """Build one shared index when a ZIP directory must be traversed."""
        if self.index is not None:
            return self.index
        files: set[str] = set()
        dirs: set[str] = set()
        child_sets: dict[str, set[str]] = {}
        for info in self.archive.infolist():
            try:
                path = ContentPath(info.filename.rstrip('/'))
            except ValueError:
                self.invalid_member_paths.append(info.filename)
                continue
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
        self.index = _ZipPathIndex(
            files=frozenset(files),
            dirs=frozenset(dirs),
            children={parent: tuple(sorted(children)) for parent, children in child_sets.items()},
        )
        return self.index


class BadContentEntry(Exception):
    pass


class BadZipContentEntry(BadContentEntry, BadZipFile):
    pass


class ContentEntry:
    """A node within a directory or ZIP content tree.

    Directory paths use the host filesystem's case rules. ZIP entry paths are
    always exact, matching Everest's archive lookups on every platform.
    """

    root: Path

    def __new__(cls, root: StrPath, *_: object, **__: object) -> Self:
        if cls is not ContentEntry:
            return object.__new__(cls)

        path = Path(root)
        if path.is_dir():
            return cast(Self, object.__new__(DirContentEntry))
        if path.is_file() and path.suffix == '.zip':
            return cast(Self, object.__new__(ZipContentEntry))
        raise BadContentEntry('Invalid content root, expected a directory or ZIP file.')

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    @property
    def at(self) -> ContentPath:
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

    def fingerprint(self) -> str:
        """Return a cheap signature that changes when this file entry changes."""
        raise NotImplementedError


class DirContentEntry(ContentEntry):
    def __init__(self, root: StrPath, at: ContentPath = ROOT_PATH) -> None:
        self.root = Path(root)
        self._at = at

    @property
    def at(self) -> ContentPath:
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

    def fingerprint(self) -> str:
        stat = self._path().stat()
        return f'{stat.st_size}:{stat.st_mtime_ns}'


class ZipContentEntry(ContentEntry):
    def __init__(self, root: StrPath) -> None:
        self._zip = _ZipArchive.open(root)
        self.root = self._zip.root
        self._at = ROOT_PATH

    def __exit__(self, *_: object) -> None:
        self._zip.close()

    @property
    def at(self) -> ContentPath:
        return self._at

    def _next(self, at: ContentPath) -> Self:
        result = object.__new__(type(self))
        result._zip = self._zip
        result.root = self.root
        result._at = at
        return result

    def _path_index(self) -> _ZipPathIndex:
        return self._zip.path_index()

    def invalid_member_paths(self) -> tuple[str, ...]:
        """Return archive members omitted because they are not valid content paths."""
        self._path_index()
        return tuple(self._zip.invalid_member_paths)

    def _has_explicit_file(self) -> bool:
        try:
            return not self._zip.archive.getinfo(self.at.as_posix()).is_dir()
        except KeyError:
            return False

    def _path_key(self) -> str:
        """Return the archive key used by the shared traversal index."""
        return self.at.as_posix() if self.at.parts else ''

    def _has_explicit_dir(self) -> bool:
        try:
            return self._zip.archive.getinfo(f'{self.at.as_posix()}/').is_dir()
        except KeyError:
            return False

    @property
    def name(self) -> str:
        return self.at.name or self.root.name

    def exists(self) -> bool:
        if not self.at.parts:
            return True
        if (index := self._zip.index) is not None:
            return self._path_key() in index.files or self._path_key() in index.dirs
        return (
            self._has_explicit_file()
            or self._has_explicit_dir()
            or self._path_key() in self._path_index().dirs
        )

    def is_dir(self) -> bool:
        if not self.at.parts:
            return True
        if (index := self._zip.index) is not None:
            return self._path_key() in index.dirs
        return self._has_explicit_dir() or self._path_key() in self._path_index().dirs

    def is_file(self) -> bool:
        if (index := self._zip.index) is not None:
            return self._path_key() in index.files
        return self._has_explicit_file()

    def iterdir(self) -> Iterator[Self]:
        if not self.is_dir():
            raise NotADirectoryError(self.at)
        for path in self._path_index().children.get(self._path_key(), ()):
            yield self._next(ContentPath(path))

    def joinpath(self, *parts: StrPath) -> Self:
        return self._next(self.at.joinpath(*map(str, parts)))

    def read_bytes(self) -> bytes:
        return self._zip.archive.read(self.at.as_posix())

    def fingerprint(self) -> str:
        info = self._zip.archive.getinfo(self.at.as_posix())
        return f'{info.CRC}:{info.compress_size}:{info.file_size}:{info.date_time}'


def iter_files(root: ContentEntry) -> Iterator[ContentEntry]:
    for path in root.iterdir():
        if path.is_dir():
            yield from iter_files(path)
        elif path.is_file():
            yield path
