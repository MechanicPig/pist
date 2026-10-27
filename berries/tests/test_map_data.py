from pathlib import Path
from struct import pack
from typing import Literal
from zipfile import ZipFile

import pytest

from berries.game import collab, map_data
from berries.game.binmap import BadMapBin
from berries.game.content import ContentPath
from berries.game.maps import Map, ModMap, VanillaMap
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
    return VanillaMap(file_path, path) if owner is None else ModMap(file_path, owner)


def _source_path(map_file: Map) -> Path:
    if isinstance(map_file, VanillaMap):
        return map_file.content_dir
    assert isinstance(map_file, ModMap)
    return map_file.mod.path


@pytest.mark.parametrize('source', ('zip', 'directory', 'game'))
def test_map_data_reads_sources_and_allows_trailing_payload(
    tmp_path: Path, source: Literal['zip', 'directory', 'game']
) -> None:
    data = b'\x0bCELESTE MAP\x07Example' + pack('<H', 1) + b'\x03Map'
    data += pack('<HBH', 0, 0, 0) + b'trailing payload'
    loaded_map = _map_source(tmp_path, source, data)

    active = map_data.load_map_data(loaded_map)
    isolated = map_data.load_map_data_from_path(_source_path(loaded_map), loaded_map.file_path)

    assert active == isolated
    assert active.package == 'Example'
    assert active.root.name == 'Map'
    assert collab.JournalRefs.from_map(loaded_map).campaign_refs == ()
    if source == 'zip':
        _source_path(loaded_map).unlink()


@pytest.mark.parametrize('source', ('zip', 'directory', 'game'))
def test_map_data_preserves_invalid_bin_diagnostics(
    tmp_path: Path, source: Literal['zip', 'directory', 'game']
) -> None:
    loaded_map = _map_source(tmp_path, source, b'not a map')
    with pytest.raises(BadMapBin):
        map_data.load_map_data(loaded_map)
    with pytest.raises(BadMapBin):
        map_data.load_map_data_from_path(_source_path(loaded_map), loaded_map.file_path)


def test_collab_reports_removed_archive(tmp_path: Path) -> None:
    loaded_map = _map_source(tmp_path, 'zip', b'map')
    _source_path(loaded_map).unlink()
    with pytest.raises(ValueError) as caught:
        collab.JournalRefs.from_map(loaded_map)
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert _source_path(loaded_map).name in str(caught.value)


def test_map_data_rejects_missing_entry(tmp_path: Path) -> None:
    loaded_map = _map_source(tmp_path, 'zip', b'map')
    assert isinstance(loaded_map, ModMap)
    missing = ModMap(ContentPath('Maps/Missing.bin'), loaded_map.mod)
    with pytest.raises(ValueError, match='Map file does not exist'):
        map_data.load_map_data(missing)
    with pytest.raises(ValueError, match='Map file does not exist'):
        map_data.load_map_data_from_path(missing.mod.path, missing.file_path)
