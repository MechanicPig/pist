"""Resolve the campaign maps that Everest makes available at runtime.

Everest enumerates physical packages by file name, but a package may defer its
content crawl until one of its manifest entries has its required dependencies.
Later crawls replace an earlier asset with the same virtual path.  This module
models that content-facing view without making the UI reproduce loader rules.
"""

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Self

from berries.containers import CaseFoldDict

from . import collab, content, dialog, everest, levels, vanilla
from . import mods as game_mods
from .map_hiders import MapHiderIssue, MapHiderResolution, MapHiderRules
from .maps import Map, ModMap

COLLAB_UTILS2 = 'CollabUtils2'


@dataclass(frozen=True, slots=True)
class DependencyIssue:
    """An unsatisfied dependency and the version actually loaded, if present."""

    dependency: everest.Dependency
    loaded: everest.ModMetadata | None


@dataclass(frozen=True, slots=True)
class ModLoadingIssue:
    """One manifest entry excluded from loading by unresolved dependencies."""

    mod: game_mods.InstalledMod
    metadata: everest.ModMetadata
    required: tuple[DependencyIssue, ...]
    optional: tuple[DependencyIssue, ...]


@dataclass(frozen=True, slots=True)
class ReservedCampaignIssue:
    """Mod maps using the reserved Celeste LevelSet, with original-map SID collisions."""

    campaign: ModCampaign
    conflicting_sids: tuple[str, ...]


type CatalogIssue = ModLoadingIssue | ReservedCampaignIssue | MapHiderIssue


@dataclass(frozen=True, slots=True)
class MapOverride:
    """One map asset replaced by a later Everest content crawl."""

    map_file: content.ContentPath
    previous: Map
    replacement: Map


@dataclass(frozen=True, slots=True)
class Campaign:
    """Shared map grouping and Dialog identity for concrete campaign types."""

    directory: content.ContentPath
    levels: tuple[levels.Level, ...]

    @property
    def dialog_key(self) -> str:
        """Return the Dialog key derived from this LevelSet directory."""
        return dialog.dialog_key_for_campaign_dir(self.directory)

    @property
    def ref(self) -> str:
        """Return this directory in CollabUtils2 journal-reference form."""
        return self.directory.relative_to(content.MAPS_DIR).as_posix()

    @property
    def map_count(self) -> int:
        """Return the number of concrete Side maps in this Campaign."""
        return sum(len(level.maps) for level in self.levels)

    def iter_maps(self) -> Iterator[Map]:
        """Yield every Map in Level order, then A/B/C Side order."""
        for level in self.levels:
            yield from level.maps

    def uses_mod(self, mod: game_mods.InstalledMod) -> bool:
        """Return whether any map in this campaign is supplied by this physical Mod."""
        return any(
            isinstance(map_file, ModMap) and map_file.mod is mod for map_file in self.iter_maps()
        )

    def iter_sides(self) -> Iterator[tuple[levels.Level, levels.LevelSide]]:
        """Yield every Level and available Side in stable display order."""
        for level in self.levels:
            for side in level.sides:
                yield level, side


@dataclass(frozen=True, slots=True)
class ModCampaign(Campaign):
    """One Mod LevelSet assembled from active maps across physical packages."""

    @property
    def uses_vanilla_campaign(self) -> bool:
        """Return whether Mod maps claim Everest's reserved ``Celeste`` LevelSet."""
        return self.directory == content.MAPS_DIR / 'Celeste'


@dataclass(frozen=True, slots=True)
class VanillaCampaign(Campaign):
    """The original game's campaign, distinct from Mod LevelSets."""

    @property
    def dialog_key(self) -> str:
        """Return the original game's campaign key, independent of its root directory."""
        return 'Celeste'


@dataclass(frozen=True, slots=True, kw_only=True)
class CollabSubCampaign(ModCampaign):
    """A Collab LevelSet other than its hub, hidden from ordinary map selection."""

    collab_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CollabHubCampaign(ModCampaign):
    """A Collab's canonical 0-Lobbies campaign, displayed under its collection title."""

    collab_id: str


