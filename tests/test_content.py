from pathlib import Path, PurePath, PurePosixPath
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from berries.game.content import (
    ContentEntry,
    ContentPath,
    DirContentEntry,
    GameContent,
    ZipContentEntry,
)
from pydantic import BaseModel


class _ContentPathModel(BaseModel):
    path: ContentPath


def test_content_path_normalizes_everest_separators_without_folding_case() -> None:
    path = ContentPath(r'Maps\Example\Map.bin')

    assert path == ContentPath('Maps/Example/Map.bin')
    assert path.parts == ('Maps', 'Example', 'Map.bin')
    assert path.parent == ContentPath('Maps/Example')
    assert path.name == 'Map.bin'
    assert path.stem == 'Map'
    assert path.suffix == '.bin'
    assert path != ContentPath('maps/Example/Map.bin')


@pytest.mark.parametrize(
    'value',
    [
        '/Maps/Test.bin',
        'Maps//Test.bin',
        'Maps/../Test.bin',
        r'C:\Maps\Test.bin',
        'C:/Maps/Test.bin',
        'C:Maps/Test.bin',
    ],
)
def test_content_path_rejects_noncanonical_paths(value: str) -> None:
    with pytest.raises(ValueError):
        ContentPath(value)


def test_content_path_validates_and_serializes_as_a_string() -> None:
    model = _ContentPathModel.model_validate({'path': r'Maps\Example.bin'})

    assert isinstance(model.path, ContentPath)
    assert model.model_dump(mode='json') == {'path': 'Maps/Example.bin'}


def test_game_content_opens_its_directory_as_a_content_tree(tmp_path: Path) -> None:
    content_dir = tmp_path / 'Content'
    map_path = content_dir / 'Maps' / 'Example.bin'
    map_path.parent.mkdir(parents=True)
    map_path.write_bytes(b'map')

    with GameContent(content_dir).open() as root:
        map_entry = root.joinpath('Maps/Example.bin')

        assert isinstance(map_entry, DirContentEntry)
        assert map_entry.path == map_path
        assert ContentPath(root.at) == ContentPath()
        assert isinstance(map_entry.at, PurePath)
        assert not isinstance(map_entry.at, ContentPath)
        assert ContentPath(map_entry.at) == ContentPath('Maps/Example.bin')
        assert map_entry.read_bytes() == b'map'
        with pytest.raises(ValueError, match='remain within their root'):
            root.joinpath('../outside.bin')


def test_zip_content_entry_infers_directories_and_sorts_their_children(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Zebra/Map.bin', b'')
        archive.writestr('Maps/Alpha/Map.bin', b'')
        archive.writestr('Dialog/English.txt', b'')

    with ContentEntry(archive_path) as root:
        assert isinstance(root, ZipContentEntry)
        assert root.zip_file.filename is not None
        assert Path(root.zip_file.filename) == archive_path
        maps = root.joinpath('Maps')

        assert ContentPath(root.at) == ContentPath()
        assert isinstance(maps.at, PurePosixPath)
        assert not isinstance(maps.at, ContentPath)
        assert ContentPath(maps.at) == ContentPath('Maps')
        assert [path.at.as_posix() for path in root.iterdir()] == ['Dialog', 'Maps']
        assert maps.exists()
        assert maps.is_dir()
        assert [path.at.as_posix() for path in maps.iterdir()] == [
            'Maps/Alpha',
            'Maps/Zebra',
        ]
        assert [
            (directory.at.as_posix(), dirnames, filenames)
            for directory, dirnames, filenames in maps.walk()
        ] == [
            ('Maps', ['Alpha', 'Zebra'], []),
            ('Maps/Alpha', [], ['Map.bin']),
            ('Maps/Zebra', [], ['Map.bin']),
        ]
        assert [
            (directory / filename).at.as_posix()
            for directory, _, filenames in maps.walk()
            for filename in filenames
        ] == [
            'Maps/Alpha/Map.bin',
            'Maps/Zebra/Map.bin',
        ]


def test_zip_content_entry_reads_root_file(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n')

    with ContentEntry(archive_path) as root:
        manifest = root.joinpath('everest.yaml')

        assert manifest.exists()
        assert manifest.is_file()
        assert manifest.read_text() == '- Name: Example\n'


def test_zip_content_entry_checks_missing_file_from_flat_index(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')

    with ContentEntry(archive_path) as root:
        assert not root.joinpath('missing.txt').is_file()


def test_zip_content_entry_walk_does_not_recurse_through_iterdir(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')
        archive.writestr('Maps/Another.bin', b'')

    with (
        ContentEntry(archive_path) as root,
        patch.object(
            ZipContentEntry,
            'iterdir',
            side_effect=AssertionError('walk should use the flat member index'),
        ),
    ):
        assert [
            (directory / filename).at.as_posix()
            for directory, _, filenames in root.walk()
            for filename in filenames
        ] == [
            'Maps/Another.bin',
            'Maps/Example.bin',
        ]


def test_zip_content_entry_reuses_cached_walk_adjacency(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')

    with ContentEntry(archive_path) as root:
        assert isinstance(root, ZipContentEntry)
        first_walk = [
            (directory.at, dirnames, filenames) for directory, dirnames, filenames in root.walk()
        ]
        with patch.object(
            root.zip_file,
            'member_index',
            side_effect=AssertionError('walk should reuse cached adjacency'),
        ):
            second_walk = [
                (directory.at, dirnames, filenames)
                for directory, dirnames, filenames in root.walk()
            ]

    assert second_walk == first_walk
