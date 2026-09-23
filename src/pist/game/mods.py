"""Offline discovery of enabled Mods."""

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from zipfile import BadZipFile

import yaml
from pydantic import BaseModel, Field, ValidationError

from pist.containers import CaseFoldDict
from pist.game import content as game_content
from pist.game import dialog, everest
from pist.game.binmap import BadMapBin, parse_map_meta
from pist.game.maps import MapInfo
from pist.models import FrozenModel

COLLAB_ID_FILENAME = 'CollabUtils2CollabID.txt'
MODS_DIRNAME = 'Mods'
BLACKLIST_FILENAME = 'blacklist.txt'
MANIFEST_FILENAME = 'everest.yaml'
MANIFEST_FALLBACK_FILENAME = 'everest.yml'
MANIFEST_FILENAMES = (MANIFEST_FILENAME, MANIFEST_FALLBACK_FILENAME)
MAPS_DIRNAME = 'Maps'
DIALOG_DIRNAME = 'Dialog'
IGNORED_DEPENDENCY_NAMES = frozenset({'Celeste', 'Everest', 'EverestCore'})
MAP_SIDE_SUFFIX_ORDER = {None: 0, 'B': 1, 'C': 2}
NATURAL_PART_PATTERN = re.compile(r'(\d+)')
COLLAB_JOURNAL_ICON_PATTERN = re.compile(r'.*/\d+-.*')


def merge_dialogs(
    mods: Iterable[InstalledMod], *, base_dialogs: Mapping[str, Mapping[str, str]] | None = None
) -> dict[str, CaseFoldDict[str]]:
    """Merge base and loaded Mod Dialog entries in Everest content-crawl order."""
    merged = {language: CaseFoldDict(entries) for language, entries in (base_dialogs or {}).items()}
    for mod in mods:
        for language, entries in mod.dialogs.items():
            current = merged.get(language)
            if current is None:
                current = CaseFoldDict()
                merged[language] = current
            current.update(entries)
    return merged


def localize_dialog_key(
    dialog_key: str, *, dialogs: Mapping[str, Mapping[str, str]]
) -> dialog.LocalizedNames:
    """Look up one Dialog key in every available language."""
    return {
        language: value
        for language, entries in dialogs.items()
        if (value := entries.get(dialog_key)) is not None
    }


def localize_collab_names(
    collab_id: str | None, *, dialogs: Mapping[str, Mapping[str, str]]
) -> dialog.LocalizedNames:
    """Return a Collab title from its lobby LevelSet Dialog keys."""
    if collab_id is None:
        return {}
    dialog_key = dialog.dialog_key_for_campaign_dir(
        game_content.ContentPath(MAPS_DIRNAME, collab_id, '0-Lobbies')
    )
    return {
        language: name
        for language, entries in dialogs.items()
        if (name := entries.get(f'levelset_{dialog_key}') or entries.get(dialog_key))
    }


class InstalledMod(BaseModel, ABC):
    """One physical package discovered under the Game Mods directory.

    The first manifest entry provides the package's display metadata. Every
    entry remains available for package-level dependency resolution.
    """

    filename: str
    path: Path
    manifest: everest.Manifest = Field(min_length=1)
    collab_id: str | None = None
    dialogs: dict[str, dict[str, str]] = Field(default_factory=dict)
    maps: list[MapInfo] = Field(default_factory=list)

    @abstractmethod
    def open(self) -> game_content.ContentEntry:
        """Open this package's content tree for a bounded operation."""

    @property
    def primary_metadata(self) -> everest.EverestModMetadata:
        """Return the first metadata entry, used for package display."""
        return self.manifest[0]

    @property
    def metadata_name(self) -> str:
        """Return the first metadata name, used as the package display name."""
        return self.primary_metadata.name

    @property
    def metadata_version(self) -> str | None:
        """Return the first metadata version, used for package display."""
        version = self.primary_metadata.version
        return None if version is None else str(version)

    @property
    def metadata_names(self) -> tuple[str, ...]:
        """Return every metadata name provided by this package."""
        return tuple(metadata.name for metadata in self.manifest)

    @property
    def map_files(self) -> list[game_content.ContentPath]:
        """Return scanned map paths in package order."""
        return [map_info.file_path for map_info in self.maps]

    def iter_dependencies(self) -> Iterator[everest.Dependency]:
        """Yield required dependencies declared by every manifest entry."""
        for metadata in self.manifest:
            yield from metadata.dependencies

    def iter_optional_dependencies(self) -> Iterator[everest.Dependency]:
        """Yield optional dependencies declared by every manifest entry."""
        for metadata in self.manifest:
            yield from metadata.optional_dependencies


class DirMod(InstalledMod):
    """One directory Mod package."""

    source: Literal['directory'] = 'directory'

    def open(self) -> game_content.DirContentEntry:
        return game_content.DirContentEntry(self.path)


class ZipMod(InstalledMod):
    """One ZIP Mod package."""

    source: Literal['zip'] = 'zip'

    def open(self) -> game_content.ZipContentEntry:
        return game_content.ZipContentEntry(self.path)


