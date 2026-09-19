"""Offline discovery of enabled Mods."""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import BadZipFile, ZipFile

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pist.game import dialog
from pist.game.binmap import BadMapBin, parse_map_meta
from pist.game.mod_path import BadModPath, BadZipModPath, ModPath, iter_files
from pist.models import ExternalModel, FrozenModel

COLLAB_ID_FILENAME = 'CollabUtils2CollabID.txt'
MODS_DIRNAME = 'Mods'
BLACKLIST_FILENAME = 'blacklist.txt'
MANIFEST_FILENAME = 'everest.yaml'
MANIFEST_FALLBACK_FILENAME = 'everest.yml'
MANIFEST_FILENAMES = (MANIFEST_FILENAME, MANIFEST_FALLBACK_FILENAME)
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


class Dependency(ExternalModel):
    """One required or optional Everest Mod dependency."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: str | None = Field(default=None, validation_alias='Version')


class EverestModMetadata(ExternalModel):
    """One Mod metadata entry declared in an Everest manifest."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(validation_alias='Name')
    version: str | None = Field(default=None, validation_alias='Version')
    dependencies: list[Dependency] = Field(default_factory=list, validation_alias='Dependencies')
    optional_dependencies: list[Dependency] = Field(
        default_factory=list, validation_alias='OptionalDependencies'
    )
    dll: str | None = Field(default=None, validation_alias='DLL')


type EverestManifest = tuple[EverestModMetadata, ...]

MANIFEST_ADAPTER = TypeAdapter(EverestManifest)


class LocalMap(BaseModel):
    """One map file and its localized display-name candidates."""

    file_path: str
    dialog_key: str
    side: Literal['B', 'C'] | None = None
    names: dialog.LocalizedNames = Field(default_factory=dict)
    author_texts: dialog.LocalizedNames = Field(default_factory=dict)
    collab_credit_tags: dialog.LocalizedNames = Field(default_factory=dict)

    @property
    def base_file(self) -> str:
        """Return the A-side file path from which this map's identity derives."""
        return dialog.map_base_file_and_side(self.file_path)[0]

    @property
    def fallback_name(self) -> str:
        """Return the Game-generated map name when Dialog has no entry."""
        name = dialog.default_map_name(self.base_file)
        return f'{name} {self.side}' if self.side is not None else name


class LocalCampaign(BaseModel):
    """One playable campaign and its contained map files."""

    directory: str
    dialog_key: str
    kind: Literal['campaign', 'collab_lobby', 'collab_prologue'] = 'campaign'
    names: dialog.LocalizedNames = Field(default_factory=dict)
    maps: list[LocalMap] = Field(default_factory=list)

    @property
    def fallback_name(self) -> str:
        """Return the Game-generated campaign name when Dialog has no entry."""
        return dialog.default_campaign_name(self.directory)


class InstalledMod(BaseModel):
    """One physical package discovered under the Game Mods directory.

    The first manifest entry provides the package's display metadata. Every
    entry remains available for package-level dependency resolution.
    """

    source: Literal['zip', 'directory']
    filename: str
    path: str
    manifest: EverestManifest = Field(min_length=1)
    collab_id: str | None = None
    map_files: list[str] = Field(default_factory=list)
    maps: list[LocalMap] = Field(default_factory=list)
    campaigns: list[LocalCampaign] = Field(default_factory=list)

    @property
    def primary_metadata(self) -> EverestModMetadata:
        """Return the first metadata entry, used for package display."""
        return self.manifest[0]

    @property
    def metadata_name(self) -> str:
        """Return the first metadata name, used as the package display name."""
        return self.primary_metadata.name

    @property
    def metadata_version(self) -> str | None:
        """Return the first metadata version, used for package display."""
        return self.primary_metadata.version

    @property
    def metadata_names(self) -> tuple[str, ...]:
        """Return every metadata name provided by this package."""
        return tuple(metadata.name for metadata in self.manifest)

    def iter_dependencies(self) -> Iterator[Dependency]:
        """Yield required dependencies declared by every manifest entry."""
        for metadata in self.manifest:
            yield from metadata.dependencies

    def iter_optional_dependencies(self) -> Iterator[Dependency]:
        """Yield optional dependencies declared by every manifest entry."""
        for metadata in self.manifest:
            yield from metadata.optional_dependencies


class ModScanWarning(FrozenModel):
    """One non-fatal text decoding problem encountered while scanning a Mod."""

    mod_filename: str
    file_path: str
    message: str