@dataclass(frozen=True, slots=True)
class CampaignCatalog:
    """The active Maps assets and their grouping after one Everest-style load.

    This result does not track file changes. Its containers are not recursively
    immutable; load a new catalog to resolve a changed installation.
    """

    campaigns: tuple[Campaign, ...]
    hidden_campaigns: tuple[Campaign, ...]
    overrides: tuple[MapOverride, ...]
    unloaded_mods: tuple[game_mods.InstalledMod, ...]
    dialogs: dict[str, CaseFoldDict[str]]
    issues: tuple[CatalogIssue, ...] = ()

    @classmethod
    def from_game_dir(
        cls,
        game_dir: Path,
        *,
        scan_report: game_mods.ModScanReport | None = None,
        map_hider_rules: MapHiderRules | None = None,
        on_error: Callable[[dialog.DialogReadError], None] | None = None,
        on_scan_error: Callable[[game_mods.ModScanError], None] | None = None,
    ) -> Self:
        """Load original and enabled Mod content, retaining structured loading issues.

        An existing report may be reused after a progress-aware scan of the same
        installation. Packaged hiding rules read only the game's Helper settings;
        callers may explicitly supply other rule declarations. Dialog read failures
        propagate unless on_error handles them and allows partial results.
        """
        if scan_report is None:
            if not game_dir.is_dir():
                raise ValueError(f'Game directory does not exist: {game_dir!r}')
            mods_dir = game_dir / game_mods.MODS_DIRNAME
            scan_report = (
                game_mods.ModScanner(game_dir, on_error=on_scan_error).scan()
                if mods_dir.exists()
                else game_mods.ModScanReport(mods_dir=str(mods_dir), disabled_filenames=[], mods=[])
            )
        elif Path(scan_report.mods_dir).resolve() != (game_dir / 'Mods').resolve():
            raise ValueError('Mod scan report belongs to a different game directory.')
        base_dialogs = vanilla.load_dialogs(game_dir, on_error=on_error)
        return cls._from_inputs(
            scan_report.enabled_mods,
            vanilla_maps=vanilla.load_maps(game_dir),
            base_dialogs=base_dialogs,
            map_hider_rules=map_hider_rules or MapHiderRules(game_dir),
        )

    @classmethod
    def _from_inputs(
        cls,
        mods: Sequence[game_mods.InstalledMod],
        *,
        vanilla_maps: Iterable[Map] = (),
        base_dialogs: Mapping[str, Mapping[str, str]] | None = None,
        map_hider_rules: MapHiderRules | None = None,
    ) -> Self:
        """Assemble a catalog from read inputs without performing a second scan."""
        loaded = _resolve_mod_loading(mods)
        merged = _merge_mod_content(loaded.mods, base_dialogs)
        hiders = None if map_hider_rules is None else map_hider_rules.resolve(loaded.metadata)
        visible_maps, hidden_maps = _split_hidden_maps(merged, hiders)
        collab_ids = frozenset(
            mod.collab_id
            for mod in loaded.mods
            if COLLAB_UTILS2 in loaded.metadata and mod.collab_id is not None
        )
        camps, hidden_camps = _assemble_campaign_groups(visible_maps, hidden_maps, collab_ids)
        vanilla_levels = levels.assemble_vanilla_levels(vanilla_maps)
        issues: list[CatalogIssue] = list(loaded.issues)
        if hiders is not None:
            issues.extend(hiders.issues)
        if (issue := _reserved_campaign_issue(camps, vanilla_levels)) is not None:
            issues.append(issue)
        if vanilla_levels:
            camps = (
                VanillaCampaign(content.MAPS_DIR, vanilla_levels),
                *camps,
            )
        return cls(
            campaigns=camps,
            hidden_campaigns=hidden_camps,
            overrides=merged.overrides,
            unloaded_mods=tuple(mod for mod in mods if mod.path not in merged.mods_by_path),
            dialogs=merged.dialogs,
            issues=tuple(issues),
        )


