"""Factories for physical Mod packages in tests."""

from pathlib import Path
from typing import Literal, NotRequired, TypedDict, Unpack

from berries.game.content import ContentPath
from berries.game.everest import Dependency, ModMetadata, Version
from berries.game.mods import DirMod, InstalledMod, ZipMod


class InstalledModArgs(TypedDict):
    """The physical-package fields unrelated to Everest metadata."""

    source: Literal['zip', 'directory']
    filename: str
    path: str | Path
    collab_id: NotRequired[str | None]
    dialogs: NotRequired[dict[str, dict[str, str]]]
    maps: NotRequired[list[ContentPath]]


def make_installed_mod(
    *,
    metadata_name: str,
    metadata_version: str | None,
    dependencies: list[Dependency] | None = None,
    optional_dependencies: list[Dependency] | None = None,
    **kwargs: Unpack[InstalledModArgs],
) -> InstalledMod:
    """Build a one-entry manifest for tests unrelated to manifest parsing."""
    source = kwargs.pop('source')
    map_files = kwargs.pop('maps', [])
    kwargs['path'] = Path(kwargs['path'])
    fields = dict(kwargs)
    fields['map_files'] = map_files
    fields['manifest'] = (
        ModMetadata(
            name=metadata_name,
            version=None if metadata_version is None else Version.parse(metadata_version),
            dependencies=() if dependencies is None else tuple(dependencies),
            optional_dependencies=()
            if optional_dependencies is None
            else tuple(optional_dependencies),
        ),
    )
    if source == 'zip':
        return ZipMod.model_validate(fields)
    return DirMod.model_validate(fields)
