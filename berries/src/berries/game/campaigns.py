"""Resolve the campaign maps that Everest makes available at runtime.

Everest enumerates physical packages by file name, but a package may defer its
content crawl until one of its manifest entries has its required dependencies.
Later crawls replace an earlier asset with the same virtual path.  This module
models that content-facing view without making the UI reproduce loader rules.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from pathlib import Path

from berries.containers import CaseFoldDict
from berries.game import collab, content, dialog, everest, levels, maps, vanilla
from berries.game import mods as game_mods
from berries.game.map_hiders import MapHiderRules
from berries.game.map_source import MapSource

COLLAB_UTILS2 = 'CollabUtils2'


@dataclass(frozen=True, slots=True)
class MapOverride:
    """One map asset replaced by a later Everest content crawl."""

    map_file: content.ContentPath
    previous: levels.Map
    replacement: levels.Map


@dataclass(frozen=True, slots=True)
class Campaign:
    """One campaign directory assembled from active maps across packages."""

    directory: content.ContentPath
    dialog_key: str
    levels: tuple[levels.Level, ...]
    source: MapSource = MapSource.MOD
    collab_id: str | None = None

    @property
    def map_count(self) -> int:
        """Return the number of concrete Side maps in this Campaign."""
        return sum(len(level.maps) for level in self.levels)

    def iter_maps(self) -> Iterator[levels.Map]:
        """Yield every Map in Level order, then A/B/C Side order."""
        for level in self.levels:
            yield from level.maps

    def iter_sides(self) -> Iterator[tuple[levels.Level, levels.LevelSide]]:
        """Yield every Level and available Side in stable display order."""
        for level in self.levels:
            for side in level.sides:
                yield level, side

    @property
    def fallback_name(self) -> str:
        """Return a readable name when no campaign Dialog entry is available."""
        if self.source is MapSource.VANILLA:
            return '官图'
        if self.directory == content.MAPS_DIR:
            return '未分类'
        return dialog.default_campaign_name(self.directory)

    @property
    def is_collab_lobby_campaign(self) -> bool:
        """Return whether this is one Collab's canonical 0-Lobbies campaign."""
        return self.collab_id is not None and self.directory == (
            content.MAPS_DIR / self.collab_id / '0-Lobbies'
        )

    @property
    def uses_vanilla_campaign(self) -> bool:
        """Return whether Mod maps claim Everest's reserved ``Celeste`` LevelSet."""
        return self.source is MapSource.MOD and self.directory == content.MAPS_DIR / 'Celeste'

    def localized_names(self, dialogs: Mapping[str, Mapping[str, str]]) -> dialog.LocalizedNames:
        """Resolve this campaign's names from the final merged Dialog mapping."""
        if self.is_collab_lobby_campaign:
            return dialog.localize_collab_names(self.collab_id, dialogs=dialogs)
        return dialog.localize_campaign_key(self.dialog_key, dialogs=dialogs)

    def display_name(
        self,
        dialogs: Mapping[str, Mapping[str, str]],
        languages: Iterable[str],
    ) -> str:
        """Resolve this campaign's user-facing name at display time."""
        name = dialog.localized_name(self.localized_names(dialogs), languages) or self.fallback_name
        return f'{name}（Mod）' if self.uses_vanilla_campaign else name


@dataclass(frozen=True, slots=True)
class CampaignCatalog:
    """The active Maps assets and their grouping after Everest-style loading."""

    campaigns: tuple[Campaign, ...]
    hidden_campaigns: tuple[Campaign, ...]
    overrides: tuple[MapOverride, ...]
    unloaded_mods: tuple[game_mods.InstalledMod, ...]
    dialogs: dict[str, CaseFoldDict[str]]
    mods_by_content_path: Mapping[Path, game_mods.InstalledMod]
    diagnostics: tuple[str, ...] = ()

    def mod_for(self, map_file: levels.Map) -> game_mods.InstalledMod | None:
        """Return the Mod owning one map file in this catalog snapshot, if any."""
        return self.mods_by_content_path.get(map_file.content.path)

    def source_name_for(self, map_file: levels.Map) -> str:
        """Return the user-facing content source name for one map file."""
        mod = self.mod_for(map_file)
        return '原版' if mod is None else mod.metadata_name

    def campaign_uses_mod(self, campaign: Campaign, mod: game_mods.InstalledMod) -> bool:
        """Return whether any map in a campaign is supplied by one physical Mod."""
        return any(self.mod_for(map_file) is mod for map_file in campaign.iter_maps())


