"""Read decoded maps from game content, Mod directories, and Mod archives."""

from collections.abc import Generator
from contextlib import contextmanager
from os import PathLike
from pathlib import Path

from .binmap import BinMap, parse_map_bin
from .content import BadContentEntry, ContentEntry
from .maps import Map

type _StrPath = str | PathLike[str]


@contextmanager
def open_map_entry(map_file: Map) -> Generator[ContentEntry]:
    """Open an existing map resource, reporting unavailable sources as ValueError."""
    try:
        with map_file.open_content() as root:
            yield _map_entry(root, map_file.file_path)
    except BadContentEntry as error:
        raise ValueError(f'Invalid content source for map: {map_file.file_path!r}') from error
    except OSError as error:
        raise ValueError(f'Cannot read map {map_file.file_path!r}: {error}') from error


def load_map_data(map_file: Map) -> BinMap:
    """Decode one active map, allowing the game's trailing map payload."""
    with open_map_entry(map_file) as entry:
        return parse_map_bin(entry.read_bytes(), allow_trailing=True)


def load_map_data_from_path(path: Path, map_file: _StrPath) -> BinMap:
    """Decode one map from a content directory or archive, allowing trailing payload."""
    try:
        with ContentEntry(path) as root:
            return parse_map_bin(_map_entry(root, map_file).read_bytes(), allow_trailing=True)
    except BadContentEntry as error:
        raise ValueError(f'Invalid map package path: {path!r}') from error
    except OSError as error:
        raise ValueError(f'Cannot read map {map_file!r} from {path!r}: {error}') from error


def _map_entry(root: ContentEntry, map_file: _StrPath) -> ContentEntry:
    entry = root / map_file
    if not entry.is_file():
        raise ValueError(f'Map file does not exist in {root.root!r}: {map_file!r}')
    return entry
