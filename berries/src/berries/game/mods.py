"""Offline Mod package discovery, enabled selection and content reading."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from logging import INFO, WARNING, getLogger
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile

import yaml
from pydantic import BaseModel, Field

from berries.containers import CaseFoldDict

from . import content, dialog, everest
from .maps import MapFilePath

_LOGGER = getLogger(__name__)

try:
    from yaml import CBaseLoader as _BaseLoader
except ImportError:
    from yaml import BaseLoader as _BaseLoader

COLLAB_ID_FILENAME = 'CollabUtils2CollabID.txt'
MODS_DIRNAME = 'Mods'
BLACKLIST_FILENAME = 'blacklist.txt'
MANIFEST_FILENAME = 'everest.yaml'
MANIFEST_FALLBACK_FILENAME = 'everest.yml'
MANIFEST_FILENAMES = (MANIFEST_FILENAME, MANIFEST_FALLBACK_FILENAME)
IGNORED_DEPENDENCY_NAMES = frozenset({'Celeste', 'Everest', 'EverestCore'})


class InstalledMod(BaseModel, ABC):
    """One physical package discovered under the Game Mods directory.

    The first manifest entry provides the package's display metadata. Every
    entry remains available for package-level dependency resolution.
    """

    filename: str
    path: Path
    manifest: everest.Manifest = Field(min_length=1)
    collab_id: str | None = None
    dialogs: dict[str, dialog.DialogEntries] = Field(default_factory=dict)
    map_files: list[MapFilePath] = Field(default_factory=list)

    @abstractmethod
    def open(self) -> content.ContentEntry:
        """Open this package's content tree for a bounded operation."""

    @property
    def primary_metadata(self) -> everest.ModMetadata:
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

    def open(self) -> content.DirContentEntry:
        return content.DirContentEntry(self.path)


class ZipMod(InstalledMod):
    """One ZIP Mod package."""

    def open(self) -> content.ZipContentEntry:
        return content.ZipContentEntry(self.path)


class ModScanStage(StrEnum):
    """The content operation associated with one scan failure."""

    PACKAGE = 'package'
    MANIFEST = 'manifest'
    TEXT = 'text'
    DIALOG = 'dialog'
    MAPS = 'maps'
    ZIP_MEMBER = 'zip_member'


class ModScanError(Exception):
    """A package scan failure with its physical source, operation and original cause."""

    def __init__(
        self,
        path: Path,
        file_path: str,
        stage: ModScanStage,
        cause: Exception,
        *,
        disabled: bool = False,
    ) -> None:
        self.path = path
        self.file_path = file_path
        self.stage = stage
        self.cause = cause
        self.disabled = disabled
        super().__init__(f'{stage}: {path} / {file_path}: {cause}')


class ModScanReport(BaseModel):
    """In-memory scan facts, exportable as JSON inventory but not a package reload format."""

    mods_dir: str
    disabled_filenames: list[str]
    disabled_mod_names: list[str] = Field(default_factory=list)
    mods: list[InstalledMod]

    @property
    def enabled_mods(self) -> tuple[InstalledMod, ...]:
        """Return only selected packages, even when disabled content was also scanned."""
        disabled = set(self.disabled_filenames)
        return tuple(mod for mod in self.mods if mod.filename not in disabled)


@dataclass(frozen=True, slots=True)
class DisabledMod:
    """The metadata available for one disabled Mod package."""

    filename: str
    disabled_mod_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModScanProgress:
    """Scan progress, initially zero and then updated after each physical package."""

    completed: int
    total: int
    path: Path | None = None
    disabled: bool = False


def is_mod_dependency(name: str) -> bool:
    """Return whether a manifest dependency belongs in the local Mod graph."""
    return name not in IGNORED_DEPENDENCY_NAMES


