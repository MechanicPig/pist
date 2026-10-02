from collections.abc import Iterator
from pathlib import Path, PurePath, PurePosixPath
from zipfile import ZipFile, ZipInfo

import pytest
from pydantic import BaseModel

from berries.game import content
from berries.game.content import (
    ContentEntry,
    ContentPath,
    DirContentEntry,
    GameContent,
    ZipContentEntry,
)


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


@pytest.mark.parametrize('branch_count', [4, 32])
def test_zip_walk_bounds_member_visits_and_reuses_cached_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch_count: int
) -> None:
    archive_path = tmp_path / 'mod.zip'
    paths = [f'Maps/Branch{i:02d}/a/b/c/d/e/f/Map.bin' for i in range(branch_count)]
    with ZipFile(archive_path, 'w') as archive:
        for path in paths:
            archive.writestr(path, b'')
    visits = 0
    descendants = content._ZipMemberIndex._descendants

    def counted_descendants(values: tuple[str, ...], path: str) -> Iterator[str]:
        nonlocal visits
        for value in descendants(values, path):
            visits += 1
            yield value

    monkeypatch.setattr(content._ZipMemberIndex, '_descendants', staticmethod(counted_descendants))

    with ContentEntry(archive_path) as root:
        assert isinstance(root, ZipContentEntry)
        infolist = root.zip_file.infolist

        def counted_infolist() -> list[ZipInfo]:
            nonlocal visits
            infos = infolist()
            visits += len(infos)
            return infos

        monkeypatch.setattr(root.zip_file, 'infolist', counted_infolist)
        first_walk = [(directory.at, dirs, files) for directory, dirs, files in root.walk()]
        assert [
            (directory / filename).as_posix()
            for directory, _, filenames in first_walk
            for filename in filenames
        ] == paths
        # Count expensive member enumeration, not calls to cached lookup methods.
        assert visits <= 3 * len(paths)
        first_visits = visits
        second_walk = [(directory.at, dirs, files) for directory, dirs, files in root.walk()]
        assert visits == first_visits

    assert second_walk == first_walk
