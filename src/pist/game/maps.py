"""Scanned map assets."""

from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from pist.game.content import ContentPath
from pist.models import FrozenModel


def _map_file_path(path: ContentPath) -> ContentPath:
    parts = path.parts
    if len(parts) < 2 or parts[0] != 'Maps' or path.suffix != '.bin':
        raise ValueError(f'Not a map file path: {path!r}')
    return path


type MapFilePath = Annotated[ContentPath, AfterValidator(_map_file_path)]


class MapInfo(FrozenModel):
    """One scanned map asset identified only by its virtual content path."""

    file_path: MapFilePath

    if TYPE_CHECKING:

        def __init__(self, *, file_path: str | ContentPath) -> None: ...
