"""Catalog signatures follow physical map changes without parsing the maps."""

from pathlib import Path
from typing import Literal
from zipfile import ZipFile

import pytest

from berries.game.content import ContentPath
from berries.game.maps import ModMap, VanillaMap
from pist.catalog_fingerprint import collab_journal_icon_fingerprint, journal_fingerprint
from test_support.mod_factory import make_installed_mod


@pytest.mark.parametrize('source', ('directory', 'zip', 'game'))
def test_journal_fingerprint_changes_with_content_and_releases_sources(
    tmp_path: Path, source: Literal['directory', 'zip', 'game']
) -> None:
    info = ContentPath('Maps/Example.bin')
    path = tmp_path / ('Example.zip' if source == 'zip' else 'Content')

    def write(data: bytes) -> None:
        if source == 'zip':
            with ZipFile(path, 'w') as archive:
                archive.writestr(info.as_posix(), data)
        else:
            target = path / info.as_posix()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    write(b'first')
    owner = (
        None
        if source == 'game'
        else make_installed_mod(
            source=source,
            filename=path.name,
            path=path,
            metadata_name='Example',
            metadata_version=None,
        )
    )
    map_file = VanillaMap(info, path) if owner is None else ModMap(info, owner)
    first = journal_fingerprint(map_file)
    write(b'second')
    assert journal_fingerprint(map_file) != first
    if source == 'zip':
        path.unlink()
        with pytest.raises(ValueError) as caught:
            journal_fingerprint(map_file)
        assert isinstance(caught.value.__cause__, FileNotFoundError)
        assert path.name in str(caught.value)


def test_collab_journal_icon_fingerprint_changes_when_a_directory_map_changes(
    tmp_path: Path,
) -> None:
    map_info = ContentPath('Maps/Collab/Map.bin')
    map_path = tmp_path / map_info
    map_path.parent.mkdir(parents=True)
    map_path.write_bytes(b'first')
    mod = make_installed_mod(
        source='directory',
        filename='Collab',
        path=str(tmp_path),
        metadata_name='Collab',
        metadata_version=None,
    )

    first = collab_journal_icon_fingerprint(mod, [map_info])
    map_path.write_bytes(b'second')

    assert collab_journal_icon_fingerprint(mod, [map_info]) != first


def test_collab_journal_icon_fingerprint_uses_zip_entry_metadata(tmp_path: Path) -> None:
    map_info = ContentPath('Maps/Collab/Map.bin')
    archive_path = tmp_path / 'Collab.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr(map_info.as_posix(), b'first')
    mod = make_installed_mod(
        source='zip',
        filename='Collab.zip',
        path=str(archive_path),
        metadata_name='Collab',
        metadata_version=None,
    )

    first = collab_journal_icon_fingerprint(mod, [map_info])
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr(map_info.as_posix(), b'second')

    assert collab_journal_icon_fingerprint(mod, [map_info]) != first