type ScannedMod = Annotated[DirMod | ZipMod, Field(discriminator='source')]


class ModScanWarning(FrozenModel):
    """One non-fatal problem encountered while scanning a Mod."""

    mod_filename: str
    file_path: str
    message: str


class ModScanReport(BaseModel):
    """Persisted result of an offline scan of locally enabled Mods."""

    mods_dir: str
    disabled_filenames: list[str]
    disabled_mod_names: list[str] = Field(default_factory=list)
    warnings: list[ModScanWarning] = Field(default_factory=list)
    mods: list[ScannedMod]


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
    return name not in IGNORED_DEPENDENCY_NAMES


def is_collab_submission_map(map_info: MapInfo) -> bool:
    """Return whether a Collab map path is neither a lobby nor a Gym."""
    parts = (part.casefold() for part in map_info.file_path.parts)
    return not any('lobb' in part or 'gym' in part for part in parts)


def collab_journal_map_order(mod: InstalledMod, maps: Iterable[MapInfo]) -> list[MapInfo]:
    """Order one Collab lobby's maps as CollabUtils2 orders its journal entries."""
    map_list = list(maps)
    try:
        icons = dict(iter_collab_journal_map_icons(mod, map_list))
    except BadMapBin, game_content.BadContentEntry, BadZipFile, FileNotFoundError, KeyError:
        return map_list
    return collab_journal_map_order_from_icons(map_list, icons)


def collab_journal_map_order_from_icons(
    maps: Iterable[MapInfo], icons: Mapping[game_content.ContentPath, str | None]
) -> list[MapInfo]:
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
            dialog.split_map_side_suffix(map_info.file_path)[0].stem == 'ZZ-HeartSide',
            str(icons[map_info.file_path]).casefold(),
            map_info.file_path.as_posix().casefold(),
        ),
    )


def iter_collab_journal_map_icons(
    mod: InstalledMod, maps: Iterable[MapInfo]
) -> Iterator[tuple[game_content.ContentPath, str | None]]:
    """Yield Collab journal icon paths one map at a time without loading layouts."""
    with mod.open() as root:
        for map_info in maps:
            icon = parse_map_meta(
                root.joinpath(map_info.file_path).read_bytes(), allow_trailing=True
            ).get('Icon')
            yield map_info.file_path, icon if isinstance(icon, str) else None


