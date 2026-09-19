from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from pist.game.mod_path import ModPath, iter_files


def test_zip_mod_path_infers_directories_and_sorts_their_children(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Zebra/Map.bin', b'')
        archive.writestr('Maps/Alpha/Map.bin', b'')
        archive.writestr('Dialog/English.txt', b'')

    with ModPath(archive_path) as root:
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


def test_zip_mod_path_reads_root_file(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n')

    with ModPath(archive_path) as root:
        manifest = root.joinpath('everest.yaml')

        assert manifest.exists()
        assert manifest.is_file()
        assert manifest.read_text() == '- Name: Example\n'


def test_zip_mod_path_checks_missing_file_without_building_directory_index(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')

    with (
        ModPath(archive_path) as root,
        patch.object(
            ZipFile, 'infolist', side_effect=AssertionError('directory index should stay lazy')
        ),
    ):
        assert not root.joinpath('missing.txt').is_file()


def test_zip_mod_path_uses_directory_index_for_recursive_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('Maps/Example.bin', b'')
        archive.writestr('Maps/Another.bin', b'')

    with ModPath(archive_path) as root:
        list(root.iterdir())
        with patch.object(
            ZipFile, 'getinfo', side_effect=AssertionError('should use the directory index')
        ):
            assert [path.at.as_posix() for path in iter_files(root)] == [
                'Maps/Another.bin',
                'Maps/Example.bin',
            ]