class ModScanReport(BaseModel):
    """Persisted result of an offline scan of locally enabled Mods."""

    mods_dir: str
    disabled_filenames: list[str]
    disabled_mod_names: list[str] = Field(default_factory=list)
    warnings: list[ModScanWarning] = Field(default_factory=list)
    mods: list[InstalledMod]


@dataclass(frozen=True, slots=True)
class DisabledMod:
    """The metadata available for one disabled Mod package."""

    filename: str
    disabled_mod_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModScanPreparation:
    """The local packages discovered before an enabled-Mod scan begins."""

    candidates: tuple[Path, ...]
    disabled_candidates: tuple[Path, ...]


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
        icons = dict(iter_collab_journal_map_icons(mod, map_list))
    except BadMapBin, BadModPath, BadZipFile, FileNotFoundError, KeyError:
        return map_list
    return collab_journal_map_order_from_icons(map_list, icons)


def collab_journal_map_order_from_icons(
    maps: Iterable[LocalMap], icons: Mapping[str, str | None]
) -> list[LocalMap]:
    """Order maps from cached Collab journal icon paths when they are all usable."""
    map_list = list(maps)
    if not all(
        (icon := icons.get(map_info.file_path)) is not None
        and COLLAB_JOURNAL_ICON_PATTERN.fullmatch(icon)
        for map_info in map_list
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


def iter_collab_journal_map_icons(
    mod: InstalledMod, maps: Iterable[LocalMap]
) -> Iterator[tuple[str, str | None]]:
    """Yield Collab journal icon paths one map at a time without loading layouts."""
    if mod.source == 'zip':
        with ZipFile(mod.path) as archive:
            for map_info in maps:
                icon = parse_map_meta(archive.read(map_info.file_path), allow_trailing=True).get(
                    'Icon'
                )
                yield map_info.file_path, icon if isinstance(icon, str) else None
        return
    mod_dir = Path(mod.path)
    if not mod_dir.is_dir():
        raise BadModPath(f'Invalid Mod directory: {mod.path!r}')
    for map_info in maps:
        icon = parse_map_meta((mod_dir / map_info.file_path).read_bytes(), allow_trailing=True).get(
            'Icon'
        )
        yield map_info.file_path, icon if isinstance(icon, str) else None


def collab_journal_icon_fingerprint(mod: InstalledMod, maps: Iterable[LocalMap]) -> str:
    """Return a fingerprint that changes when a source map package changes."""
    map_files = sorted(map_info.file_path for map_info in maps)
    digest = sha256()
    digest.update(mod.source.encode())
    digest.update(b'\0')
    mod_path = Path(mod.path)
    if mod.source == 'zip':
        with ZipFile(mod_path) as archive:
            for map_file in map_files:
                info = archive.getinfo(map_file)
                digest.update(
                    (
                        f'{map_file}\0{info.CRC}:{info.compress_size}:'
                        f'{info.file_size}:{info.date_time}\0'
                    ).encode()
                )
    else:
        if not mod_path.is_dir():
            raise BadModPath(f'Invalid Mod directory: {mod.path!r}')
        for map_file in map_files:
            stat = (mod_path / map_file).stat()
            digest.update(f'{map_file}\0{stat.st_size}:{stat.st_mtime_ns}\0'.encode())
    for map_file in map_files:
        digest.update(map_file.encode())
        digest.update(b'\0')
    return digest.hexdigest()


class ModScanner:
    """Read Everest metadata without changing the game's files."""

    def __init__(
        self,
        game_dir: Path,
        *,
        whitelist_path: Path | None = None,
        temporary_blacklist_path: Path | None = None,
        whitelist_full_override: bool = False,
    ) -> None:
        self._mods_dir = game_dir / MODS_DIRNAME
        self._whitelist_path = whitelist_path
        self._temporary_blacklist_path = temporary_blacklist_path
        self._whitelist_full_override = whitelist_full_override
        self._warnings: list[ModScanWarning] = []

    def scan(self) -> ModScanReport:
        """Read every enabled Mod package and return its finished scan report."""
        preparation = self.prepare()
        return self.build_report(
            (
                mod
                for candidate in preparation.candidates
                if (mod := self.scan_mod(candidate)) is not None
            ),
            (self.scan_disabled_mod(candidate) for candidate in preparation.disabled_candidates),
        )

    def prepare(self) -> ModScanPreparation:
        """Discover the packages Everest would load without opening any package."""
        self._warnings.clear()
        if not self._mods_dir.is_dir():
            raise ValueError(f'Game Mods directory does not exist: {self._mods_dir!r}')

        blacklist = self._read_blacklist()
        temporary_blacklist = self._read_optional_list(self._temporary_blacklist_path)
        whitelist = self._read_optional_list(self._whitelist_path)
        packages = (*self._zip_mod_paths(), *self._directory_mod_paths())
        return ModScanPreparation(
            candidates=tuple(
                package
                for package in packages
                if self._should_load(package.name, blacklist, temporary_blacklist, whitelist)
            ),
            disabled_candidates=tuple(
                package
                for package in packages
                if not self._should_load(package.name, blacklist, temporary_blacklist, whitelist)
            ),
        )

    def scan_mod(self, path: Path) -> InstalledMod | None:
        """Scan one candidate prepared by :meth:`prepare`."""
        return self._read_mod(path)

    def scan_disabled_mod(self, path: Path) -> DisabledMod:
        """Read the minimal metadata needed to identify one disabled Mod."""
        return DisabledMod(
            filename=path.name,
            disabled_mod_names=self._read_metadata_names(path),
        )

    def build_report(
        self, mods: Iterable[InstalledMod], disabled_mods: Iterable[DisabledMod]
    ) -> ModScanReport:
        """Build a completed report from scanned Mod results."""
        disabled = tuple(disabled_mods)
        scanned_mods = list(mods)
        return ModScanReport(
            mods_dir=str(self._mods_dir),
            disabled_filenames=[mod.filename for mod in disabled],
            disabled_mod_names=sorted(
                {name for mod in disabled for name in mod.disabled_mod_names},
                key=str.casefold,
            ),
            warnings=self._warnings.copy(),
            mods=scanned_mods,
        )

    def scan_all(self) -> tuple[InstalledMod, ...]:
        """Return every valid Mod package, including disabled ones."""
        self._warnings.clear()
        if not self._mods_dir.is_dir():
            raise ValueError(f'Game Mods directory does not exist: {self._mods_dir!r}')
        return tuple(
            mod
            for candidate in (*self._zip_mod_paths(), *self._directory_mod_paths())
            if (mod := self._read_mod(candidate, localize=False)) is not None
        )

    def _zip_mod_paths(self) -> tuple[Path, ...]:
        """Return ZIP packages in the order Everest loads them."""
        return tuple(
            sorted(
                (
                    path
                    for path in self._mods_dir.iterdir()
                    if path.is_file() and path.name.endswith('.zip')
                ),
                key=lambda path: path.name,
            )
        )

    def _directory_mod_paths(self) -> tuple[Path, ...]:
        """Return Mod directories in the order Everest loads them."""
        return tuple(
            sorted(
                (
                    path
                    for path in self._mods_dir.iterdir()
                    if path.is_dir() and path.name != 'Cache'
                ),
                key=lambda path: path.name,
            )
        )

    def _read_blacklist(self) -> set[str]:
        return self._read_list(self._mods_dir / BLACKLIST_FILENAME) or set()

    def _read_optional_list(self, path: Path | None) -> set[str] | None:
        if path is None:
            return None
        return self._read_list(path if path.is_absolute() else self._mods_dir / path)

    @staticmethod
    def _read_list(path: Path) -> set[str] | None:
        if not path.is_file():
            return None
        return {
            ('' if line.startswith('#') else line).strip()
            for line in path.read_text(encoding='utf-8-sig').splitlines()
        }

    def _should_load(
        self,
        filename: str,
        blacklist: set[str],
        temporary_blacklist: set[str] | None,
        whitelist: set[str] | None,
    ) -> bool:
        """Return whether Everest's loader would load a package with these options."""
        is_blacklisted = filename in blacklist or (
            temporary_blacklist is not None and filename in temporary_blacklist
        )
        if self._whitelist_full_override:
            return filename in whitelist if whitelist is not None else not is_blacklisted
        return (whitelist is not None and filename in whitelist) or not is_blacklisted

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
        return MANIFEST_ADAPTER.validate_python(parsed)

    def _read_mod(self, path: Path, *, localize: bool = True) -> InstalledMod | None:
        try:
            with ModPath(path) as mod_path:
                manifest_path = self._find_manifest(mod_path)
                if manifest_path is None:
                    return None
                manifest_content = self._read_text(manifest_path, mod_filename=path.name)
                if manifest_content is None:
                    return None
                manifest = self._parse_manifest(manifest_content, source=path)
                collab_id_path = self._find_child(mod_path, COLLAB_ID_FILENAME)
                collab_id = (
                    content.strip()
                    if collab_id_path is not None
                    and (content := self._read_text(collab_id_path, mod_filename=path.name))
                    is not None
                    else None
                )
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
                    dialogs = self._read_dialogs(mod_path, mod_filename=path.name)
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
                    manifest=manifest,
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

    def _read_metadata_names(self, path: Path) -> tuple[str, ...]:
        try:
            with ModPath(path) as mod_path:
                manifest_path = self._find_manifest(mod_path)
                if manifest_path is None:
                    return ()
                content = self._read_text(manifest_path, mod_filename=path.name)
                return (
                    tuple(metadata.name for metadata in self._parse_manifest(content, source=path))
                    if content is not None
                    else ()
                )
        except BadModPath, BadZipModPath, FileNotFoundError:
            return ()

    @staticmethod
    def _map_sort_key(map_file: str) -> tuple[int, tuple[tuple[int, int | str], ...], int, str]:
        """Sort map paths naturally, keeping B/C sides after their A-side."""
        base_file, side = dialog.map_base_file_and_side(map_file)
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
        path = root.joinpath(name)
        return path if path.exists() else None

    @classmethod
    def _find_manifest(cls, root: ModPath) -> ModPath | None:
        """Find Everest's preferred manifest name, then its legacy fallback."""
        return next(
            (
                manifest_path
                for filename in MANIFEST_FILENAMES
                if (manifest_path := cls._find_child(root, filename)) is not None
            ),
            None,
        )

    def _read_dialogs(
        self, mod_path: ModPath, *, mod_filename: str
    ) -> dict[str, Mapping[str, str]]:
        dialog_dir = self._find_child(mod_path, DIALOG_DIRNAME)
        if dialog_dir is None:
            return {}
        dialogs: dict[str, Mapping[str, str]] = {}
        for lang, filename in DIALOG_FILENAMES.items():
            dialog_path = self._find_child(dialog_dir, filename)
            if dialog_path is None:
                continue
            content = self._read_text(dialog_path, mod_filename=mod_filename)
            if content is not None:
                dialogs[lang] = dialog.parse_dialog(content)
        return dialogs

    def _read_text(self, path: ModPath, *, mod_filename: str) -> str | None:
        """Read one Mod text file, retaining decoding failures as scan diagnostics."""
        try:
            return path.read_text()
        except UnicodeDecodeError as error:
            self._warnings.append(
                ModScanWarning(
                    mod_filename=mod_filename,
                    file_path=path.at.as_posix(),
                    message=f'无法以 UTF-8 解码：{error.reason}',
                )
            )
            return None

    @staticmethod
    def _localize_maps(
        map_files: list[str],
        *,
        dialogs: Mapping[str, Mapping[str, str]],
    ) -> list[LocalMap]:
        maps: list[LocalMap] = []
        for map_file in map_files:
            base_file, side = dialog.map_base_file_and_side(map_file)
            dialog_key = dialog.dialog_key_for_map_file(base_file)
            names = {
                lang: name
                for lang, dialog_entries in dialogs.items()
                if (name := ModScanner._side_name(dialog_entries.get(dialog_key), side)) is not None
            }
            author_texts = {
                lang: author
                for lang, dialog_entries in dialogs.items()
                if (author := dialog_entries.get(f'{dialog_key}_author')) is not None
            }
            collab_credit_tags = {
                lang: tags
                for lang, dialog_entries in dialogs.items()
                if (tags := dialog_entries.get(f'{dialog_key}_collabcreditstags')) is not None
            }
            maps.append(
                LocalMap(
                    file_path=map_file,
                    dialog_key=dialog_key,
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
            campaign_dir = dialog.campaign_dir_for_map_file(map_info.file_path)
            if campaign_dir == MAPS_DIRNAME:
                continue
            maps_by_campaign.setdefault(campaign_dir, []).append(map_info)
        campaigns: list[LocalCampaign] = []
        for campaign_dir, campaign_maps in maps_by_campaign.items():
            dialog_key = dialog.dialog_key_for_campaign_dir(campaign_dir)
            names = {
                lang: name
                for lang, dialog_entries in dialogs.items()
                if (name := dialog_entries.get(dialog_key)) is not None
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
                lobby.dialog_key
                if lobby is not None
                else dialog.dialog_key_for_campaign_dir(campaign_dir)
            )
            names = (
                lobby.names.copy()
                if lobby is not None
                else {
                    lang: name
                    for lang, dialog_entries in dialogs.items()
                    if (name := dialog_entries.get(dialog_key)) is not None
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
