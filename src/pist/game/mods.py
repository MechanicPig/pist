"""Offline discovery of enabled Mods."""

import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import BadZipFile, ZipFile

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pist.game.binmap import BadMapBin, parse_map_meta
from pist.game.dialog import (
    LocalizedNames,
    campaign_dir_for_map_file,
    default_campaign_name,
    default_map_name,
    dialog_key_for_campaign_dir,
    dialog_key_for_map_file,
    map_base_file_and_side,
    parse_dialog,
)
from pist.game.mod_path import BadModPath, BadZipModPath, ModPath, iter_files

COLLAB_ID_FILENAME = 'CollabUtils2CollabID.txt'
MODS_DIRNAME = 'Mods'
BLACKLIST_FILENAME = 'blacklist.txt'
MANIFEST_FILENAME = 'everest.yaml'
MAPS_DIRNAME = 'Maps'
DIALOG_DIRNAME = 'Dialog'
DIALOG_FILENAMES = {
    'pt-br': 'Brazilian Portuguese.txt',
    'en': 'English.txt',
    'fr': 'French.txt',
    'de': 'German.txt',
    'it': 'Italian.txt',
    'ja': 'Japanese.txt',
    'ko': 'Korean.txt',
    'ru': 'Russian.txt',
    'zh-cn': 'Simplified Chinese.txt',
    'es': 'Spanish.txt',
}
IGNORED_DEPENDENCY_NAMES = frozenset({'celeste', 'everest', 'everestcore'})
MAP_SIDE_ORDER = {None: 0, 'B': 1, 'C': 2}
NATURAL_PART_PATTERN = re.compile(r'(\d+)')
COLLAB_JOURNAL_ICON_PATTERN = re.compile(r'.*/\d+-.*')


