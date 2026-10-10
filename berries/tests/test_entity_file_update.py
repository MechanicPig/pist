import os
from pathlib import Path

import pytest

from berries.entities._file_update import RuleFileUpdateError, replace_rule_files


def test_preparation_failure_leaves_all_originals_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / 'first.toml'
    second = tmp_path / 'second.toml'
    first.write_text('first', encoding='utf-8')
    second.write_text('second', encoding='utf-8')

    def fail_sync(_fd: int) -> None:
        raise OSError('simulated staging failure')

    monkeypatch.setattr(os, 'fsync', fail_sync)
    with (
        pytest.raises(RuleFileUpdateError, match='staging failure'),
        replace_rule_files({first: 'changed', second: 'changed'}),
    ):
        pytest.fail('participant must not run before files are prepared')
    assert first.read_text(encoding='utf-8') == 'first'
    assert second.read_text(encoding='utf-8') == 'second'
    assert not tuple(tmp_path.glob('.*'))


def test_restoration_failure_keeps_original_backup_and_reports_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / 'rules.toml'
    target.write_text('original', encoding='utf-8')
    replace = Path.replace

    def replace_file(path: Path, destination: Path) -> Path:
        if path.suffix == '.bak':
            raise PermissionError('simulated restoration failure')
        return replace(path, destination)

    monkeypatch.setattr(Path, 'replace', replace_file)
    with pytest.raises(RuleFileUpdateError) as caught, replace_rule_files({target: 'changed'}):
        raise RuntimeError('simulated participant failure')
    backups = tuple(tmp_path.glob('.*.bak'))
    assert len(backups) == 1
    assert backups[0].read_text(encoding='utf-8') == 'original'
    assert str(backups[0]) in str(caught.value)
    assert 'participant failure' in str(caught.value)
    assert not tuple(tmp_path.glob('.*.tmp'))


def test_replacement_changes_modification_time(tmp_path: Path) -> None:
    target = tmp_path / 'rules.toml'
    target.write_text('original', encoding='utf-8')
    old_time = 1_000_000_000
    os.utime(target, (old_time, old_time))
    with replace_rule_files({target: 'changed'}):
        pass
    assert target.stat().st_mtime > old_time


def test_participant_failure_removes_new_file(tmp_path: Path) -> None:
    target = tmp_path / 'new.toml'
    with (
        pytest.raises(ValueError, match='participant failure'),
        replace_rule_files({target: 'new'}),
    ):
        raise ValueError('participant failure')
    assert not target.exists()
    assert not tuple(tmp_path.glob('.*'))