def collab_journal_icon_fingerprint(mod: InstalledMod, maps: Iterable[MapInfo]) -> str:
    """Return a fingerprint that changes when a source map package changes."""
    map_files = sorted(map_info.file_path for map_info in maps)
    digest = sha256()
    with mod.open() as root:
        for map_file in map_files:
            entry = root.joinpath(map_file)
            digest.update(f'{map_file}\0{entry.fingerprint()}\0'.encode())
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

    def scan_mod(self, path: Path) -> ScannedMod | None:
        """Scan one candidate prepared by :meth:`prepare`."""
        return self._read_mod(path)

    def scan_disabled_mod(self, path: Path) -> DisabledMod:
        """Read the minimal metadata needed to identify one disabled Mod."""
        return DisabledMod(
            filename=path.name,
            disabled_mod_names=self._read_metadata_names(path),
        )

    def build_report(
        self, mods: Iterable[ScannedMod], disabled_mods: Iterable[DisabledMod]
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

    def scan_all(self) -> tuple[ScannedMod, ...]:
        """Return every valid Mod package, including disabled ones."""
        self._warnings.clear()
        if not self._mods_dir.is_dir():
            raise ValueError(f'Game Mods directory does not exist: {self._mods_dir!r}')
        return tuple(
            mod
            for candidate in (*self._zip_mod_paths(), *self._directory_mod_paths())
            if (mod := self._read_mod(candidate, read_dialogs=False)) is not None
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
    def _parse_manifest(content: str, *, source: Path) -> everest.Manifest:
        try:
            parsed = yaml.load(content, Loader=yaml.BaseLoader)
        except yaml.YAMLError as error:
            raise ValueError(f'Invalid {MANIFEST_FILENAME} in {source!r}: {error}') from error
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(
                f'{MANIFEST_FILENAME} in {source!r} must contain a non-empty package list.'
            )
        return everest.MANIFEST_ADAPTER.validate_python(parsed)

    def _read_mod(self, path: Path, *, read_dialogs: bool = True) -> ScannedMod | None:
        try:
            with game_content.ContentEntry(path) as mod_path:
                mod_type = ZipMod if isinstance(mod_path, game_content.ZipContentEntry) else DirMod
                manifest_path = self._find_manifest(mod_path)
                self._warn_invalid_zip_members(mod_path, mod_filename=path.name)
                if manifest_path is None:
                    return None
                manifest_content = self._read_text(manifest_path, mod_filename=path.name)
                if manifest_content is None:
                    return None
                try:
                    manifest = self._parse_manifest(manifest_content, source=path)
                except (ValueError, ValidationError) as error:
                    self._warn_invalid_manifest(path, manifest_path, error)
                    manifest = self._fallback_manifest(
                        path, archive=isinstance(mod_path, game_content.ZipContentEntry)
                    )
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
                            map_path.at
                            for map_path in game_content.iter_files(maps_dir)
                            if map_path.at.suffix == '.bin'
                        ),
                        key=self._map_sort_key,
                    )
                    if maps_dir is not None
                    else []
                )
                maps = [MapInfo(file_path=map_file) for map_file in map_files]
                dialogs: dict[str, CaseFoldDict[str]] = {}
                if read_dialogs:
                    dialogs = self._read_dialogs(mod_path, mod_filename=path.name)
                return mod_type(
                    filename=path.name,
                    path=path,
                    manifest=manifest,
                    collab_id=collab_id or None,
                    dialogs={language: dict(entries) for language, entries in dialogs.items()},
                    maps=maps,
                )
        except game_content.BadZipContentEntry as error:
            raise ValueError(f'Invalid Mod archive: {path!r}') from error
        except game_content.BadContentEntry:
            return None
        except FileNotFoundError:
            return None

    def _read_metadata_names(self, path: Path) -> tuple[str, ...]:
        try:
            with game_content.ContentEntry(path) as mod_path:
                manifest_path = self._find_manifest(mod_path)
                self._warn_invalid_zip_members(mod_path, mod_filename=path.name, disabled=True)
                if manifest_path is None:
                    return ()
                content = self._read_text(manifest_path, mod_filename=path.name)
                if content is None:
                    return ()
                try:
                    manifest = self._parse_manifest(content, source=path)
                except (ValueError, ValidationError) as error:
                    self._warn_invalid_manifest(path, manifest_path, error, disabled=True)
                    return ()
                return tuple(metadata.name for metadata in manifest)
        except game_content.BadContentEntry, game_content.BadZipContentEntry, FileNotFoundError:
            return ()

    def _warn_invalid_manifest(
        self,
        path: Path,
        manifest_path: game_content.ContentEntry,
        error: ValueError,
        *,
        disabled: bool = False,
    ) -> None:
        """Record malformed manifest metadata without aborting the package scan."""
        prefix = '禁用 Mod 元数据无效' if disabled else 'Mod 元数据无效'
        self._warnings.append(
            ModScanWarning(
                mod_filename=path.name,
                file_path=manifest_path.at.as_posix(),
                message=f'{prefix}：{error}',
            )
        )

    def _warn_invalid_zip_members(
        self,
        root: game_content.ContentEntry,
        *,
        mod_filename: str,
        disabled: bool = False,
    ) -> None:
        """Record ZIP members ignored because they cannot name virtual content."""
        if not isinstance(root, game_content.ZipContentEntry):
            return
        prefix = '禁用 Mod' if disabled else 'Mod'
        for member_path in root.invalid_member_paths():
            self._warnings.append(
                ModScanWarning(
                    mod_filename=mod_filename,
                    file_path=member_path,
                    message=f'{prefix} 包含无效成员路径，已忽略。',
                )
            )

    @staticmethod
    def _fallback_manifest(path: Path, *, archive: bool) -> everest.Manifest:
        """Build the dummy metadata Everest uses after a manifest load failure."""
        prefix = '_zip_' if archive else '_dir_'
        name = path.stem if archive else path.name
        return (
            everest.EverestModMetadata(
                name=f'{prefix}{name}',
                version=everest.Version.parse('0.0.0-dummy'),
            ),
        )

    @staticmethod
    def _map_sort_key(
        map_file: game_content.ContentPath,
    ) -> tuple[int, tuple[tuple[int, int | str], ...], int, str]:
        """Sort map paths naturally, keeping B/C sides after their A-side."""
        base_file, side_suffix = dialog.split_map_side_suffix(map_file)
        base_path = base_file.with_suffix('').as_posix()
        is_heart_side = base_file.stem == 'ZZ-HeartSide'
        return (
            int(is_heart_side),
            _natural_path_key(base_path),
            MAP_SIDE_SUFFIX_ORDER[side_suffix],
            map_file.as_posix().casefold(),
        )

    @staticmethod
    def _find_child(root: game_content.ContentEntry, name: str) -> game_content.ContentEntry | None:
        if not root.is_dir():
            return None
        path = root.joinpath(name)
        return path if path.exists() else None

    @classmethod
    def _find_manifest(cls, root: game_content.ContentEntry) -> game_content.ContentEntry | None:
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
        self, mod_path: game_content.ContentEntry, *, mod_filename: str
    ) -> dict[str, CaseFoldDict[str]]:
        dialog_dir = self._find_child(mod_path, DIALOG_DIRNAME)
        if dialog_dir is None:
            return {}
        dialogs: dict[str, CaseFoldDict[str]] = {}
        for lang, filename in dialog.DIALOG_FILENAMES.items():
            dialog_path = self._find_child(dialog_dir, filename)
            if dialog_path is None:
                continue
            content = self._read_text(dialog_path, mod_filename=mod_filename)
            if content is not None:
                dialogs[lang] = dialog.parse_dialog(content)
        return dialogs

    def _read_text(self, path: game_content.ContentEntry, *, mod_filename: str) -> str | None:
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
