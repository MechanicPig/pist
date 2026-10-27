"""Scanned map assets."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import AfterValidator

from .content import MAPS_DIR, ContentEntry, ContentPath, DirContentEntry
from .dialog import dialog_key_for_map_file

if TYPE_CHECKING:
    from .mods import InstalledMod

MAP_SIDE_SUFFIX_PATTERN = re.compile(r'-(?P<side>[BC])$')
type MapSideSuffix = Literal['B', 'C']


def _validate_map_file_path(path: ContentPath) -> None:
    parts = path.parts
    if len(parts) < 2 or parts[0] != MAPS_DIR.name or path.suffix != '.bin':
        raise ValueError(f'Not a map file path: {path!r}')


def _map_file_path(path: ContentPath) -> ContentPath:
    _validate_map_file_path(path)
    return path


type MapFilePath = Annotated[ContentPath, AfterValidator(_map_file_path)]


@dataclass(frozen=True, slots=True)
class Map(ABC):
    """One map asset with an exact virtual path and source-specific reading."""

    file_path: ContentPath

    def __post_init__(self) -> None:
        _validate_map_file_path(self.file_path)

    @property
    def dialog_key(self) -> str:
        """Return the Dialog key for this exact map file, without side grouping."""
        return dialog_key_for_map_file(self.file_path)

    @property
    def campaign_dir(self) -> ContentPath:
        """Return this map's campaign directory without reading its source."""
        return self.file_path.parent

    @abstractmethod
    def open_content(self) -> ContentEntry:
        """Open the content tree that supplies this asset for a bounded operation."""


@dataclass(frozen=True, slots=True)
class VanillaMap(Map):
    """One original-game map supplied by a Content directory."""

    content_dir: Path

    def open_content(self) -> DirContentEntry:
        return DirContentEntry(self.content_dir)


@dataclass(frozen=True, slots=True)
class ModMap(Map):
    """One Mod map bound to its installed package."""

    mod: InstalledMod

    def open_content(self) -> ContentEntry:
        return self.mod.open()


def campaign_dir_for_map_file(map_file: ContentPath) -> ContentPath:
    """Return the ``Maps``-relative campaign directory for one map file."""
    return _map_file_path(map_file).parent


def split_map_side_suffix(
    map_file: ContentPath,
) -> tuple[ContentPath, MapSideSuffix | None]:
    """Split a ``-B`` or ``-C`` map suffix from its base map file."""
    match = MAP_SIDE_SUFFIX_PATTERN.search(map_file.stem)
    if match is None:
        return map_file, None
    suffix = cast(MapSideSuffix, match['side'])
    return map_file.with_stem(map_file.stem[: match.start()]), suffix
