"""Resolve the campaign maps that Everest makes available at runtime.

Everest enumerates physical packages by file name, but a package may defer its
content crawl until one of its manifest entries has its required dependencies.
Later crawls replace an earlier asset with the same virtual path.  This module
models that content-facing view without making the UI reproduce loader rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pist.game import collab, content, dialog, everest, levels, vanilla
from pist.game import mods as game_mods
from pist.game.map_hiders import MapHiderRules
from pist.game.map_source import MapSource

COLLAB_UTILS2 = 'CollabUtils2'


@dataclass(frozen=True, slots=True)
class MapOverride:
    """One map asset replaced by a later Everest content crawl."""

    map_file: content.ContentPath
    previous: levels.LoadedMap
    replacement: levels.LoadedMap


@dataclass(frozen=True, slots=True)
class LoadedCampaign:
    """One campaign directory assembled from active maps across packages."""

    directory: content.ContentPath
    dialog_key: str | None
    levels: tuple[levels.Level, ...]
    source: MapSource = MapSource.MOD
    is_ungrouped: bool = False
    collab_id: str | None = None

    @property
    def map_count(self) -> int:
        """Return the number of concrete Side maps in this Campaign."""
        return sum(len(level.maps_by_side) for level in self.levels)

    def iter_sides(self) -> Iterator[tuple[levels.Level, levels.LevelSide]]:
        """Yield every Level and available Side in stable display order."""
        for level in self.levels:
            for side in level.maps_by_side:
                yield level, side

    @property
    def fallback_name(self) -> str:
        """Return a readable name when no campaign Dialog entry is available."""
        if self.source is MapSource.VANILLA:
            return '原版地图'
        if self.is_ungrouped:
            return '未归类地图'
        return dialog.default_campaign_name(self.directory)

    @property
    def is_collab_lobby_campaign(self) -> bool:
        """Return whether this is one Collab's canonical 0-Lobbies campaign."""
        return self.collab_id is not None and self.directory == content.ContentPath(
            f'Maps/{self.collab_id}/0-Lobbies'
        )