def load_campaigns(
    mods: Sequence[game_mods.InstalledMod],
    *,
    vanilla_maps: Iterable[levels.Map] = (),
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
    entries = [(mod, metadata) for mod in mods for metadata in mod.manifest]
    loaded_metadata: dict[str, everest.ModMetadata] = {}
    content_mods: list[game_mods.InstalledMod] = []
    content_mod_paths: set[Path] = set()
    delayed: list[tuple[game_mods.InstalledMod, everest.ModMetadata]] = []
    for mod, metadata in entries:
        if _try_load_metadata(
            mod,
            metadata,
            loaded_metadata=loaded_metadata,
            content_mods=content_mods,
            content_mod_paths=content_mod_paths,
            enforce_optional_dependencies=True,
        ):
            delayed = _load_pending(
                delayed,
                loaded_metadata=loaded_metadata,
                content_mods=content_mods,
                content_mod_paths=content_mod_paths,
                enforce_optional_dependencies=True,
            )
        elif metadata.name not in loaded_metadata:
            delayed.append((mod, metadata))
    delayed = _load_pending(
        delayed,
        loaded_metadata=loaded_metadata,
        content_mods=content_mods,
        content_mod_paths=content_mod_paths,
        enforce_optional_dependencies=False,
    )
    dependency_diagnostics = tuple(
        _unloaded_metadata_diagnostic(mod, metadata, loaded_metadata) for mod, metadata in delayed
    )

    dialogs = dialog.merge_dialogs((mod.dialogs for mod in content_mods), base_dialogs=base_dialogs)
    mods_by_content_path = {mod.path: mod for mod in content_mods}
    active_maps: dict[content.ContentPath, levels.Map] = {}
    overrides: list[MapOverride] = []
    for mod in content_mods:
        for map_info in mod.maps:
            map_file = levels.Map(map_info, mod)
            if (previous := active_maps.get(map_info.file_path)) is not None:
                overrides.append(MapOverride(map_info.file_path, previous, map_file))
            active_maps[map_info.file_path] = map_file
    map_hiders = None if map_hider_rules is None else map_hider_rules.resolve(loaded_metadata)
    # Everest assigns a physical package's assets to the first manifest entry
    # when it creates ``ModContent``. ``metadata_name`` models that source ID.
    visible_maps: list[levels.Map] = []
    hidden_maps: list[levels.Map] = []
    for map_file in active_maps.values():
        mod = mods_by_content_path[map_file.content.path]
        is_hidden = map_hiders is not None and map_hiders.hides(
            source=mod.metadata_name,
            map_file=map_file.file_path,
        )
        if is_hidden:
            hidden_maps.append(map_file)
        else:
            visible_maps.append(map_file)
    collab_utils_loaded = COLLAB_UTILS2 in loaded_metadata
    collab_ids = frozenset(
        mod.collab_id for mod in content_mods if collab_utils_loaded and mod.collab_id is not None
    )
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
    vanilla_levels = levels.assemble_vanilla_levels(vanilla_maps)
    diagnostics = list(dependency_diagnostics)
    if map_hiders is not None:
        diagnostics.extend(map_hiders.diagnostics)
    if (
        diagnostic := _reserved_campaign_diagnostic(campaigns, vanilla_levels, mods_by_content_path)
    ) is not None:
        diagnostics.append(diagnostic)
    if vanilla_levels:
        campaigns = (
            Campaign(
                directory=content.MAPS_DIR,
                dialog_key='Celeste',
                levels=vanilla_levels,
                source=MapSource.VANILLA,
            ),
            *campaigns,
        )
    return CampaignCatalog(
        campaigns=campaigns,
        hidden_campaigns=hidden_campaigns,
        overrides=tuple(overrides),
        unloaded_mods=tuple(mod for mod in mods if mod.path not in content_mod_paths),
        dialogs=dialogs,
        mods_by_content_path=mods_by_content_path,
        diagnostics=tuple(diagnostics),
    )


def _reserved_campaign_diagnostic(
    campaigns: Iterable[Campaign],
    vanilla_levels: Iterable[levels.Level],
    mods_by_content_path: Mapping[Path, game_mods.InstalledMod],
) -> str | None:
    """Warn about Mod maps that Everest assigns to its reserved vanilla LevelSet."""
    campaign = next((item for item in campaigns if item.uses_vanilla_campaign), None)
    if campaign is None:
        return None
    sources = '、'.join(
        dict.fromkeys(
            mods_by_content_path[map_file.content.path].metadata_name
            for map_file in campaign.iter_maps()
        )
    )
    vanilla_sids = {level.sid for level in vanilla_levels}
    conflicting_sids = sorted(
        {level.sid for level in campaign.levels} & vanilla_sids,
        key=str.casefold,
    )
    conflict = (
        f'；与官图 SID 冲突并会替换官图定义：{"、".join(conflicting_sids)}'
        if conflicting_sids
        else ''
    )
    message = 'Mod 地图集 Maps/Celeste 使用 Everest 保留的 Celeste LevelSet'
    message += f'（来源：{sources}）；游戏会将其视为官图并使用官图存档{conflict}。'
    return message


def _load_pending(
    pending: list[tuple[game_mods.InstalledMod, everest.ModMetadata]],
    *,
    loaded_metadata: dict[str, everest.ModMetadata],
    content_mods: list[game_mods.InstalledMod],
    content_mod_paths: set[Path],
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
                content_mods=content_mods,
                content_mod_paths=content_mod_paths,
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
    content_mods: list[game_mods.InstalledMod],
    content_mod_paths: set[Path],
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
    if mod.path not in content_mod_paths:
        content_mods.append(mod)
        content_mod_paths.add(mod.path)
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


def _unloaded_metadata_diagnostic(
    mod: game_mods.InstalledMod,
    metadata: everest.ModMetadata,
    loaded: Mapping[str, everest.ModMetadata],
) -> str:
    """Describe why one manifest entry remained outside the loaded Mod graph."""
    required = tuple(
        dependency
        for dependency in metadata.dependencies
        if game_mods.is_mod_dependency(dependency.name)
        and not _dependency_is_satisfied(dependency, loaded)
    )
    optional = tuple(
        dependency
        for dependency in metadata.optional_dependencies
        if game_mods.is_mod_dependency(dependency.name)
        and dependency.name in loaded
        and not _dependency_is_satisfied(dependency, loaded)
    )
    problems: list[str] = []
    if required:
        problems.append(f'必需依赖：{_dependency_diagnostic_list(required, loaded)}')
    if optional:
        problems.append(f'可选依赖版本不兼容：{_dependency_diagnostic_list(optional, loaded)}')
    reason = '；'.join(problems) or '依赖关系无法解析'
    return f'Mod 元数据 {metadata.name}（{mod.filename}）未加载：{reason}'


def _dependency_diagnostic_list(
    dependencies: Iterable[everest.Dependency],
    loaded: Mapping[str, everest.ModMetadata],
) -> str:
    """Format unresolved dependencies without losing installed-version context."""
    entries: list[str] = []
    for dependency in dependencies:
        required = dependency.version or everest.DEFAULT_VERSION
        installed_metadata = loaded.get(dependency.name)
        if installed_metadata is None:
            entries.append(f'{dependency.name}（未加载，需要 {required}）')
        else:
            installed = installed_metadata.version or everest.DEFAULT_VERSION
            entries.append(f'{dependency.name} {installed}（需要 {required}）')
    return '、'.join(entries)


def _group_campaigns(level_items: Iterable[levels.Level]) -> tuple[Campaign, ...]:
    """Group final Levels by their exact Everest Level Set directory."""
    grouped: dict[content.ContentPath, list[levels.Level]] = {}
    for level in level_items:
        directory = maps.campaign_dir_for_map_file(_level_file_path(level))
        grouped.setdefault(directory, []).append(level)
    return tuple(
        Campaign(
            directory=directory,
            dialog_key=dialog.dialog_key_for_campaign_dir(directory),
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
    return replace(
        campaign,
        levels=tuple(
            replace(level, is_lobby=True)
            if collab_id is not None and collab.is_lobby_sid(level.sid, (collab_id,))
            else level
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
        if campaign.collab_id is None or campaign.is_collab_lobby_campaign:
            visible_campaigns.append(campaign)
        else:
            hidden_campaigns.append(campaign)
    return tuple(visible_campaigns), tuple(hidden_campaigns)


def resolve_campaign_refs(
    campaigns: Mapping[str, Campaign],
    campaign_refs: Iterable[str],
    *,
    reference_name: str = '日志引用',
) -> tuple[tuple[Campaign, ...], tuple[str, ...]]:
    """Resolve raw journal or configuration references against the exact campaign index."""
    resolved: list[Campaign] = []
    diagnostics: list[str] = []
    seen: set[str] = set()
    for campaign_ref in campaign_refs:
        directory = f'{content.MAPS_DIR}/{campaign_ref}'
        campaign = campaigns.get(directory)
        if campaign is None:
            diagnostics.append(f'{reference_name}的地图集不存在：{campaign_ref}')
        elif directory not in seen:
            seen.add(directory)
            resolved.append(campaign)
    return tuple(resolved), tuple(diagnostics)


def load_vanilla_dialogs(game_dir: Path) -> dict[str, CaseFoldDict[str]]:
    """Read original Dialog entries used as the base of the global merge."""
    return vanilla.load_dialogs(game_dir)


def load_vanilla_maps(game_dir: Path) -> tuple[levels.Map, ...]:
    """Read original Game Content maps without treating them as a Mod package."""
    game_content = content.GameContent(game_dir / content.CONTENT_DIRNAME)
    return tuple(levels.Map(map_info, game_content) for map_info in vanilla.load_maps(game_dir))


def _level_file_path(level: levels.Level) -> content.ContentPath:
    return level[levels.LevelSide.A].file_path
