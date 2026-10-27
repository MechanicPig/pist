"""Application policy for Mod scan diagnostics and map presentation order."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError

from berries.game import campaigns
from berries.game import mods as backend
from berries.game.content import ContentPath
from berries.game.maps import split_map_side_suffix
from berries.models import FrozenModel

from .campaign_catalog import CatalogLoadResult

_NATURAL_PART_PATTERN = re.compile(r'(\d+)')
_SIDE_ORDER = {None: 0, 'B': 1, 'C': 2}


def map_sort_key(
    map_file: ContentPath,
) -> tuple[int, tuple[tuple[int, int | str], ...], int, str]:
    """Order display paths naturally, with A/B/C sides together and HeartSide last."""
    base_file, side_suffix = split_map_side_suffix(map_file)
    base_path = base_file.with_suffix('').as_posix()
    natural_key = tuple(
        (0, int(part)) if part.isdecimal() else (1, part.casefold())
        for part in _NATURAL_PART_PATTERN.split(base_path)
    )
    return (
        int(base_file.stem == 'ZZ-HeartSide'),
        natural_key,
        _SIDE_ORDER[side_suffix],
        map_file.as_posix().casefold(),
    )


class ModScanDiagnostic(FrozenModel):
    """One application-owned scan notice or continued-failure diagnostic."""

    mod_filename: str
    file_path: str
    message: str
    stage: backend.ModScanStage = backend.ModScanStage.PACKAGE
    disabled: bool = False
    severity: Literal['info', 'warning'] = 'warning'

    @classmethod
    def from_error(cls, error: backend.ModScanError) -> Self:
        """Render a callback failure without retaining its exception or open resources."""
        detail = (
            _validation_error_detail(error.cause)
            if isinstance(error.cause, ValidationError)
            else str(error.cause)
        )
        warning = cls(
            mod_filename=error.path.name,
            file_path=error.file_path,
            message=detail,
            stage=error.stage,
            disabled=error.disabled,
        )
        return warning.model_copy(update={'message': warning_text(warning)})


class ModScanReport(backend.ModScanReport):
    """Pist's exportable scan inventory plus application-collected diagnostics."""

    warnings: list[ModScanDiagnostic] = Field(default_factory=list)
    infos: list[ModScanDiagnostic] = Field(default_factory=list)

    @property
    def diagnostics(self) -> tuple[str, ...]:
        """Return source-qualified messages with their explicit severity."""
        return tuple(
            f'[{message.severity}] Mod 扫描 {message.mod_filename}/{message.file_path}：{message.message}'
            for message in (*self.warnings, *self.infos)
        )

    def load_catalog(self, game_dir: Path) -> CatalogLoadResult:
        """Compose scan diagnostics with the catalog's own content-loading diagnostics."""
        messages = list(self.diagnostics)
        catalog = campaigns.CampaignCatalog.from_game_dir(
            game_dir, scan_report=self, on_error=lambda error: messages.append(str(error))
        )
        return CatalogLoadResult.from_catalog(catalog, diagnostics=messages)


def _validation_error_detail(error: ValidationError) -> str:
    """Format boundary validation failures without Pydantic's developer metadata."""
    details: list[str] = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = '.'.join(str(part) for part in issue['loc'])
        message = issue['msg'].removeprefix('Value error, ')
        details.append(f'{location}: {message}' if location else message)
    return '; '.join(details)


def warning_text(warning: ModScanDiagnostic) -> str:
    """Render structured scan diagnostics for Pist's Chinese interface."""
    prefix = '禁用 Mod' if warning.disabled else 'Mod'
    match warning.stage:
        case backend.ModScanStage.MANIFEST:
            return f'{prefix} 元数据无效：{warning.message}'
        case backend.ModScanStage.ZIP_MEMBER:
            return f'{prefix} 包含无效成员路径，已忽略。'
        case backend.ModScanStage.DIALOG:
            return f'Dialog 读取失败：{warning.message}'
        case backend.ModScanStage.TEXT:
            return f'文本读取失败：{warning.message}'
        case _:
            return f'{prefix} 读取失败：{warning.message}'


class ModScanner:
    """Compose the game scanner with Pist's recover-and-report and sorting policy."""

    def __init__(
        self,
        game_dir: Path,
        *,
        whitelist_path: Path | None = None,
        temporary_blacklist_path: Path | None = None,
        whitelist_full_override: bool = False,
    ) -> None:
        self._scanner = backend.ModScanner(
            game_dir,
            whitelist_path=whitelist_path,
            temporary_blacklist_path=temporary_blacklist_path,
            whitelist_full_override=whitelist_full_override,
            on_error=self._collect_error,
            on_path_notice=self._collect_path_notice,
        )
        self._warnings: list[ModScanDiagnostic] = []
        self._infos: list[ModScanDiagnostic] = []

    def _collect_error(self, error: backend.ModScanError) -> None:
        """Record one failure and allow the game reader to continue."""
        self._warnings.append(ModScanDiagnostic.from_error(error))

    def _collect_path_notice(
        self, path: Path, file_path: str, severity: Literal['info', 'warning'], disabled: bool
    ) -> None:
        if '//' in file_path.replace('\\', '/'):
            message = '包含连续斜杠，可能造成游戏查询漂移；Pist 不支持此路径，已忽略。'
        elif severity == 'info':
            message = '包含 . 或 .. 路径段，按 Everest 规则忽略。'
        else:
            message = '包含不支持的成员路径，已忽略。'
        diagnostic = ModScanDiagnostic(
            mod_filename=path.name,
            file_path=file_path,
            message=message,
            stage=backend.ModScanStage.ZIP_MEMBER,
            severity=severity,
            disabled=disabled,
        )
        (self._infos if severity == 'info' else self._warnings).append(diagnostic)

    def scan(
        self, *, on_progress: Callable[[backend.ModScanProgress], None] | None = None
    ) -> ModScanReport:
        """Read enabled packages and apply application-only ordering and messages."""
        self._warnings.clear()
        self._infos.clear()
        report = self._scanner.scan(on_progress=on_progress)
        for mod in report.mods:
            mod.map_files.sort(key=map_sort_key)
        return ModScanReport(
            mods_dir=report.mods_dir,
            disabled_filenames=report.disabled_filenames,
            disabled_mod_names=report.disabled_mod_names,
            mods=report.mods,
            warnings=self._warnings.copy(),
            infos=self._infos.copy(),
        )
