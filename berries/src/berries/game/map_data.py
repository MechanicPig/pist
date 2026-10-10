"""Read decoded maps from game content, Mod directories, and Mod archives."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from berries.game import binmap, content
from berries.game.levels import Map


@contextmanager
def open_map_entry(map_file: Map) -> Generator[content.ContentEntry]:
    """Open an existing map resource, reporting unavailable sources as ValueError."""
    try:
        with map_file.open_content() as root:
            yield _map_entry(root, map_file.file_path)
    except content.BadContentEntry as error:
        raise ValueError(f'Invalid content source for map: {map_file.file_path!r}') from error
    except OSError as error:
        raise ValueError(
            f'Cannot read map {map_file.file_path!r} from {map_file.content.path!r}: {error}'
        ) from error


def load_map_data(map_file: Map) -> binmap.BinMap:
    """Decode one active map, allowing the game's trailing map payload."""
    with open_map_entry(map_file) as entry:
        return binmap.parse_map_bin(entry.read_bytes(), allow_trailing=True)


def load_map_data_from_path(path: Path, map_file: content.StrPath) -> binmap.BinMap:
    """Decode one map from a content directory or archive, allowing trailing payload."""
    try:
        with content.ContentEntry(path) as root:
            return binmap.parse_map_bin(
                _map_entry(root, map_file).read_bytes(), allow_trailing=True
            )
    except content.BadContentEntry as error:
        raise ValueError(f'Invalid map package path: {path!r}') from error
    except OSError as error:
        raise ValueError(f'Cannot read map {map_file!r} from {path!r}: {error}') from error


def _map_entry(root: content.ContentEntry, map_file: content.StrPath) -> content.ContentEntry:
    entry = root.joinpath(map_file)
    if not entry.is_file():
        raise ValueError(f'Map file does not exist in {root.root!r}: {map_file!r}')
    return entry
