"""Resolve packaged resources and writable Pist data paths."""

from collections.abc import Mapping
from os import environ
from pathlib import Path

from platformdirs import user_data_path

PKG_DIR = Path(__file__).resolve().parent
SHARED_DATA_DIR = PKG_DIR / 'data'


def source_root(pkg_dir: Path = PKG_DIR) -> Path | None:
    """Return the repository root when Pist is running from a source checkout."""
    candidate = pkg_dir.parent.parent.parent
    if pkg_dir == candidate / 'pist' / 'src' / 'pist' and (candidate / 'pyproject.toml').is_file():
        return candidate
    return None


def pist_dir(
    pkg_dir: Path = PKG_DIR,
    environment: Mapping[str, str] = environ,
) -> Path:
    """Return the writable ``.pist`` directory for this installation."""
    if configured := environment.get('PIST_DATA_DIR'):
        return Path(configured).expanduser().resolve()
    if root := source_root(pkg_dir):
        return root / '.pist'
    return user_data_path('.pist', appauthor=False)


SOURCE_ROOT = source_root()
PIST_DIR = pist_dir()
