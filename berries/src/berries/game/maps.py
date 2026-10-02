"""Scanned map assets."""

import re
from typing import Annotated, Literal, cast

from pydantic import AfterValidator

from berries.game.content import MAPS_DIR, ContentPath
from berries.models import FrozenModel

MAP_SIDE_SUFFIX_PATTERN = re.compile(r'-(?P<side>[BC])$')
type MapSideSuffix = Literal['B', 'C']


def _map_file_path(path: ContentPath) -> ContentPath:
    parts = path.parts
    if len(parts) < 2 or parts[0] != MAPS_DIR.name or path.suffix != '.bin':
        raise ValueError(f'Not a map file path: {path!r}')
    return path


type MapFilePath = Annotated[ContentPath, AfterValidator(_map_file_path)]


class MapInfo(FrozenModel):
    """One scanned map asset identified only by its virtual content path.

    Internal construction takes a ``ContentPath``. External mappings or JSON
    may contain a string path, normalized and checked by model validation.
    """

    file_path: MapFilePath


def campaign_dir_for_map_file(map_file: ContentPath) -> ContentPath:
    """Return the ``Maps``-relative campaign directory for one map file."""
    return _map_file_path(map_file).parent


def split_map_side_suffix(map_file: ContentPath) -> tuple[ContentPath, MapSideSuffix | None]:
    """Split a ``-B`` or ``-C`` map suffix from its base map file."""
    match = MAP_SIDE_SUFFIX_PATTERN.search(map_file.stem)
    if match is None:
        return map_file, None
    suffix = cast(MapSideSuffix, match['side'])
    return map_file.with_stem(map_file.stem[: match.start()]), suffix