@dataclass(frozen=True, slots=True)
class _LoadedModContent:
    mods: tuple[game_mods.InstalledMod, ...]
    metadata: Mapping[str, everest.ModMetadata]
    issues: tuple[ModLoadingIssue, ...]


@dataclass(frozen=True, slots=True)
class _MergedModContent:
    maps: tuple[ModMap, ...]
    overrides: tuple[MapOverride, ...]
    dialogs: dict[str, CaseFoldDict[str]]
    mods_by_path: Mapping[Path, game_mods.InstalledMod]


def _resolve_mod_loading(mods: Sequence[game_mods.InstalledMod]) -> _LoadedModContent:
    """Resolve Mod dependencies and content-crawl order.

    ``mods`` must retain the scanner's physical-package order: ZIP packages
    first, then directories, each ordered by file name.  This deliberately
    models both required and optional dependency delays. Once Everest has
    considered every package, absent optional dependencies no longer block a
    package, while a present-but-incompatible optional dependency still does.

    DLL load failures cannot be determined without loading third-party code,
    so a manifest entry whose applicable dependencies are satisfied is treated
    as loadable.
    """
    entries = [(mod, metadata) for mod in mods for metadata in mod.manifest]
    loaded_metadata: dict[str, everest.ModMetadata] = {}
    loaded_mods: list[game_mods.InstalledMod] = []
    loaded_mod_paths: set[Path] = set()
    delayed: list[tuple[game_mods.InstalledMod, everest.ModMetadata]] = []
    for mod, metadata in entries:
        if _try_load_metadata(
            mod,
            metadata,
            loaded_metadata=loaded_metadata,
            loaded_mods=loaded_mods,
            loaded_mod_paths=loaded_mod_paths,
            enforce_optional_dependencies=True,
        ):
            delayed = _load_pending(
                delayed,
                loaded_metadata=loaded_metadata,
                loaded_mods=loaded_mods,
                loaded_mod_paths=loaded_mod_paths,
                enforce_optional_dependencies=True,
            )
        elif metadata.name not in loaded_metadata:
            delayed.append((mod, metadata))
    delayed = _load_pending(
        delayed,
        loaded_metadata=loaded_metadata,
        loaded_mods=loaded_mods,
        loaded_mod_paths=loaded_mod_paths,
        enforce_optional_dependencies=False,
    )
    issues = tuple(
        _unloaded_metadata_issue(mod, metadata, loaded_metadata) for mod, metadata in delayed
    )
    return _LoadedModContent(tuple(loaded_mods), loaded_metadata, issues)


def _merge_mod_content(
    mods: Sequence[game_mods.InstalledMod],
    base_dialogs: Mapping[str, Mapping[str, str]] | None,
) -> _MergedModContent:
    """Merge Dialog and replace map assets in resolved content-crawl order."""
    dialogs = dialog.merge_dialogs((mod.dialogs for mod in mods), base_dialogs=base_dialogs)
    mods_by_path = {mod.path: mod for mod in mods}
    active_maps: dict[content.ContentPath, ModMap] = {}
    overrides: list[MapOverride] = []
    for mod in mods:
        for path in mod.map_files:
            map_file = ModMap(path, mod)
            if (previous := active_maps.get(path)) is not None:
                overrides.append(MapOverride(path, previous, map_file))
            active_maps[path] = map_file
    return _MergedModContent(tuple(active_maps.values()), tuple(overrides), dialogs, mods_by_path)


def _split_hidden_maps(
    merged: _MergedModContent, map_hiders: MapHiderResolution | None
) -> tuple[tuple[Map, ...], tuple[Map, ...]]:
    """Separate visible and hidden assets using only confirmed Helper rules."""
    # Everest assigns a physical package's assets to the first manifest entry
    # when it creates ``ModContent``. ``metadata_name`` models that source ID.
    visible_maps: list[Map] = []
    hidden_maps: list[Map] = []
    for map_file in merged.maps:
        mod = map_file.mod
        is_hidden = map_hiders is not None and map_hiders.hides(
            source=mod.metadata_name,
            map_file=map_file.file_path,
        )
        if is_hidden:
            hidden_maps.append(map_file)
        else:
            visible_maps.append(map_file)
    return tuple(visible_maps), tuple(hidden_maps)