class ModScanner:
    """Read Everest metadata without changing the game's files."""

    def __init__(
        self,
        game_dir: Path,
        *,
        whitelist_path: Path | None = None,
        temporary_blacklist_path: Path | None = None,
        whitelist_full_override: bool = False,
        on_error: Callable[[ModScanError], None] | None = None,
        on_path_notice: Callable[[Path, str, Literal['info', 'warning'], bool], None] | None = None,
    ) -> None:
        self._mods_dir = game_dir / MODS_DIRNAME
        self._whitelist_path = whitelist_path
        self._temporary_blacklist_path = temporary_blacklist_path
        self._whitelist_full_override = whitelist_full_override
        self._on_error = on_error
        self._on_path_notice = on_path_notice

    def scan(
        self,
        *,
        include_disabled: bool = False,
        read_dialogs: bool = True,
        on_progress: Callable[[ModScanProgress], None] | None = None,
    ) -> ModScanReport:
        """Discover packages in Everest order and read their declared content.

        Disabled packages normally contribute metadata names only. include_disabled
        includes their content too; read_dialogs independently controls language reads.
        Failures raise by default. The constructor's on_error callback may return to
        continue using the documented fallback or skip the failed input, or raise to abort.
        Progress callbacks run outside error handlers; their exceptions propagate unchanged.
        Ignored ZIP paths use on_path_notice(root, raw_path, severity, disabled),
        or standard logging when no handler is supplied. Notices never register
        the ignored path; handler exceptions propagate without being swallowed.
        """
        if not self._mods_dir.is_dir():
            raise ValueError(f'Game Mods directory does not exist: {self._mods_dir!r}')

        blacklist = self._read_blacklist()
        temporary_blacklist = self._read_optional_list(self._temporary_blacklist_path)
        whitelist = self._read_optional_list(self._whitelist_path)
        pkgs = (*self._zip_mod_paths(), *self._dir_mod_paths())
        scanned_mods: list[InstalledMod] = []
        disabled: list[DisabledMod] = []
        if on_progress is not None:
            on_progress(ModScanProgress(0, len(pkgs)))
        for i, pkg in enumerate(pkgs, start=1):
            enabled = self._should_load(pkg.name, blacklist, temporary_blacklist, whitelist)
            if enabled or include_disabled:
                mod = self.read_mod(pkg, read_dialogs=read_dialogs, disabled=not enabled)
                if mod is not None:
                    scanned_mods.append(mod)
                if not enabled:
                    disabled.append(
                        DisabledMod(pkg.name, () if mod is None else mod.metadata_names)
                    )
            else:
                disabled.append(DisabledMod(pkg.name, self._read_metadata_names(pkg)))
            if on_progress is not None:
                on_progress(ModScanProgress(i, len(pkgs), pkg, not enabled))
        return ModScanReport(
            mods_dir=str(self._mods_dir),
            disabled_filenames=[mod.filename for mod in disabled],
            disabled_mod_names=sorted(
                {name for mod in disabled for name in mod.disabled_mod_names},
                key=str.casefold,
            ),
            mods=scanned_mods,
        )

    def _report_error(self, error: ModScanError) -> None:
        if self._on_error is None:
            raise error from error.cause
        self._on_error(error)

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

    def _dir_mod_paths(self) -> tuple[Path, ...]:
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
    def _parse_manifest(text: str, *, source: Path) -> everest.Manifest:
        try:
            parsed = yaml.load(text, Loader=_BaseLoader)
        except yaml.YAMLError as error:
            raise ValueError(f'Invalid {MANIFEST_FILENAME} in {source!r}: {error}') from error
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(
                f'{MANIFEST_FILENAME} in {source!r} must contain a non-empty package list.'
            )
        return everest.MANIFEST_ADAPTER.validate_python(parsed)

    def read_mod(
        self, path: Path, *, read_dialogs: bool = True, disabled: bool = False
    ) -> InstalledMod | None:
        """Read one package independently of enabled selection; missing manifests return None.

        A returning on_error callback permits partial content and Everest's dummy
        manifest fallback. Map paths retain content traversal order, not display order.
        """
        try:
            mod_path = content.ContentEntry(path)
        except (content.BadContentEntry, OSError, BadZipFile) as error:
            self._report_error(
                ModScanError(path, '', ModScanStage.PACKAGE, error, disabled=disabled)
            )
            return None
        with mod_path:
            mod_type = ZipMod if isinstance(mod_path, content.ZipContentEntry) else DirMod
            manifest_path = self._find_manifest(mod_path)
            self._report_zip_paths(mod_path, disabled=disabled)
            if manifest_path is None:
                return None
            manifest_text = self._read_text(manifest_path, disabled=disabled)
            if manifest_text is None:
                return None
            try:
                manifest = self._parse_manifest(manifest_text, source=path)
            except ValueError as error:
                self._report_invalid_manifest(path, manifest_path, error, disabled=disabled)
                manifest = self._fallback_manifest(
                    path, archive=isinstance(mod_path, content.ZipContentEntry)
                )
            collab_id_path = self._find_child(mod_path, COLLAB_ID_FILENAME)
            collab_id = (
                text.strip()
                if collab_id_path is not None
                and (text := self._read_text(collab_id_path, disabled=disabled)) is not None
                else None
            )
            map_files = self._read_map_paths(mod_path, disabled=disabled)
            dialogs = self._read_dialogs(mod_path, disabled=disabled) if read_dialogs else {}
            return mod_type(
                filename=path.name,
                path=path,
                manifest=manifest,
                collab_id=collab_id or None,
                dialogs=dialogs,
                map_files=list(map_files),
            )

    def _read_metadata_names(self, path: Path) -> tuple[str, ...]:
        try:
            mod_path = content.ContentEntry(path)
        except (content.BadContentEntry, OSError, BadZipFile) as error:
            self._report_error(ModScanError(path, '', ModScanStage.PACKAGE, error, disabled=True))
            return ()
        with mod_path:
            manifest_path = self._find_manifest(mod_path)
            self._report_zip_paths(mod_path, disabled=True)
            if manifest_path is None:
                return ()
            text = self._read_text(manifest_path, disabled=True)
            if text is None:
                return ()
            try:
                manifest = self._parse_manifest(text, source=path)
            except ValueError as error:
                self._report_invalid_manifest(path, manifest_path, error, disabled=True)
                return ()
            return tuple(metadata.name for metadata in manifest)

    def _report_invalid_manifest(
        self,
        path: Path,
        manifest_path: content.ContentEntry,
        error: ValueError,
        *,
        disabled: bool = False,
    ) -> None:
        """Offer malformed manifest metadata to the caller's failure policy."""
        self._report_error(
            ModScanError(
                path, manifest_path.at.as_posix(), ModScanStage.MANIFEST, error, disabled=disabled
            )
        )

    def _report_zip_paths(
        self,
        root: content.ContentEntry,
        *,
        disabled: bool = False,
    ) -> None:
        """Report skipped paths separately from input failures requiring recovery."""
        if not isinstance(root, content.ZipContentEntry):
            return
        for member_path in root.zip_file.member_index.invalid_paths:
            virtual = member_path.replace('\\', '/')
            if any(part.startswith('.') and part not in {'.', '..'} for part in virtual.split('/')):
                continue
            severity: Literal['info', 'warning'] = (
                'info'
                if '//' not in virtual and any(part in {'.', '..'} for part in virtual.split('/'))
                else 'warning'
            )
            if self._on_path_notice is not None:
                self._on_path_notice(root.root, member_path, severity, disabled)
            else:
                _LOGGER.log(
                    INFO if severity == 'info' else WARNING,
                    '%s / %s: ignored noncanonical ZIP path',
                    root.root,
                    member_path,
                )

    @staticmethod
    def _fallback_manifest(path: Path, *, archive: bool) -> everest.Manifest:
        """Build the dummy metadata Everest uses after a manifest load failure."""
        prefix = '_zip_' if archive else '_dir_'
        name = path.stem if archive else path.name
        return (
            everest.ModMetadata(
                name=f'{prefix}{name}',
                version=everest.DUMMY_VERSION,
            ),
        )

    @staticmethod
    def _find_child(root: content.ContentEntry, name: str) -> content.ContentEntry | None:
        if not root.is_dir():
            return None
        path = root.joinpath(name)
        return path if path.exists() else None

    @classmethod
    def _find_manifest(cls, root: content.ContentEntry) -> content.ContentEntry | None:
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
        self, mod_path: content.ContentEntry, *, disabled: bool = False
    ) -> dict[str, CaseFoldDict[str]]:
        try:
            dialog_dir = next(
                (
                    child
                    for child in mod_path.iterdir()
                    if child.name == content.DIALOG_DIR.name and child.is_dir()
                ),
                None,
            )
        except (OSError, BadZipFile, KeyError) as error:
            self._report_error(
                ModScanError(
                    mod_path.root,
                    content.DIALOG_DIR.as_posix(),
                    ModScanStage.DIALOG,
                    error,
                    disabled=disabled,
                )
            )
            return {}
        if dialog_dir is None:
            return {}

        def report_error(error: dialog.DialogReadError) -> None:
            self._report_error(
                ModScanError(
                    error.root,
                    error.file_path.as_posix(),
                    ModScanStage.DIALOG,
                    error.cause,
                    disabled=disabled,
                )
            )

        return dialog.read_dialogs(dialog_dir, strict=True, on_error=report_error)

    def _read_text(self, path: content.ContentEntry, *, disabled: bool = False) -> str | None:
        """Read one Mod text file, retaining decoding failures as scan diagnostics."""
        try:
            return path.read_text()
        except (UnicodeDecodeError, OSError, BadZipFile, KeyError) as error:
            self._report_error(
                ModScanError(
                    path.root, path.at.as_posix(), ModScanStage.TEXT, error, disabled=disabled
                )
            )
            return None

    def _read_map_paths(
        self, root: content.ContentEntry, *, disabled: bool
    ) -> list[content.ContentPath]:
        """Discover map files without parsing layouts, retaining any completed traversal."""
        paths: list[content.ContentPath] = []
        try:
            maps_dir = self._find_child(root, content.MAPS_DIR.name)
            if maps_dir is not None:
                for directory, dirnames, filenames in maps_dir.walk():
                    dirnames[:] = [name for name in dirnames if not name.startswith('.')]
                    for filename in filenames:
                        if filename.startswith('.'):
                            continue
                        map_path = directory / filename
                        if map_path.at.suffix == '.bin':
                            paths.append(content.ContentPath(map_path.at))
        except (OSError, BadZipFile, KeyError) as error:
            self._report_error(
                ModScanError(
                    root.root,
                    content.MAPS_DIR.as_posix(),
                    ModScanStage.MAPS,
                    error,
                    disabled=disabled,
                )
            )
        return paths
