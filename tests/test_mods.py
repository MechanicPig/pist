from pathlib import Path
from struct import pack
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from pist.game.mods import (
    EverestManifest,
    InstalledMod,
    LocalMap,
    ModScanner,
    collab_journal_map_order,
    is_collab_submission_map,
    is_mod_dependency,
)


def write_zip_mod(
    mods_directory: Path, filename: str, manifest_name: str, *, collab_id: str | None = None
) -> None:
    with ZipFile(mods_directory / filename, 'w') as archive:
        archive.writestr(
            'everest.yaml',
            f'- Name: {manifest_name}\n  Version: 1.2.3\n  Dependencies:\n'
            '    - Name: RequiredDependency\n      Version: 1.0\n'
            '  OptionalDependencies:\n'
            '    - Name: OptionalDependency\n      Version: 2.0\n',
        )
        archive.writestr('Maps/Test/0_Map.bin', b'map data')
        archive.writestr(
            'Dialog/English.txt',
            'Test_0_Map=English Map\n'
            'Test_0_Map_author=by Alice\n'
            'Test_0_Map_collabcreditstags=Beginner\n',
        )
        archive.writestr('Dialog/Simplified Chinese.txt', 'Test_0_Map=中文地图\n')
        if collab_id is not None:
            archive.writestr('CollabUtils2CollabID.txt', f'{collab_id}\n')


