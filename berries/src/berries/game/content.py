"""Read-only access to Game and Mod content trees."""

from bisect import bisect_left
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from heapq import merge
from itertools import groupby
from os import PathLike
from pathlib import Path, PurePath, PurePosixPath
from string import ascii_letters
from typing import Self, cast
from zipfile import BadZipFile, ZipFile, ZipInfo

from pydantic_core import core_schema

type _StrPath = str | PathLike[str]


def _normalized_virtual_segment(segment: _StrPath) -> str:
    normalized = (
        ''
        if isinstance(segment, PurePath) and not segment.parts
        else str(segment).replace('\\', '/')
    )
    if not normalized:
        return normalized
    has_windows_drive = (
        len(normalized) >= 2 and normalized[0] in ascii_letters and normalized[1] == ':'
    )
    if has_windows_drive or normalized.startswith('/') or normalized.endswith('/'):
        raise ValueError('Content paths must be relative and must not end with a separator.')
    if any(part in {'', '.', '..'} for part in normalized.split('/')):
        raise ValueError('Content paths must not contain empty, dot, or parent segments.')
    return normalized


def _normalized_virtual_segments(segments: tuple[_StrPath, ...]) -> tuple[str, ...]:
    return tuple(_normalized_virtual_segment(segment) for segment in segments)


def _dir_entry_path(*segments: _StrPath) -> PurePath:
    path = PurePath(*segments)
    if path.is_absolute() or path.drive or '..' in path.parts:
        raise ValueError('Directory entry paths must be relative and remain within their root.')
    return path


class ContentPath(PurePosixPath):
    """An exact, relative path in Everest's virtual content tree.

    This is a supported subset of Everest paths, not its resource lookup model.
    Backslashes become slashes; empty, dot, parent, absolute, and drive-prefixed
    segments are rejected before pathlib can normalize them. In particular,
    repeated separators are unsupported rather than aliases of another path.
    Everest may register such names and later redirect reads or fall back to
    physical files; this type deliberately does not emulate those edge cases.

    Ordinary ZIPs created from a directory tree with relative member names,
    single separators, and no dot/parent segments have no such discrepancy.
    ZIP reads retain original member names and ZipInfo independently of this
    value, including duplicate names and file/directory coexistence. Hidden
    entries remain representable here; resource loaders decide to ignore them.
    Pass raw strings at external boundaries: a preconstructed PurePath may
    already have discarded unsupported segments and cannot recover them.
    """

    def __init__(self, *segments: _StrPath) -> None:
        super().__init__(*_normalized_virtual_segments(segments))

    def with_segments(self, *segments: _StrPath) -> Self:
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


CONTENT_DIRNAME = 'Content'
MAPS_DIR = ContentPath('Maps')
DIALOG_DIR = ContentPath('Dialog')


class BadContentEntry(Exception):
    pass


class BadZipContentEntry(BadContentEntry, BadZipFile):
    pass


@dataclass(frozen=True, slots=True)
class ZipMemberIndex:
    """Sorted exact ZIP member paths for binary and prefix lookup.

    ``files`` and ``dirs`` contain unique, normalized, case-sensitive paths in
    lexicographic order. ``dirs`` contains only explicit directory entries;
    directory queries also infer implicit directories from descendant members.
    ``entries`` maps virtual names to the first ZIP occurrence of the last
    registered original name. ``invalid_paths`` preserves skipped raw names.
    """

    files: tuple[str, ...]
    dirs: tuple[str, ...]
    invalid_paths: tuple[str, ...]
    entries: Mapping[str, ZipInfo]

    @staticmethod
    def _contains(values: tuple[str, ...], path: str) -> bool:
        index = bisect_left(values, path)
        return index < len(values) and values[index] == path

    @staticmethod
    def _contains_descendant(values: tuple[str, ...], path: str) -> bool:
        prefix = f'{path}/'
        index = bisect_left(values, prefix)
        return index < len(values) and values[index].startswith(prefix)

    def has_file(self, path: str) -> bool:
        return self._contains(self.files, path)

    def has_dir(self, path: str) -> bool:
        return (
            self._contains(self.dirs, path)
            or self._contains_descendant(self.files, path)
            or self._contains_descendant(self.dirs, path)
        )

    @staticmethod
    def _descendants(values: tuple[str, ...], path: str) -> Iterator[str]:
        prefix = f'{path}/' if path else ''
        start = bisect_left(values, prefix)
        for i in range(start, len(values)):
            value = values[i]
            if prefix and not value.startswith(prefix):
                break
            yield value

    def files_below(self, path: str) -> Iterator[str]:
        return self._descendants(self.files, path)

    def dirs_below(self, path: str) -> Iterator[str]:
        return self._descendants(self.dirs, path)


