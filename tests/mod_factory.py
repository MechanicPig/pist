"""Factories for physical Mod packages in tests."""

from pathlib import Path
from typing import Literal, NotRequired, TypedDict, Unpack

from pist.game.everest import Dependency, EverestModMetadata, Version
from pist.game.maps import MapInfo
from pist.game.mods import DirMod, ScannedMod, ZipMod


class InstalledModArgs(TypedDict):
    """The physical-package fields unrelated to Everest metadata."""

    source: Literal['zip', 'directory']
    filename: str
    path: str | Path
    collab_id: NotRequired[str | None]
    dialogs: NotRequired[dict[str, dict[str, str]]]
    maps: NotRequired[list[MapInfo]]


def make_installed_mod(
    *,
    metadata_name: str,
    metadata_version: str | None,
    dependencies: list[Dependency] | None = None,
    optional_dependencies: list[Dependency] | None = None,
    **kwargs: Unpack[InstalledModArgs],
) -> ScannedMod:
    """Build a one-entry manifest for tests unrelated to manifest parsing."""
    source = kwargs.pop('source')
    kwargs['path'] = Path(kwargs['path'])
    fields = dict(kwargs)
    fields['manifest'] = (
        EverestModMetadata(
            name=metadata_name,
            version=None if metadata_version is None else Version.parse(metadata_version),
            dependencies=[] if dependencies is None else dependencies,
            optional_dependencies=[] if optional_dependencies is None else optional_dependencies,
        ),
    )
    if source == 'zip':
        return ZipMod.model_validate(fields)
    return DirMod.model_validate(fields)
