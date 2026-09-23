from pathlib import Path

from platformdirs import user_data_path

from pist.paths import PIST_DIR, pist_dir, source_root


def test_source_checkout_uses_repository_pist_directory(tmp_path: Path) -> None:
    package_dir = tmp_path / 'src/pist'
    package_dir.mkdir(parents=True)
    (tmp_path / 'pyproject.toml').touch()

    assert source_root(package_dir) == tmp_path
    assert pist_dir(package_dir, {}) == tmp_path / '.pist'


def test_installed_package_uses_platform_data_directory(tmp_path: Path) -> None:
    package_dir = tmp_path / 'site-packages/pist'
    package_dir.mkdir(parents=True)

    assert source_root(package_dir) is None
    assert pist_dir(package_dir, {}) == user_data_path('.pist', appauthor=False)


def test_explicit_data_directory_overrides_runtime_mode(tmp_path: Path) -> None:
    configured = tmp_path / '.pist'

    assert pist_dir(tmp_path / 'installed/pist', {'PIST_DATA_DIR': str(configured)}) == configured


def test_current_checkout_keeps_existing_project_data_directory() -> None:
    root = source_root()

    assert root is not None
    assert PIST_DIR == root / '.pist'
