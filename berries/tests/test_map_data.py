from pathlib import Path
from struct import pack
from typing import Literal
from zipfile import ZipFile

import pytest

from berries.game import collab, map_data
from berries.game.binmap import BadMapBin
from berries.game.content import ContentPath, GameContent
from berries.game.levels import Map
from berries.game.maps import MapInfo
from test_support.mod_factory import make_installed_mod


def _map_source(tmp_path: Path, source: Literal['zip', 'directory', 'game'], data: bytes) -> Map:
    file_path = ContentPath('Maps/Example.bin')
    if source == 'zip':
        path = tmp_path / 'Example.zip'
        with ZipFile(path, 'w') as archive:
            archive.writestr(file_path.as_posix(), data)
    else:
        path = tmp_path / 'Content'
        target = path / file_path.as_posix()
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
    owner = (
        GameContent(path)
        if source == 'game'
        else make_installed_mod(
            source=source,
            filename=path.name,
            path=path,
            metadata_name='Example',
            metadata_version=None,
        )
    )
    return Map(MapInfo(file_path=file_path), owner)


@pytest.mark.parametrize('source', ('zip', 'directory', 'game'))
def test_map_data_reads_sources_and_allows_trailing_payload(
    tmp_path: Path, source: Literal['zip', 'directory', 'game']
) -> None:
    data = b'\x0bCELESTE MAP\x07Example' + pack('<H', 1) + b'\x03Map'
    data += pack('<HBH', 0, 0, 0) + b'trailing payload'
    loaded_map = _map_source(tmp_path, source, data)

    active = map_data.load_map_data(loaded_map)
    isolated = map_data.load_map_data_from_path(loaded_map.content.path, loaded_map.file_path)

    assert active == isolated
    assert active.package == 'Example'
    assert active.root.name == 'Map'
    assert collab.journal_references(loaded_map).campaign_refs == ()
    assert collab.journal_fingerprint(loaded_map)
    if source == 'zip':
        loaded_map.content.path.unlink()


@pytest.mark.parametrize('source', ('zip', 'directory', 'game'))
def test_map_data_preserves_invalid_bin_diagnostics(
    tmp_path: Path, source: Literal['zip', 'directory', 'game']
) -> None:
    loaded_map = _map_source(tmp_path, source, b'not a map')
    with pytest.raises(BadMapBin):
        map_data.load_map_data(loaded_map)
    with pytest.raises(BadMapBin):
        map_data.load_map_data_from_path(loaded_map.content.path, loaded_map.file_path)


@pytest.mark.parametrize('fingerprint', (False, True))
def test_collab_reports_removed_archive(tmp_path: Path, fingerprint: bool) -> None:
    loaded_map = _map_source(tmp_path, 'zip', b'map')
    loaded_map.content.path.unlink()
    with pytest.raises(ValueError) as caught:
        if fingerprint:
            collab.journal_fingerprint(loaded_map)
        else:
            collab.journal_references(loaded_map)
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert loaded_map.content.path.name in str(caught.value)


def test_map_data_rejects_missing_entry(tmp_path: Path) -> None:
    loaded_map = _map_source(tmp_path, 'zip', b'map')
    missing = Map(MapInfo(file_path=ContentPath('Maps/Missing.bin')), loaded_map.content)
    with pytest.raises(ValueError, match='Map file does not exist'):
        map_data.load_map_data(missing)
    with pytest.raises(ValueError, match='Map file does not exist'):
        map_data.load_map_data_from_path(missing.content.path, missing.file_path)