@dataclass(frozen=True, slots=True)
class _ZipChildren:
    dirs: tuple[str, ...]
    files: tuple[str, ...]


class ContentZipFile(ZipFile):
    """A read-only content archive with lazy member and directory caches.

    ``member_index.entries`` selects original members by normalized virtual name;
    inherited ``getinfo`` retains the standard library's name lookup semantics.
    Use the selected ZipInfo with ``open`` or ``read`` for content lookups.
    Directory traversal caches are private implementation shared with
    ZipContentEntry, not part of the public archive interface.
    """

    def __init__(self, root: _StrPath) -> None:
        try:
            super().__init__(root)
        except BadZipFile:
            raise BadZipContentEntry(f'{root!r} is not a ZIP file.') from None
        self.root = Path(root)
        self._children: dict[str, _ZipChildren] = {}
        self._cached_subtrees: set[str] = set()

    @cached_property
    def member_index(self) -> ZipMemberIndex:
        """Validate and index ZIP members without building directory relationships."""
        files: set[str] = set()
        dirs: set[str] = set()
        invalid_paths: list[str] = []
        first_entries: dict[str, ZipInfo] = {}
        selected_names: dict[str, str] = {}
        for info in self.infolist():
            raw = info.orig_filename
            try:
                virtual = raw.replace('\\', '/')
                path = _normalized_virtual_segment(virtual.removesuffix('/'))
            except ValueError:
                invalid_paths.append(raw)
                continue
            if not path:
                continue
            if raw not in first_entries:
                first_entries[raw] = info
            selected_names[virtual] = raw
            if virtual.endswith('/'):
                dirs.add(path)
            else:
                files.add(path)

        return ZipMemberIndex(
            files=tuple(sorted(files)),
            dirs=tuple(sorted(dirs)),
            invalid_paths=tuple(invalid_paths),
            entries={key: first_entries[raw] for key, raw in selected_names.items()},
        )

    def _children_at(self, path: str) -> _ZipChildren:
        """Return and cache one directory's immediate children."""
        children = self._children.get(path)
        if children is not None:
            return children

        prefix = f'{path}/' if path else ''
        dirs: set[str] = set()
        files: set[str] = set()
        member_index = self.member_index

        for member in member_index.dirs_below(path):
            relative = member[len(prefix) :]
            if relative:
                dirs.add(relative.partition('/')[0])

        for member in member_index.files_below(path):
            relative = member[len(prefix) :]
            if not relative:
                continue
            name, sep, _ = relative.partition('/')
            if sep:
                dirs.add(name)
            else:
                files.add(name)

        result = _ZipChildren(tuple(sorted(dirs)), tuple(sorted(files)))
        self._children[path] = result
        return result

    def _cache_subtree(self, base: str) -> None:
        """Populate adjacency entries for one subtree in a single member pass."""
        if base in self._cached_subtrees:
            return
        prefix = f'{base}/' if base else ''
        dir_children: dict[str, set[str]] = {}
        file_children: dict[str, set[str]] = {}
        directories = {base}

        def add_path(path: str, *, is_dir: bool) -> None:
            relative = path[len(prefix) :]
            if not relative:
                return
            parts = relative.split('/')
            parent = base
            for part in parts[:-1]:
                dir_children.setdefault(parent, set()).add(part)
                parent = f'{parent}/{part}' if parent else part
                directories.add(parent)
            children = dir_children if is_dir else file_children
            children.setdefault(parent, set()).add(parts[-1])
            if is_dir:
                child = f'{parent}/{parts[-1]}' if parent else parts[-1]
                directories.add(child)

        member_index = self.member_index
        for path in member_index.dirs_below(base):
            add_path(path, is_dir=True)
        for path in member_index.files_below(base):
            add_path(path, is_dir=False)

        for directory in directories:
            dirs = dir_children.get(directory, set())
            files = file_children.get(directory, set())
            self._children[directory] = _ZipChildren(tuple(sorted(dirs)), tuple(sorted(files)))
        self._cached_subtrees.update(directories)