def _assemble_campaign_groups(
    visible_maps: Collection[Map],
    hidden_maps: Collection[Map],
    collab_ids: AbstractSet[str],
) -> tuple[tuple[Campaign, ...], tuple[Campaign, ...]]:
    """Group Mod maps and separate Collab subcampaigns from their lobby collection."""
    campaigns = _group_campaigns(levels.assemble_mod_levels(visible_maps))
    campaigns = _mark_lobby_maps(campaigns, collab_ids)
    campaigns, hidden_collab_campaigns = _split_collab_campaigns(campaigns)
    hidden_campaigns = _group_campaigns(levels.assemble_mod_levels(hidden_maps))
    hidden_campaigns = tuple(
        sorted(
            (*hidden_campaigns, *hidden_collab_campaigns),
            key=lambda campaign: campaign.directory.as_posix().casefold(),
        )
    )
    return campaigns, hidden_campaigns


def _reserved_campaign_issue(
    campaigns: Iterable[Campaign],
    vanilla_levels: Iterable[levels.Level],
) -> ReservedCampaignIssue | None:
    """Identify Mod maps using the reserved vanilla LevelSet and colliding SIDs."""
    campaign = next(
        (
            item
            for item in campaigns
            if isinstance(item, ModCampaign) and item.uses_vanilla_campaign
        ),
        None,
    )
    if campaign is None:
        return None
    vanilla_sids = {level.sid for level in vanilla_levels}
    conflicting_sids = sorted(
        {level.sid for level in campaign.levels} & vanilla_sids,
        key=str.casefold,
    )
    return ReservedCampaignIssue(campaign, tuple(conflicting_sids))


