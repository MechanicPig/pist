"""Stage rule files and retain recoverable originals until a coordinated update succeeds."""

import os
from collections.abc import Generator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from shutil import copystat
from tempfile import NamedTemporaryFile


class RuleFileUpdateError(RuntimeError):
    """Rule files could not be committed or restored; diagnostics identify retained backups."""


@dataclass(slots=True)
class _FileChange:
    target: Path
    staged: Path
    backup: Path | None
    applied: bool = False


def _stage(target: Path, data: bytes, suffix: str) -> Path:
    """Prepare one complete file beside its target for same-filesystem replacement."""
    with NamedTemporaryFile(
        dir=target.parent, prefix=f'.{target.name}.', suffix=suffix, delete=False
    ) as file:
        path = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            path.unlink(missing_ok=True)
            raise
    try:
        if target.is_file():
            if suffix == '.bak':
                copystat(target, path)
            else:
                path.chmod(target.stat().st_mode)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


@contextmanager
def replace_rule_files(updates: Mapping[Path, str]) -> Generator[None]:
    """Publish complete files, restoring originals if this operation or its participant fails.

    All new files and backups are prepared before any target changes. Backups survive
    failed restoration or process termination; this is not a cross-resource crash-atomic
    transaction. The participant must roll back its own changes when it raises.
    """
    changes: list[_FileChange] = []
    keep_backups = False
    try:
        for target, text in updates.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                original = target.read_bytes()
            except FileNotFoundError:
                original = None
            data = text.encode('utf-8')
            if data == original:
                continue
            staged = _stage(target, data, '.tmp')
            try:
                backup = None if original is None else _stage(target, original, '.bak')
            except BaseException:
                staged.unlink(missing_ok=True)
                raise
            changes.append(_FileChange(target, staged, backup))
        for change in changes:
            change.staged.replace(change.target)
            change.applied = True
        yield
    except BaseException as error:
        failed: list[Path] = []
        for change in reversed(changes):
            if not change.applied:
                continue
            try:
                if change.backup is None:
                    change.target.unlink(missing_ok=True)
                else:
                    change.backup.replace(change.target)
            except OSError:
                failed.append(change.target)
        if failed:
            keep_backups = True
            backups = [
                str(change.backup)
                for change in changes
                if change.backup is not None and change.backup.exists()
            ]
            raise RuleFileUpdateError(
                f'规则更新失败（{error}），部分文件无法恢复：'
                f'{", ".join(map(str, failed))}；旧文件副本保留于：{", ".join(backups)}'
            ) from error
        if isinstance(error, OSError):
            raise RuleFileUpdateError(f'规则更新失败，原文件已保留或恢复：{error}') from error
        raise
    finally:
        for change in changes:
            with suppress(OSError):
                change.staged.unlink(missing_ok=True)
            if change.backup is not None and not keep_backups:
                with suppress(OSError):
                    change.backup.unlink(missing_ok=True)