@dataclass(frozen=True, slots=True)
class CollabLobby(levels.Level):
    """A Collab lobby with lazily resolved Campaign projections for each side."""

    campaigns_by_side: dict[levels.LevelSide, tuple[LoadedCampaign, ...]] = field(
        default_factory=dict, compare=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class CampaignCatalog:
    """The active Maps assets and their grouping after Everest-style loading."""

    campaigns: tuple[LoadedCampaign, ...]
    hidden_campaigns: tuple[LoadedCampaign, ...]
    overrides: tuple[MapOverride, ...]
    unloaded_mods: tuple[game_mods.InstalledMod, ...]
    dialogs: Mapping[str, Mapping[str, str]]
    map_hider_diagnostics: tuple[str, ...] = ()


def campaign_names(
    campaign: LoadedCampaign, dialogs: Mapping[str, Mapping[str, str]]
) -> dialog.LocalizedNames:
    """Resolve one campaign's names from the final merged Dialog mapping."""
    if campaign.dialog_key is None:
        return {}
    if campaign.is_collab_lobby_campaign:
        return game_mods.localize_collab_names(campaign.collab_id, dialogs=dialogs)
    return game_mods.localize_dialog_key(campaign.dialog_key, dialogs=dialogs)


def campaign_display_name(
    campaign: LoadedCampaign,
    dialogs: Mapping[str, Mapping[str, str]],
    languages: Iterable[str],
) -> str:
    """Resolve one campaign's user-facing name at display time."""
    return (
        dialog.localized_name(campaign_names(campaign, dialogs), languages)
        or campaign.fallback_name
    )


def load_campaigns(
    mods: Iterable[game_mods.InstalledMod],
    *,
    vanilla_maps: Iterable[levels.LoadedVanillaMap] = (),
    base_dialogs: Mapping[str, Mapping[str, str]] | None = None,
    map_hider_rules: MapHiderRules | None = None,
) -> CampaignCatalog:
    """Resolve active map files in Everest content-crawl order.

    ``mods`` must retain the scanner's physical-package order: ZIP packages
    first, then directories, each ordered by file name.  This deliberately
    models both required and optional dependency delays. Once Everest has
    considered every package, absent optional dependencies no longer block a
    package, while a present-but-incompatible optional dependency still does.

    DLL load failures cannot be determined without loading third-party code,
    so a manifest entry whose applicable dependencies are satisfied is treated
    as loadable.
    """
    mod_list = tuple(mods)
    entries = [(mod, metadata) for mod in mod_list for metadata in mod.manifest]
    loaded_metadata: dict[str, everest.EverestModMetadata] = {}
    content_mods: list[game_mods.InstalledMod] = []
    content_mod_ids: set[int] = set()
    delayed: list[tuple[game_mods.InstalledMod, everest.EverestModMetadata]] = []
    for mod, metadata in entries:
        if _try_load_metadata(
            mod,
            metadata,
            loaded_metadata=loaded_metadata,
            content_mods=content_mods,
            content_mod_ids=content_mod_ids,
            enforce_optional_dependencies=True,
        ):
            delayed = _load_pending(
                delayed,
                loaded_metadata=loaded_metadata,
                content_mods=content_mods,
                content_mod_ids=content_mod_ids,
                enforce_optional_dependencies=True,
            )
        elif metadata.name not in loaded_metadata:
            delayed.append((mod, metadata))
    delayed = _load_pending(
        delayed,
        loaded_metadata=loaded_metadata,
        content_mods=content_mods,
        content_mod_ids=content_mod_ids,
        enforce_optional_dependencies=False,
    )

    dialogs = game_mods.merge_dialogs(content_mods, base_dialogs=base_dialogs)
    active_maps: dict[content.ContentPath, levels.LoadedModMap] = {}
    overrides: list[MapOverride] = []
    for mod in content_mods:
        for map_info in mod.maps:
            loaded_map = levels.LoadedModMap(map_info, mod)
            if (previous := active_maps.get(map_info.file_path)) is not None:
                overrides.append(MapOverride(map_info.file_path, previous, loaded_map))
            active_maps[map_info.file_path] = loaded_map
    map_hiders = None if map_hider_rules is None else map_hider_rules.resolve(loaded_metadata)
    # Everest assigns a physical package's assets to the first manifest entry
    # when it creates ``ModContent``. ``metadata_name`` models that source ID.
    visible_maps: list[levels.LoadedModMap] = []
    hidden_maps: list[levels.LoadedModMap] = []
    for loaded_map in active_maps.values():
        is_hidden = map_hiders is not None and map_hiders.hides(
            source=loaded_map.mod.metadata_name,
            map_file=loaded_map.map_info.file_path,
        )
        (hidden_maps if is_hidden else visible_maps).append(loaded_map)
    collab_utils_loaded = COLLAB_UTILS2 in loaded_metadata
    collab_ids = tuple(
        mod.collab_id for mod in content_mods if collab_utils_loaded and mod.collab_id is not None
    )
    campaigns = _group_campaigns(levels.assemble_mod_levels(tuple(visible_maps)))
    campaigns = _mark_collab_lobbies(campaigns, collab_ids)
    campaigns, hidden_collab_campaigns = _split_collab_campaigns(campaigns)
    hidden_campaigns = _group_campaigns(levels.assemble_mod_levels(tuple(hidden_maps)))
    hidden_campaigns = tuple(
        sorted(
            (*hidden_campaigns, *hidden_collab_campaigns),
            key=lambda campaign: campaign.directory.as_posix().casefold(),
        )
    )
    vanilla_map_tuple = tuple(vanilla_maps)
    if vanilla_map_tuple:
        campaigns = (
            LoadedCampaign(
                directory=content.ContentPath('Maps'),
                dialog_key=None,
                levels=levels.assemble_vanilla_levels(vanilla_map_tuple),
                source=MapSource.VANILLA,
            ),
            *campaigns,
        )
    return CampaignCatalog(
        campaigns=campaigns,
        hidden_campaigns=hidden_campaigns,
        overrides=tuple(overrides),
        unloaded_mods=tuple(mod for mod in mod_list if id(mod) not in content_mod_ids),
        dialogs=dialogs,
        map_hider_diagnostics=() if map_hiders is None else map_hiders.diagnostics,
    )


def _load_pending(
    pending: list[tuple[game_mods.InstalledMod, everest.EverestModMetadata]],
    *,
    loaded_metadata: dict[str, everest.EverestModMetadata],
    content_mods: list[game_mods.InstalledMod],
    content_mod_ids: set[int],
    enforce_optional_dependencies: bool,
) -> list[tuple[game_mods.InstalledMod, everest.EverestModMetadata]]:
    """Load every pending manifest entry that can progress in this phase."""
    while pending:
        next_pending: list[tuple[game_mods.InstalledMod, everest.EverestModMetadata]] = []
        progressed = False
        for mod, metadata in pending:
            if _try_load_metadata(
                mod,
                metadata,
                loaded_metadata=loaded_metadata,
                content_mods=content_mods,
                content_mod_ids=content_mod_ids,
                enforce_optional_dependencies=enforce_optional_dependencies,
            ):
                progressed = True
            elif metadata.name not in loaded_metadata:
                next_pending.append((mod, metadata))
        if not progressed:
            return next_pending
        pending = next_pending
    return []


def _try_load_metadata(
    mod: game_mods.InstalledMod,
    metadata: everest.EverestModMetadata,
    *,
    loaded_metadata: dict[str, everest.EverestModMetadata],
    content_mods: list[game_mods.InstalledMod],
    content_mod_ids: set[int],
    enforce_optional_dependencies: bool,
) -> bool:
    """Load one metadata entry if Everest would not delay it."""
    if metadata.name in loaded_metadata:
        return True
    if not _metadata_dependencies_loaded(
        metadata,
        loaded_metadata,
        enforce_optional_dependencies=enforce_optional_dependencies,
    ):
        return False
    if id(mod) not in content_mod_ids:
        content_mods.append(mod)
        content_mod_ids.add(id(mod))
    loaded_metadata[metadata.name] = metadata
    return True


def _metadata_dependencies_loaded(
    metadata: everest.EverestModMetadata,
    loaded: dict[str, everest.EverestModMetadata],
    *,
    enforce_optional_dependencies: bool,
) -> bool:
    if not _dependencies_loaded(metadata.dependencies, loaded):
        return False
    return all(
        not game_mods.is_mod_dependency(dependency.name)
        or _dependency_is_satisfied(dependency, loaded)
        if enforce_optional_dependencies or dependency.name in loaded
        else True
        for dependency in metadata.optional_dependencies
    )


def _dependencies_loaded(
    dependencies: Iterable[everest.Dependency], loaded: dict[str, everest.EverestModMetadata]
) -> bool:
    return all(
        not game_mods.is_mod_dependency(dependency.name)
        or _dependency_is_satisfied(dependency, loaded)
        for dependency in dependencies
    )


def _dependency_is_satisfied(
    dependency: everest.Dependency, loaded: dict[str, everest.EverestModMetadata]
) -> bool:
    metadata = loaded.get(dependency.name)
    return metadata is not None and (
        metadata.version or everest.DEFAULT_VERSION
    ).is_compatible_with(dependency.version or everest.DEFAULT_VERSION)


def _group_campaigns(level_items: Iterable[levels.Level]) -> tuple[LoadedCampaign, ...]:
    """Group final Levels by their exact Everest Level Set directory."""
    grouped: dict[content.ContentPath, list[levels.Level]] = {}
    ungrouped_levels: list[levels.Level] = []
    for level in level_items:
        directory = dialog.campaign_dir_for_map_file(_level_file_path(level))
        if directory == content.ContentPath('Maps'):
            ungrouped_levels.append(level)
            continue
        grouped.setdefault(directory, []).append(level)
    groups = (
        [
            LoadedCampaign(
                directory=content.ContentPath('Maps'),
                dialog_key=None,
                levels=tuple(ungrouped_levels),
                is_ungrouped=True,
            )
        ]
        if ungrouped_levels
        else []
    )
    groups.extend(
        LoadedCampaign(
            directory=directory,
            dialog_key=dialog.dialog_key_for_campaign_dir(directory),
            levels=tuple(campaign_levels),
        )
        for directory, campaign_levels in sorted(
            grouped.items(), key=lambda item: item[0].as_posix().casefold()
        )
    )
    return tuple(groups)


def _mark_collab_lobbies(
    campaigns: Iterable[LoadedCampaign], collab_ids: tuple[str, ...]
) -> tuple[LoadedCampaign, ...]:
    """Replace Levels matching CollabUtils2's lobby predicate with lobby values."""
    return tuple(_mark_campaign_collab(campaign, collab_ids) for campaign in campaigns)


def _mark_campaign_collab(campaign: LoadedCampaign, collab_ids: tuple[str, ...]) -> LoadedCampaign:
    path = campaign.directory
    collab_id = path.parts[1] if len(path.parts) > 2 and path.parts[1] in collab_ids else None
    return LoadedCampaign(
        directory=campaign.directory,
        dialog_key=campaign.dialog_key,
        levels=tuple(
            CollabLobby(level.sid, level.dialog_key, level.maps_by_side)
            if collab_id is not None and collab.is_lobby(level, (collab_id,))
            else level
            for level in campaign.levels
        ),
        source=campaign.source,
        is_ungrouped=campaign.is_ungrouped,
        collab_id=collab_id,
    )


def _split_collab_campaigns(
    campaigns: Iterable[LoadedCampaign],
) -> tuple[tuple[LoadedCampaign, ...], tuple[LoadedCampaign, ...]]:
    """Keep only each Collab's 0-Lobbies level set in the normal collection."""
    visible_campaigns: list[LoadedCampaign] = []
    hidden_campaigns: list[LoadedCampaign] = []
    for campaign in campaigns:
        if campaign.collab_id is None or campaign.is_collab_lobby_campaign:
            visible_campaigns.append(campaign)
        else:
            hidden_campaigns.append(campaign)
    return tuple(visible_campaigns), tuple(hidden_campaigns)


def resolve_lobby_side(
    lobby: CollabLobby,
    side: levels.LevelSide,
    campaigns: Mapping[str, LoadedCampaign],
    campaign_refs: Iterable[str],
    *,
    reference_name: str = '日志引用',
) -> tuple[tuple[LoadedCampaign, ...], tuple[str, ...]]:
    """Resolve raw journal references against the current exact campaign index."""
    resolved: list[LoadedCampaign] = []
    diagnostics: list[str] = []
    seen: set[str] = set()
    for campaign_ref in campaign_refs:
        directory = f'Maps/{campaign_ref}'
        campaign = campaigns.get(directory)
        if campaign is None:
            diagnostics.append(f'{reference_name}的地图集不存在：{campaign_ref}')
        elif directory not in seen:
            seen.add(directory)
            resolved.append(campaign)
    result = tuple(resolved)
    messages = tuple(diagnostics)
    lobby.campaigns_by_side[side] = result
    return result, messages


def load_vanilla_dialogs(game_dir: Path) -> Mapping[str, Mapping[str, str]]:
    """Read original Dialog entries used as the base of the global merge."""
    return vanilla.load_dialogs(game_dir)


def load_vanilla_maps(game_dir: Path) -> tuple[levels.LoadedVanillaMap, ...]:
    """Read original Game Content maps without treating them as a Mod package."""
    game_content = content.GameContent(game_dir / 'Content')
    return tuple(
        levels.LoadedVanillaMap(map_info, game_content) for map_info in vanilla.load_maps(game_dir)
    )


def _level_file_path(level: levels.Level) -> content.ContentPath:
    return level.maps_by_side[levels.LevelSide.A].info.file_path
