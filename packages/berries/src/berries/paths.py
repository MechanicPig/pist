"""Resolve packaged resources and writable Berries data paths."""

from collections.abc import Mapping
from os import environ
from pathlib import Path

from platformdirs import user_data_path

PACKAGE_DIR = Path(__file__).resolve().parent
SHARED_DATA_DIR = PACKAGE_DIR / 'data'


def source_root(package_dir: Path = PACKAGE_DIR) -> Path | None:
    """Return the repository root when Berries is running from its workspace checkout."""
    for candidate in package_dir.parents:
        expected = candidate / 'packages' / 'berries' / 'src' / 'berries'
        if package_dir == expected and (candidate / 'pyproject.toml').is_file():
            return candidate
    return None


def berries_dir(
    package_dir: Path = PACKAGE_DIR,
    environment: Mapping[str, str] = environ,
) -> Path:
    """Return the writable Berries data directory for this installation."""
    if configured := environment.get('BERRIES_DATA_DIR'):
        return Path(configured).expanduser().resolve()
    if root := source_root(package_dir):
        return root / '.pist'
    return user_data_path('berries', appauthor=False)


SOURCE_ROOT = source_root()
BERRIES_DIR = berries_dir()