def write_map_with_icon(path: Path, icon: str) -> None:
    """Write the smallest binary map that carries a top-level ``meta.Icon``."""
    lookup = ('Map', 'meta', 'Icon', icon)
    indices = {value: index for index, value in enumerate(lookup)}

    def element(name: str, attrs: list[bytes], children: list[bytes]) -> bytes:
        return b''.join(
            (
                pack('<H', indices[name]),
                pack('<B', len(attrs)),
                *attrs,
                pack('<H', len(children)),
                *children,
            )
        )

    meta = element('meta', [pack('<H', indices['Icon']) + b'\x05' + pack('<H', indices[icon])], [])
    root = element('Map', [], [meta])
    data = b'\x0bCELESTE MAP\x0bExample/Map' + pack('<H', len(lookup))
    data += b''.join(bytes((len(value),)) + value.encode() for value in lookup) + root
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_scanner_reads_enabled_zip_and_directory_mods(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    write_zip_mod(mods_directory, 'enabled.zip', 'EnabledZip', collab_id='TestCollab')
    write_zip_mod(mods_directory, 'disabled.zip', 'DisabledZip')
    (mods_directory / 'blacklist.txt').write_text('# generated\ndisabled.zip\n', encoding='utf-8')

    directory_mod = mods_directory / 'DirectoryMod'
    (directory_mod / 'Maps' / 'Directory').mkdir(parents=True)
    (directory_mod / 'everest.yaml').write_text('- Name: DirectoryMod\n', encoding='utf-8')
    (directory_mod / 'Maps' / 'Directory' / '0-Map.bin').write_bytes(b'map data')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert [mod.metadata_name for mod in report.mods] == ['DirectoryMod', 'EnabledZip']
    assert report.skipped_blacklisted == ['disabled.zip']
    assert report.disabled_mod_names == ['DisabledZip']
    assert report.mods[0].map_files == ['Maps/Directory/0-Map.bin']
    assert report.mods[1].map_files == ['Maps/Test/0_Map.bin']
    assert report.mods[1].collab_id == 'TestCollab'
    assert report.mods[1].maps[0].names == {'zh-cn': '中文地图', 'en': 'English Map'}
    assert report.mods[1].maps[0].author_texts == {'en': 'by Alice'}
    assert report.mods[1].maps[0].collab_credit_tags == {'en': 'Beginner'}
    assert report.mods[1].campaigns[0].names == {}
    assert report.mods[1].campaigns[0].fallback_name == 'Test'
    assert report.mods[1].campaigns[0].maps == report.mods[1].maps
    assert report.mods[1].dependencies[0].name == 'RequiredDependency'
    assert report.mods[1].optional_dependencies[0].version == '2.0'


def test_scanner_lists_disabled_root_map_mods_without_campaign_localization(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'root-map.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: RootMap\n')
        archive.writestr('Maps/RootMap.bin', b'map data')
    (mods_directory / 'blacklist.txt').write_text('root-map.zip\n', encoding='utf-8')

    mods = ModScanner(tmp_path / 'Celeste').scan_all()

    assert [(mod.metadata_name, mod.map_files, mod.maps, mod.campaigns) for mod in mods] == [
        ('RootMap', ['Maps/RootMap.bin'], [], [])
    ]


def test_scanner_keeps_enabled_root_maps_outside_campaigns(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'root-map.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: RootMap\n')
        archive.writestr('Maps/RootMap.bin', b'map data')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.campaigns == []
    assert mod.maps[0].fallback_name == 'Root Map'


def test_scanner_preserves_yaml_name_and_version_as_text(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'version.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: 123\n  Version: 1.20\n')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert report.mods[0].metadata_name == '123'
    assert report.mods[0].metadata_version == '1.20'


def test_manifest_rejects_non_string_version() -> None:
    with pytest.raises(ValidationError):
        EverestManifest.model_validate({'Name': 'Example', 'Version': 1})


def test_mod_dependency_excludes_loader_entries() -> None:
    assert not is_mod_dependency('Celeste')
    assert not is_mod_dependency('Everest')
    assert not is_mod_dependency('EverestCore')


def test_collab_submission_map_excludes_lobbies_and_gyms() -> None:
    assert is_collab_submission_map(
        LocalMap(file_path='Maps/Expert/Example.bin', dialog_key='Expert_Example')
    )
    assert not is_collab_submission_map(
        LocalMap(file_path='Maps/0-Lobbies/Prologue.bin', dialog_key='0_Lobbies_Prologue')
    )
    assert not is_collab_submission_map(
        LocalMap(file_path='Maps/Beginner Gym/Example.bin', dialog_key='Beginner_Gym_Example')
    )
    assert is_mod_dependency('FrostHelper')


def test_collab_journal_map_order_uses_numbered_map_icons(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Collab'
    maps = [
        LocalMap(file_path='Maps/Collab/Hard.bin', dialog_key='Collab_Hard'),
        LocalMap(file_path='Maps/Collab/Medium.bin', dialog_key='Collab_Medium'),
        LocalMap(file_path='Maps/Collab/ZZ-HeartSide.bin', dialog_key='Collab_ZZ_HeartSide'),
    ]
    for map_info, icon in zip(
        maps,
        (
            'areas/Collab/meters/3-hard',
            'areas/Collab/meters/2-medium',
            'areas/Collab/meters/1-easy',
        ),
        strict=True,
    ):
        write_map_with_icon(mod_dir / map_info.file_path, icon)
    mod = InstalledMod(
        source='directory',
        filename='Collab',
        path=str(mod_dir),
        metadata_name='Collab',
        metadata_version=None,
    )

    assert collab_journal_map_order(mod, maps) == [maps[1], maps[0], maps[2]]


def test_scanner_uses_base_dialog_name_for_b_and_c_sides(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'sides.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Sides\n  Version: 1.0.0\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Maps/Test/Map-B.bin', b'map data')
        archive.writestr('Maps/Test/Map-C.bin', b'map data')
        archive.writestr('Dialog/English.txt', 'Test_Map=Map Name\n')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]
    maps = mod.maps
    maps_by_file = {map_info.file_path: map_info for map_info in maps}

    assert [map_info.file_path for map_info in maps] == [
        'Maps/Test/Map.bin',
        'Maps/Test/Map-B.bin',
        'Maps/Test/Map-C.bin',
    ]
    assert maps_by_file['Maps/Test/Map-B.bin'].dialog_key == 'Test_Map'
    assert maps_by_file['Maps/Test/Map-B.bin'].side == 'B'
    assert maps_by_file['Maps/Test/Map-B.bin'].names == {'en': 'Map Name B'}
    assert maps_by_file['Maps/Test/Map-C.bin'].names == {'en': 'Map Name C'}
    assert mod.campaigns[0].dialog_key == 'Test'
    assert mod.campaigns[0].names == {}
    assert mod.campaigns[0].fallback_name == 'Test'


def test_scanner_sorts_map_paths_naturally_and_keeps_heart_side_last(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'order.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Order\n')
        for file_path in (
            'Maps/Test/10-Advanced/Map.bin',
            'Maps/Test/2-Intermediate/Map.bin',
            'Maps/Test/1-Beginner/Map-C.bin',
            'Maps/Test/1-Beginner/Map.bin',
            'Maps/Test/1-Beginner/Map-B.bin',
            'Maps/Test/1-Beginner/ZZ-HeartSide.bin',
        ):
            archive.writestr(file_path, b'map data')

    map_files = ModScanner(tmp_path / 'Celeste').scan().mods[0].map_files

    assert map_files == [
        'Maps/Test/1-Beginner/Map.bin',
        'Maps/Test/1-Beginner/Map-B.bin',
        'Maps/Test/1-Beginner/Map-C.bin',
        'Maps/Test/2-Intermediate/Map.bin',
        'Maps/Test/10-Advanced/Map.bin',
        'Maps/Test/1-Beginner/ZZ-HeartSide.bin',
    ]


def test_scanner_reads_all_supported_dialog_languages(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'languages.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Languages\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Dialog/English.txt', 'Test_Map=English Map\n')
        archive.writestr('Dialog/Japanese.txt', 'Test_Map=日本語マップ\n')

    map_info = ModScanner(tmp_path / 'Celeste').scan().mods[0].maps[0]

    assert map_info.names == {'en': 'English Map', 'ja': '日本語マップ'}


def test_scanner_uses_game_fallback_name_without_dialog(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'fallback.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Fallback\n  Version: 1.0.0\n')
        archive.writestr('Maps/Author/MicroMountain/Map.bin', b'map data')
        archive.writestr('Maps/Author/MicroMountain/Map-B.bin', b'map data')

    maps = ModScanner(tmp_path / 'Celeste').scan().mods[0].maps
    maps_by_file = {map_info.file_path: map_info for map_info in maps}

    assert maps_by_file['Maps/Author/MicroMountain/Map.bin'].names == {}
    assert maps_by_file['Maps/Author/MicroMountain/Map.bin'].base_file == (
        'Maps/Author/MicroMountain/Map.bin'
    )
    assert maps_by_file['Maps/Author/MicroMountain/Map.bin'].fallback_name == (
        'Author_Micro Mountain'
    )
    assert maps_by_file['Maps/Author/MicroMountain/Map-B.bin'].names == {}
    assert maps_by_file['Maps/Author/MicroMountain/Map-B.bin'].fallback_name == (
        'Author_Micro Mountain B'
    )


def test_scanner_groups_collab_maps_under_their_lobbies(tmp_path: Path) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'collab.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: TestCollab\n')
        archive.writestr('CollabUtils2CollabID.txt', 'TestCollab\n')
        archive.writestr('Maps/TestCollab/0-Lobbies/0-Prologue.bin', b'map data')
        archive.writestr('Maps/TestCollab/0-Lobbies/1-Beginner.bin', b'map data')
        archive.writestr('Maps/TestCollab/0-Gyms/1-Beginner.bin', b'map data')
        archive.writestr('Maps/TestCollab/1-Beginner/Map.bin', b'map data')
        archive.writestr(
            'Dialog/English.txt',
            'TestCollab_0_Lobbies_0_Prologue=Prologue\n'
            'TestCollab_0_Lobbies_1_Beginner=Beginner Lobby\n'
            'TestCollab_1_Beginner_Map=Playable Map\n',
        )

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert [map_info.file_path for map_info in mod.maps] == [
        'Maps/TestCollab/0-Gyms/1-Beginner.bin',
        'Maps/TestCollab/0-Lobbies/0-Prologue.bin',
        'Maps/TestCollab/0-Lobbies/1-Beginner.bin',
        'Maps/TestCollab/1-Beginner/Map.bin',
    ]
    assert [(campaign.kind, campaign.names['en']) for campaign in mod.campaigns] == [
        ('collab_prologue', 'Prologue'),
        ('collab_lobby', 'Beginner Lobby'),
    ]
    assert mod.campaigns[1].maps[0].names['en'] == 'Playable Map'


def test_scanner_groups_multiple_collab_levelsets_without_reading_lobby_bins(
    tmp_path: Path,
) -> None:
    mods_directory = tmp_path / 'Celeste' / 'Mods'
    mods_directory.mkdir(parents=True)
    with ZipFile(mods_directory / 'collab.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: TestCollab\n')
        archive.writestr('CollabUtils2CollabID.txt', 'TestCollab\n')
        archive.writestr('Maps/TestCollab/0-Lobbies/1-Maps.bin', b'not parsed')
        archive.writestr('Maps/TestCollab/1-Maps/Map.bin', b'not parsed')
        archive.writestr('Maps/TestCollab/1-Submissions/Submission.bin', b'not parsed')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert [
        (campaign.directory, [map_info.file_path for map_info in campaign.maps])
        for campaign in mod.campaigns
    ] == [
        ('Maps/TestCollab/1-Maps', ['Maps/TestCollab/1-Maps/Map.bin']),
        ('Maps/TestCollab/1-Submissions', ['Maps/TestCollab/1-Submissions/Submission.bin']),
    ]