class ContentEntry:
    """A node within a directory or ZIP content tree.

    Directory paths use the host filesystem's case rules. ZIP entry paths are
    always exact, matching Everest's archive lookups on every platform.
    """

    root: Path

    def __new__(cls, root: _StrPath, *_: object, **__: object) -> Self:
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
    def at(self) -> PurePath:
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

    def walk(self) -> Iterator[tuple[Self, list[str], list[str]]]:
        """Walk this content subtree from the top down, like :meth:`pathlib.Path.walk`."""
        raise NotImplementedError

    def joinpath(self, *_: _StrPath) -> Self:
        raise NotImplementedError

    def __truediv__(self, part: _StrPath) -> Self:
        return self.joinpath(part)

    def read_bytes(self) -> bytes:
        raise NotImplementedError

    def read_text(self, *, encoding: str = 'utf-8-sig') -> str:
        return self.read_bytes().decode(encoding)


class DirContentEntry(ContentEntry):
    def __init__(self, root: _StrPath, at: _StrPath = '') -> None:
        self.root = Path(root)
        self._at = _dir_entry_path(at)

    @property
    def at(self) -> PurePath:
        return self._at

    @property
    def path(self) -> Path:
        """Return the physical filesystem path represented by this entry."""
        return self.root.joinpath(*self.at.parts)

    @property
    def name(self) -> str:
        return self.path.name

    def exists(self) -> bool:
        return self.path.exists()

    def is_dir(self) -> bool:
        return self.path.is_dir()

    def is_file(self) -> bool:
        return self.path.is_file()

    def iterdir(self) -> Iterator[Self]:
        if not self.is_dir():
            raise NotADirectoryError(self.path)
        for path in self.path.iterdir():
            yield type(self)(self.root, self.at / path.name)

    def walk(self) -> Iterator[tuple[Self, list[str], list[str]]]:
        for directory, dirnames, filenames in self.path.walk():
            yield type(self)(self.root, directory.relative_to(self.root)), dirnames, filenames

    def joinpath(self, *parts: _StrPath) -> Self:
        return type(self)(self.root, self.at.joinpath(*parts))

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


class ZipContentEntry(ContentEntry):
    def __init__(self, root: _StrPath) -> None:
        self._zip = ContentZipFile(root)
        self.root = self._zip.root
        self._at = PurePosixPath()

    def __exit__(self, *_: object) -> None:
        self._zip.close()

    @property
    def at(self) -> PurePosixPath:
        return self._at

    @property
    def zip_file(self) -> ContentZipFile:
        """Borrow the content archive until this content root is closed.

        Internal member indexes and caches are not part of this public contract.
        """
        return self._zip

    def _next(self, at: PurePosixPath) -> Self:
        result = object.__new__(type(self))
        result._zip = self._zip  # noqa: SLF001
        result.root = self.root
        result._at = at  # noqa: SLF001
        return result

    def _path_key(self) -> str:
        """Return the archive key used by the shared caches."""
        return self.at.as_posix() if self.at.parts else ''

    @property
    def name(self) -> str:
        return self.at.name or self.root.name

    def exists(self) -> bool:
        if not self.at.parts:
            return True
        path = self._path_key()
        index = self._zip.member_index
        return index.has_file(path) or index.has_dir(path)

    def is_dir(self) -> bool:
        if not self.at.parts:
            return True
        return self._zip.member_index.has_dir(self._path_key())

    def is_file(self) -> bool:
        return self._zip.member_index.has_file(self._path_key())

    def iterdir(self) -> Iterator[Self]:
        if not self.is_dir():
            raise NotADirectoryError(self.at)
        children = self._zip._children_at(self._path_key())  # noqa: SLF001
        for name, _ in groupby(merge(children.dirs, children.files)):
            yield self._next(self.at / name)

    def walk(self) -> Iterator[tuple[Self, list[str], list[str]]]:
        """Walk top-down, allowing in-place pruning or reordering of yielded dirnames.

        As with Path.walk(), changes to dirnames control subsequent traversal.
        The yielded lists are independent of cached directory contents, so edits
        do not affect later walks.
        A name may occur in both dirnames and filenames when the ZIP has a
        file and a directory with the same name.
        """
        if not self.is_dir():
            return
        self._zip._cache_subtree(self._path_key())  # noqa: SLF001

        def visit(entry: Self) -> Iterator[tuple[Self, list[str], list[str]]]:
            children = self._zip._children_at(entry._path_key())  # noqa: SLF001
            dirnames = list(children.dirs)
            filenames = list(children.files)
            yield entry, dirnames, filenames
            for dirname in dirnames:
                yield from visit(entry._next(entry.at / dirname))

        yield from visit(self)

    def joinpath(self, *parts: _StrPath) -> Self:
        return self._next(self.at.joinpath(*_normalized_virtual_segments(parts)))

    def read_bytes(self) -> bytes:
        return self._zip.read(self._zip.member_index.entries[self.at.as_posix()])