def _load_pending(
    pending: list[tuple[game_mods.InstalledMod, everest.ModMetadata]],
    *,
    loaded_metadata: dict[str, everest.ModMetadata],
    loaded_mods: list[game_mods.InstalledMod],
    loaded_mod_paths: set[Path],
    enforce_optional_dependencies: bool,
) -> list[tuple[game_mods.InstalledMod, everest.ModMetadata]]:
    """Load every pending manifest entry that can progress in this phase."""
    while pending:
        next_pending: list[tuple[game_mods.InstalledMod, everest.ModMetadata]] = []
        progressed = False
        for mod, metadata in pending:
            if _try_load_metadata(
                mod,
                metadata,
                loaded_metadata=loaded_metadata,
                loaded_mods=loaded_mods,
                loaded_mod_paths=loaded_mod_paths,
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
    metadata: everest.ModMetadata,
    *,
    loaded_metadata: dict[str, everest.ModMetadata],
    loaded_mods: list[game_mods.InstalledMod],
    loaded_mod_paths: set[Path],
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
    if mod.path not in loaded_mod_paths:
        loaded_mods.append(mod)
        loaded_mod_paths.add(mod.path)
    loaded_metadata[metadata.name] = metadata
    return True


def _metadata_dependencies_loaded(
    metadata: everest.ModMetadata,
    loaded: Mapping[str, everest.ModMetadata],
    *,
    enforce_optional_dependencies: bool,
) -> bool:
    if not _dependencies_loaded(metadata.dependencies, loaded):
        return False
    return all(
        not enforce_optional_dependencies
        and dependency.name not in loaded
        or not game_mods.is_mod_dependency(dependency.name)
        or _dependency_is_satisfied(dependency, loaded)
        for dependency in metadata.optional_dependencies
    )


def _dependencies_loaded(
    dependencies: Iterable[everest.Dependency], loaded: Mapping[str, everest.ModMetadata]
) -> bool:
    return all(
        not game_mods.is_mod_dependency(dependency.name)
        or _dependency_is_satisfied(dependency, loaded)
        for dependency in dependencies
    )


def _dependency_is_satisfied(
    dependency: everest.Dependency, loaded: Mapping[str, everest.ModMetadata]
) -> bool:
    metadata = loaded.get(dependency.name)
    return metadata is not None and (
        metadata.version or everest.DEFAULT_VERSION
    ).is_compatible_with(dependency.version or everest.DEFAULT_VERSION)


def _unloaded_metadata_issue(
    mod: game_mods.InstalledMod,
    metadata: everest.ModMetadata,
    loaded: Mapping[str, everest.ModMetadata],
) -> ModLoadingIssue:
    """Describe why one manifest entry remained outside the loaded Mod graph."""
    required = tuple(
        DependencyIssue(dependency, loaded.get(dependency.name))
        for dependency in metadata.dependencies
        if game_mods.is_mod_dependency(dependency.name)
        and not _dependency_is_satisfied(dependency, loaded)
    )
    optional = tuple(
        DependencyIssue(dependency, loaded.get(dependency.name))
        for dependency in metadata.optional_dependencies
        if game_mods.is_mod_dependency(dependency.name)
        and dependency.name in loaded
        and not _dependency_is_satisfied(dependency, loaded)
    )
    return ModLoadingIssue(mod, metadata, required, optional)


def _group_campaigns(level_items: Iterable[levels.Level]) -> tuple[Campaign, ...]:
    """Group final Levels by their exact Everest Level Set directory."""
    grouped: dict[content.ContentPath, list[levels.Level]] = {}
    for level in level_items:
        directory = level[levels.LevelSide.A].campaign_dir
        grouped.setdefault(directory, []).append(level)
    return tuple(
        ModCampaign(
            directory=directory,
            levels=tuple(campaign_levels),
        )
        for directory, campaign_levels in sorted(
            grouped.items(), key=lambda item: item[0].as_posix().casefold()
        )
    )


def _mark_lobby_maps(
    campaigns: Iterable[Campaign], collab_ids: AbstractSet[str]
) -> tuple[Campaign, ...]:
    """Mark active maps whose assembled SID matches CollabUtils2's lobby predicate."""
    return tuple(_mark_campaign_lobby_maps(campaign, collab_ids) for campaign in campaigns)


def _mark_campaign_lobby_maps(campaign: Campaign, collab_ids: AbstractSet[str]) -> Campaign:
    path = campaign.directory
    collab_id = path.parts[1] if len(path.parts) > 2 and path.parts[1] in collab_ids else None
    if collab_id is None:
        return campaign
    campaign_type = (
        CollabHubCampaign
        if path == content.MAPS_DIR / collab_id / '0-Lobbies'
        else CollabSubCampaign
    )
    return campaign_type(
        directory=campaign.directory,
        levels=tuple(
            replace(level, is_lobby=True) if collab.is_lobby_sid(level.sid, (collab_id,)) else level
            for level in campaign.levels
        ),
        collab_id=collab_id,
    )


def _split_collab_campaigns(
    campaigns: Iterable[Campaign],
) -> tuple[tuple[Campaign, ...], tuple[Campaign, ...]]:
    """Keep only each Collab's 0-Lobbies level set in the normal collection."""
    visible_campaigns: list[Campaign] = []
    hidden_campaigns: list[Campaign] = []
    for campaign in campaigns:
        if isinstance(campaign, CollabSubCampaign):
            hidden_campaigns.append(campaign)
        else:
            visible_campaigns.append(campaign)
    return tuple(visible_campaigns), tuple(hidden_campaigns)


def resolve_campaign_refs(
    campaigns: Mapping[str, Campaign],
    campaign_refs: Iterable[str],
) -> tuple[tuple[Campaign, ...], tuple[str, ...]]:
    """Resolve exact references, returning matched campaigns and missing raw references."""
    resolved: list[Campaign] = []
    missing: list[str] = []
    seen: set[str] = set()
    for campaign_ref in campaign_refs:
        directory = f'{content.MAPS_DIR}/{campaign_ref}'
        campaign = campaigns.get(directory)
        if campaign is None:
            missing.append(campaign_ref)
        elif directory not in seen:
            seen.add(directory)
            resolved.append(campaign)
    return tuple(resolved), tuple(missing)
