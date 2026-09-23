from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from pydantic import BaseModel

from pist.game.content import ContentEntry, ContentPath, GameContent, iter_files


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
        assert root.joinpath('Maps/Example.bin').read_bytes() == b'map'


def test_zip_content_entry_infers_directories_and_sorts_their_children(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Zebra/Map.bin', b'')
        archive.writestr('Maps/Alpha/Map.bin', b'')
        archive.writestr('Dialog/English.txt', b'')

    with ContentEntry(archive_path) as root:
        maps = root.joinpath('Maps')

        assert [path.at.as_posix() for path in root.iterdir()] == ['Dialog', 'Maps']
        assert maps.exists()
        assert maps.is_dir()
        assert [path.at.as_posix() for path in maps.iterdir()] == [
            'Maps/Alpha',
            'Maps/Zebra',
        ]
        assert [path.at.as_posix() for path in iter_files(maps)] == [
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


def test_zip_content_entry_checks_missing_file_without_building_directory_index(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')

    with (
        ContentEntry(archive_path) as root,
        patch.object(
            ZipFile, 'infolist', side_effect=AssertionError('directory index should stay lazy')
        ),
    ):
        assert not root.joinpath('missing.txt').is_file()


def test_zip_content_entry_uses_directory_index_for_recursive_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')
        archive.writestr('Maps/Another.bin', b'')

    with ContentEntry(archive_path) as root:
        list(root.iterdir())
        with patch.object(
            ZipFile, 'getinfo', side_effect=AssertionError('should use the directory index')
        ):
            assert [path.at.as_posix() for path in iter_files(root)] == [
                'Maps/Another.bin',
                'Maps/Example.bin',
            ]
