from pathlib import Path

from platformdirs import user_data_path

from berries.paths import BERRIES_DIR, berries_dir, source_root


def test_source_checkout_uses_repository_data_directory(tmp_path: Path) -> None:
    package_dir = tmp_path / 'berries/src/berries'
    package_dir.mkdir(parents=True)
    (tmp_path / 'pyproject.toml').touch()

    assert source_root(package_dir) == tmp_path
    assert berries_dir(package_dir, {}) == tmp_path / '.pist'


def test_installed_package_uses_berries_platform_data_directory(tmp_path: Path) -> None:
    package_dir = tmp_path / 'site-packages/berries'
    package_dir.mkdir(parents=True)

    assert source_root(package_dir) is None
    assert berries_dir(package_dir, {}) == user_data_path('berries', appauthor=False)


def test_explicit_berries_data_directory_overrides_runtime_mode(tmp_path: Path) -> None:
    configured = tmp_path / '.berries'

    assert (
        berries_dir(tmp_path / 'installed/berries', {'BERRIES_DATA_DIR': str(configured)})
        == configured
    )


def test_current_checkout_shares_repository_runtime_directory() -> None:
    root = source_root()

    assert root is not None
    assert BERRIES_DIR == root / '.pist'
