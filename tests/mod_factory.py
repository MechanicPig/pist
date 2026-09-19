"""Factories for physical Mod packages in tests."""

from typing import Literal, NotRequired, TypedDict, Unpack

from pist.game.mods import Dependency, EverestModMetadata, InstalledMod, LocalCampaign, LocalMap


class InstalledModArgs(TypedDict):
    """The physical-package fields unrelated to Everest metadata."""

    source: Literal['zip', 'directory']
    filename: str
    path: str
    collab_id: NotRequired[str | None]
    map_files: NotRequired[list[str]]
    maps: NotRequired[list[LocalMap]]
    campaigns: NotRequired[list[LocalCampaign]]


def make_installed_mod(
    *,
    metadata_name: str,
    metadata_version: str | None,
    dependencies: list[Dependency] | None = None,
    optional_dependencies: list[Dependency] | None = None,
    **kwargs: Unpack[InstalledModArgs],
) -> InstalledMod:
    """Build a one-entry manifest for tests unrelated to manifest parsing."""
    return InstalledMod(
        **kwargs,
        manifest=(
            EverestModMetadata(
                name=metadata_name,
                version=metadata_version,
                dependencies=[] if dependencies is None else dependencies,
                optional_dependencies=[]
                if optional_dependencies is None
                else optional_dependencies,
            ),
        ),
    )