class Dependency(BaseModel):
    """One required or optional Everest Mod dependency."""

    model_config = ConfigDict(extra='ignore', populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: str | None = Field(default=None, validation_alias='Version')


class EverestManifest(BaseModel):
    """The first package entry in an Everest ``everest.yaml`` manifest."""

    model_config = ConfigDict(extra='ignore', populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: str | None = Field(default=None, validation_alias='Version')
    dependencies: list[Dependency] = Field(default_factory=list, validation_alias='Dependencies')
    optional_dependencies: list[Dependency] = Field(
        default_factory=list, validation_alias='OptionalDependencies'
    )


def _base_file_from_data(data: dict[str, object]) -> str:
    """Build a map's base file when callers do not already provide one."""
    file_path = data['file_path']
    assert isinstance(file_path, str)
    return map_base_file_and_side(file_path)[0]


class LocalMap(BaseModel):
    """One map file and its localized display-name candidates."""

    file_path: str
    dialog_key: str
    base_file: str = Field(default_factory=_base_file_from_data)
    side: Literal['B', 'C'] | None = None
    names: LocalizedNames = Field(default_factory=dict)
    author_texts: LocalizedNames = Field(default_factory=dict)
    collab_credit_tags: LocalizedNames = Field(default_factory=dict)

    @property
    def fallback_name(self) -> str:
        """Return the Game-generated map name when Dialog has no entry."""
        name = default_map_name(self.base_file)
        return f'{name} {self.side}' if self.side is not None else name


class LocalCampaign(BaseModel):
    """One playable campaign and its contained map files."""

    directory: str
    dialog_key: str
    kind: Literal['campaign', 'collab_lobby', 'collab_prologue'] = 'campaign'
    names: LocalizedNames = Field(default_factory=dict)
    maps: list[LocalMap] = Field(default_factory=list)

    @property
    def fallback_name(self) -> str:
        """Return the Game-generated campaign name when Dialog has no entry."""
        return default_campaign_name(self.directory)


class InstalledMod(BaseModel):
    """An enabled local Mod package discovered under the Game Mods directory."""

    source: Literal['zip', 'directory']
    filename: str
    path: str
    metadata_name: str
    metadata_version: str | None
    dependencies: list[Dependency] = Field(default_factory=list)
    optional_dependencies: list[Dependency] = Field(default_factory=list)
    collab_id: str | None = None
    map_files: list[str] = Field(default_factory=list)
    maps: list[LocalMap] = Field(default_factory=list)
    campaigns: list[LocalCampaign] = Field(default_factory=list)


class ModScanReport(BaseModel):
    """Persisted result of an offline scan of locally enabled Mods."""

    mods_directory: str
    blacklist_entries: list[str]
    skipped_blacklisted: list[str]
    disabled_mod_names: list[str] = Field(default_factory=list)
    mods: list[InstalledMod]


def _natural_path_key(path: str) -> tuple[tuple[int, int | str], ...]:
    """Return a case-insensitive natural sort key for a map path."""
    return tuple(
        (0, int(part)) if part.isdecimal() else (1, part.casefold())
        for part in NATURAL_PART_PATTERN.split(path)
    )


def is_mod_dependency(name: str) -> bool:
    """Return whether a manifest dependency belongs in the local Mod graph."""
    return name.casefold() not in IGNORED_DEPENDENCY_NAMES


def is_collab_submission_map(map_info: LocalMap) -> bool:
    """Return whether a Collab map path is neither a lobby nor a Gym."""
    parts = map_info.file_path.casefold().replace('\\', '/').split('/')
    return not any('lobb' in part or 'gym' in part for part in parts)


def collab_journal_map_order(mod: InstalledMod, maps: Iterable[LocalMap]) -> list[LocalMap]:
    """Order one Collab lobby's maps as CollabUtils2 orders its journal entries."""
    map_list = list(maps)
    try:
        icons = _collab_map_icons(mod, map_list)
    except BadMapBin, BadModPath, BadZipFile, FileNotFoundError, KeyError:
        return map_list
    if not all(
        isinstance(icon, str) and COLLAB_JOURNAL_ICON_PATTERN.fullmatch(icon)
        for icon in icons.values()
    ):
        return map_list
    return sorted(
        map_list,
        key=lambda map_info: (
            PurePosixPath(map_info.base_file).stem.casefold() == 'zz-heartside',
            str(icons[map_info.file_path]).casefold(),
            map_info.file_path.casefold(),
        ),
    )


def _collab_map_icons(mod: InstalledMod, maps: list[LocalMap]) -> dict[str, object]:
    if mod.source == 'zip':
        with ZipFile(mod.path) as archive:
            return {
                map_info.file_path: parse_map_meta(
                    archive.read(map_info.file_path), allow_trailing=True
                ).get('Icon')
                for map_info in maps
            }
    mod_dir = Path(mod.path)
    if not mod_dir.is_dir():
        raise BadModPath(f'Invalid Mod directory: {mod.path!r}')
    return {
        map_info.file_path: parse_map_meta(
            (mod_dir / map_info.file_path).read_bytes(), allow_trailing=True
        ).get('Icon')
        for map_info in maps
    }


class ModScanner:
    """Read Everest metadata without changing the game's files."""

    def __init__(self, game_dir: Path) -> None:
        self._mods_dir = game_dir / MODS_DIRNAME

    def scan(self) -> ModScanReport:
        if not self._mods_dir.is_dir():
            raise ValueError(f'Game Mods directory does not exist: {self._mods_dir!r}')

        blacklist = self._read_blacklist()
        skipped_blacklisted: list[str] = []
        disabled_mod_names: list[str] = []
        mods: list[InstalledMod] = []
        for candidate in sorted(self._mods_dir.iterdir(), key=lambda item: item.name.casefold()):
            if candidate.name.casefold() in blacklist:
                skipped_blacklisted.append(candidate.name)
                if metadata_name := self._read_metadata_name(candidate):
                    disabled_mod_names.append(metadata_name)
                continue
            discovered = self._read_mod(candidate)
            if discovered is not None:
                mods.append(discovered)

        return ModScanReport(
            mods_directory=str(self._mods_dir),
            blacklist_entries=sorted(blacklist),
            skipped_blacklisted=skipped_blacklisted,
            disabled_mod_names=sorted(disabled_mod_names, key=str.casefold),
            mods=mods,
        )

    def scan_all(self) -> tuple[InstalledMod, ...]:
        """Return every valid Mod package, including disabled ones."""
        if not self._mods_dir.is_dir():
            raise ValueError(f'Game Mods directory does not exist: {self._mods_dir!r}')
        return tuple(
            mod
            for candidate in sorted(self._mods_dir.iterdir(), key=lambda item: item.name.casefold())
            if (mod := self._read_mod(candidate, localize=False)) is not None
        )

    def _read_blacklist(self) -> set[str]:
        blacklist_path = self._mods_dir / BLACKLIST_FILENAME
        if not blacklist_path.is_file():
            return set()
        return {
            line.casefold()
            for raw_line in blacklist_path.read_text(encoding='utf-8-sig').splitlines()
            if (line := raw_line.strip()) and not line.startswith('#')
        }

    @staticmethod
    def _parse_manifest(content: str, *, source: Path) -> EverestManifest:
        try:
            parsed = yaml.load(content, Loader=yaml.BaseLoader)
        except yaml.YAMLError as error:
            raise ValueError(f'Invalid {MANIFEST_FILENAME} in {source!r}: {error}') from error
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(
                f'{MANIFEST_FILENAME} in {source!r} must contain a non-empty package list.'
            )
        return EverestManifest.model_validate(parsed[0])

    def _read_mod(self, path: Path, *, localize: bool = True) -> InstalledMod | None:
        try:
            with ModPath(path) as mod_path:
                manifest_path = self._find_child(mod_path, MANIFEST_FILENAME)
                if manifest_path is None:
                    return None
                manifest = self._parse_manifest(manifest_path.read_text(), source=path)
                collab_id_path = self._find_child(mod_path, COLLAB_ID_FILENAME)
                collab_id = collab_id_path.read_text().strip() if collab_id_path else None
                maps_dir = self._find_child(mod_path, MAPS_DIRNAME)
                map_files = (
                    sorted(
                        (
                            map_path.at.as_posix()
                            for map_path in iter_files(maps_dir)
                            if map_path.at.suffix.casefold() == '.bin'
                        ),
                        key=self._map_sort_key,
                    )
                    if maps_dir is not None
                    else []
                )
                maps: list[LocalMap] = []
                campaigns: list[LocalCampaign] = []
                if localize:
                    dialogs = self._read_dialogs(mod_path)
                    maps = self._localize_maps(map_files, dialogs=dialogs)
                    campaigns = self._localize_campaigns(
                        maps,
                        collab_id=collab_id or None,
                        dialogs=dialogs,
                    )
                return InstalledMod(
                    source=mod_path.source,
                    filename=path.name,
                    path=str(path),
                    metadata_name=manifest.name,
                    metadata_version=manifest.version,
                    dependencies=manifest.dependencies,
                    optional_dependencies=manifest.optional_dependencies,
                    collab_id=collab_id or None,
                    map_files=map_files,
                    maps=maps,
                    campaigns=campaigns,
                )
        except BadZipModPath as error:
            raise ValueError(f'Invalid Mod archive: {path!r}') from error
        except BadModPath:
            return None
        except FileNotFoundError:
            return None

    def _read_metadata_name(self, path: Path) -> str | None:
        try:
            with ModPath(path) as mod_path:
                manifest_path = self._find_child(mod_path, MANIFEST_FILENAME)
                if manifest_path is None:
                    return None
                return self._parse_manifest(manifest_path.read_text(), source=path).name
        except BadModPath, BadZipModPath, FileNotFoundError:
            return None

    @staticmethod
    def _map_sort_key(map_file: str) -> tuple[int, tuple[tuple[int, int | str], ...], int, str]:
        """Sort map paths naturally, keeping B/C sides after their A-side."""
        base_file, side = map_base_file_and_side(map_file)
        base_path = PurePosixPath(base_file).with_suffix('').as_posix()
        is_heart_side = PurePosixPath(base_file).stem.casefold() == 'zz-heartside'
        return (
            int(is_heart_side),
            _natural_path_key(base_path),
            MAP_SIDE_ORDER[side],
            map_file.casefold(),
        )

    @staticmethod
    def _find_child(root: ModPath, name: str) -> ModPath | None:
        if not root.is_dir():
            return None
        return next(
            (path for path in root.iterdir() if path.name.casefold() == name.casefold()), None
        )

    def _read_dialogs(self, mod_path: ModPath) -> dict[str, Mapping[str, str]]:
        dialog_dir = self._find_child(mod_path, DIALOG_DIRNAME)
        if dialog_dir is None:
            return {}
        return {
            lang: parse_dialog(dialog_path.read_text())
            for lang, filename in DIALOG_FILENAMES.items()
            if (dialog_path := self._find_child(dialog_dir, filename)) is not None
        }

    @staticmethod
    def _localize_maps(
        map_files: list[str],
        *,
        dialogs: Mapping[str, Mapping[str, str]],
    ) -> list[LocalMap]:
        maps: list[LocalMap] = []
        for map_file in map_files:
            base_file, side = map_base_file_and_side(map_file)
            dialog_key = dialog_key_for_map_file(base_file)
            names = {
                lang: name
                for lang, dialog in dialogs.items()
                if (name := ModScanner._side_name(dialog.get(dialog_key), side)) is not None
            }
            author_texts = {
                lang: author
                for lang, dialog in dialogs.items()
                if (author := dialog.get(f'{dialog_key}_author')) is not None
            }
            collab_credit_tags = {
                lang: tags
                for lang, dialog in dialogs.items()
                if (tags := dialog.get(f'{dialog_key}_collabcreditstags')) is not None
            }
            maps.append(
                LocalMap(
                    file_path=map_file,
                    dialog_key=dialog_key,
                    base_file=base_file,
                    side=side,
                    names=names,
                    author_texts=author_texts,
                    collab_credit_tags=collab_credit_tags,
                )
            )
        return maps

    @staticmethod
    def _side_name(name: str | None, side: str | None) -> str | None:
        return f'{name} {side}' if name is not None and side is not None else name

    @staticmethod
    def _localize_campaigns(
        maps: list[LocalMap],
        *,
        collab_id: str | None,
        dialogs: Mapping[str, Mapping[str, str]],
    ) -> list[LocalCampaign]:
        if collab_id and ModScanner._has_collab_lobbies(maps, collab_id):
            return ModScanner._localize_collab_campaigns(
                maps,
                collab_id=collab_id,
                dialogs=dialogs,
            )
        maps_by_campaign: dict[str, list[LocalMap]] = {}
        for map_info in maps:
            campaign_dir = campaign_dir_for_map_file(map_info.file_path)
            if campaign_dir == MAPS_DIRNAME:
                continue
            maps_by_campaign.setdefault(campaign_dir, []).append(map_info)
        campaigns: list[LocalCampaign] = []
        for campaign_dir, campaign_maps in maps_by_campaign.items():
            dialog_key = dialog_key_for_campaign_dir(campaign_dir)
            names = {
                lang: name
                for lang, dialog in dialogs.items()
                if (name := dialog.get(dialog_key)) is not None
            }
            campaigns.append(
                LocalCampaign(
                    directory=campaign_dir,
                    dialog_key=dialog_key,
                    names=names,
                    maps=campaign_maps,
                )
            )
        return campaigns

    @staticmethod
    def _has_collab_lobbies(maps: list[LocalMap], collab_id: str) -> bool:
        collab_dir = PurePosixPath(MAPS_DIRNAME, collab_id)
        return any(
            PurePosixPath(map_info.file_path).parent == collab_dir / '0-Lobbies'
            for map_info in maps
        )

    @staticmethod
    def _localize_collab_campaigns(
        maps: list[LocalMap],
        *,
        collab_id: str,
        dialogs: Mapping[str, Mapping[str, str]],
    ) -> list[LocalCampaign]:
        collab_dir = PurePosixPath(MAPS_DIRNAME, collab_id)
        lobby_dir = collab_dir / '0-Lobbies'
        lobbies = {
            PurePosixPath(map_info.file_path).stem: map_info
            for map_info in maps
            if PurePosixPath(map_info.file_path).parent == lobby_dir
        }
        maps_by_lobby: dict[str, list[LocalMap]] = {}
        for map_info in maps:
            path = PurePosixPath(map_info.file_path)
            try:
                relative = path.relative_to(collab_dir)
            except ValueError:
                continue
            if not relative.parts or relative.parts[0] in {'0-Gyms', '0-Lobbies'}:
                continue
            maps_by_lobby.setdefault(relative.parts[0], []).append(map_info)

        campaigns: list[LocalCampaign] = []
        prologue = lobbies.get('0-Prologue')
        if prologue is not None:
            campaigns.append(
                LocalCampaign(
                    directory=str(lobby_dir),
                    dialog_key=prologue.dialog_key,
                    kind='collab_prologue',
                    names=prologue.names,
                    maps=[prologue],
                )
            )
        for lobby_name, campaign_maps in maps_by_lobby.items():
            lobby = lobbies.get(lobby_name)
            campaign_dir = str(collab_dir / lobby_name)
            dialog_key = (
                lobby.dialog_key if lobby is not None else dialog_key_for_campaign_dir(campaign_dir)
            )
            names = (
                lobby.names.copy()
                if lobby is not None
                else {
                    lang: name
                    for lang, dialog in dialogs.items()
                    if (name := dialog.get(dialog_key)) is not None
                }
            )
            campaigns.append(
                LocalCampaign(
                    directory=campaign_dir,
                    dialog_key=dialog_key,
                    kind='collab_lobby',
                    names=names,
                    maps=campaign_maps,
                )
            )
        return campaigns
