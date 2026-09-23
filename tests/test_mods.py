from pathlib import Path
from struct import pack
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from pist.game.content import BadContentEntry, ContentEntry, ContentPath
from pist.game.everest import EverestModMetadata, Version
from pist.game.maps import MapInfo
from pist.game.mods import (
    DirMod,
    ModScanner,
    ModScanReport,
    ZipMod,
    collab_journal_icon_fingerprint,
    collab_journal_map_order,
    collab_journal_map_order_from_icons,
    is_collab_submission_map,
    is_mod_dependency,
)
from tests.mod_factory import make_installed_mod


def _paths(*values: str) -> list[ContentPath]:
    return [ContentPath(value) for value in values]


def test_map_info_requires_a_maps_bin_content_path() -> None:
    map_info = MapInfo(file_path=r'Maps\Test\Map.bin')

    assert map_info.file_path == ContentPath('Maps/Test/Map.bin')
    assert isinstance(map_info.file_path, ContentPath)
    with pytest.raises(ValidationError):
        MapInfo(file_path='Dialog/Test.txt')


def write_zip_mod(
    mods_dir: Path, filename: str, manifest_name: str, *, collab_id: str | None = None
) -> None:
    with ZipFile(mods_dir / filename, 'w') as archive:
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
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    write_zip_mod(mods_dir, 'enabled.zip', 'EnabledZip', collab_id='TestCollab')
    write_zip_mod(mods_dir, 'disabled.zip', 'DisabledZip')
    write_zip_mod(mods_dir, 'ignored.ZIP', 'IgnoredZip')
    (mods_dir / 'blacklist.txt').write_text('# generated\ndisabled.zip\n', encoding='utf-8')

    cache = mods_dir / 'Cache'
    cache.mkdir()
    (cache / 'everest.yaml').write_text('- Name: Cache\n', encoding='utf-8')

    directory_mod = mods_dir / 'DirectoryMod'
    (directory_mod / 'Maps' / 'Directory').mkdir(parents=True)
    (directory_mod / 'everest.yaml').write_text('- Name: DirectoryMod\n', encoding='utf-8')
    (directory_mod / 'Maps' / 'Directory' / '0-Map.bin').write_bytes(b'map data')

    scanner = ModScanner(tmp_path / 'Celeste')
    preparation = scanner.prepare()
    report = scanner.build_report(
        (
            mod
            for candidate in preparation.candidates
            if (mod := scanner.scan_mod(candidate)) is not None
        ),
        (scanner.scan_disabled_mod(candidate) for candidate in preparation.disabled_candidates),
    )

    assert [mod.metadata_name for mod in report.mods] == ['EnabledZip', 'DirectoryMod']
    assert isinstance(report.mods[0], ZipMod)
    assert isinstance(report.mods[1], DirMod)
    assert report.disabled_filenames == ['disabled.zip']
    assert report.disabled_mod_names == ['DisabledZip']
    assert report.mods[0].map_files == _paths('Maps/Test/0_Map.bin')
    assert report.mods[1].map_files == _paths('Maps/Directory/0-Map.bin')
    assert report.mods[0].collab_id == 'TestCollab'
    assert report.mods[0].dialogs == {
        'en': {
            'test_0_map': 'English Map',
            'test_0_map_author': 'by Alice',
            'test_0_map_collabcreditstags': 'Beginner',
        },
        'zh-cn': {'test_0_map': '中文地图'},
    }
    assert next(report.mods[0].iter_dependencies()).name == 'RequiredDependency'
    assert next(report.mods[0].iter_optional_dependencies()).version == Version.parse('2.0')
    assert [candidate.name for candidate in preparation.candidates] == [
        'enabled.zip',
        'DirectoryMod',
    ]
    assert [candidate.name for candidate in preparation.disabled_candidates] == ['disabled.zip']

    restored = ModScanReport.model_validate_json(report.model_dump_json())
    assert isinstance(restored.mods[0], ZipMod)
    assert isinstance(restored.mods[1], DirMod)


def test_scanner_warns_and_ignores_invalid_zip_member_paths(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    write_zip_mod(mods_dir, 'valid.zip', 'Valid')
    with ZipFile(mods_dir / 'valid.zip', 'a') as archive:
        archive.writestr('../unrelated.txt', 'ignored')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert [mod.metadata_name for mod in report.mods] == ['Valid']
    assert report.mods[0].map_files == _paths('Maps/Test/0_Map.bin')
    assert [warning.model_dump() for warning in report.warnings] == [
        {
            'mod_filename': 'valid.zip',
            'file_path': '../unrelated.txt',
            'message': 'Mod 包含无效成员路径，已忽略。',
        }
    ]


def test_scanner_preserves_all_metadata_entries_for_one_package(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'bundle.zip', 'w') as archive:
        archive.writestr(
            'everest.yaml',
            '- Name: Primary\n'
            '  Version: 1.0.0\n'
            '  Dependencies:\n'
            '    - Name: PrimaryDependency\n'
            '- Name: Secondary\n'
            '  Version: 2.0.0\n'
            '  DLL: bin/Secondary.dll\n'
            '  Dependencies:\n'
            '    - Name: SecondaryDependency\n',
        )
    with ZipFile(mods_dir / 'disabled-bundle.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: DisabledPrimary\n- Name: DisabledSecondary\n')
    (mods_dir / 'blacklist.txt').write_text('disabled-bundle.zip\n', encoding='utf-8')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert len(report.mods) == 1
    mod = report.mods[0]
    assert mod.metadata_name == 'Primary'
    assert mod.metadata_version == '1.0.0'
    assert [metadata.name for metadata in mod.manifest] == ['Primary', 'Secondary']
    assert mod.manifest[1].dll == 'bin/Secondary.dll'
    assert [dependency.name for dependency in mod.iter_dependencies()] == [
        'PrimaryDependency',
        'SecondaryDependency',
    ]
    assert report.disabled_mod_names == ['DisabledPrimary', 'DisabledSecondary']


def test_scanner_matches_everest_whitelist_and_temporary_blacklist(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    for filename in ('enabled.zip', 'blacklisted.zip', 'other.zip'):
        (mods_dir / filename).write_bytes(b'')
    (mods_dir / 'blacklist.txt').write_text('blacklisted.zip\n', encoding='utf-8')
    (mods_dir / 'session-blacklist.txt').write_text('enabled.zip\n', encoding='utf-8')
    (mods_dir / 'session-whitelist.txt').write_text('blacklisted.zip\n', encoding='utf-8')

    scanner = ModScanner(
        tmp_path / 'Celeste',
        whitelist_path=Path('session-whitelist.txt'),
        temporary_blacklist_path=Path('session-blacklist.txt'),
    )
    preparation = scanner.prepare()

    assert [candidate.name for candidate in preparation.candidates] == [
        'blacklisted.zip',
        'other.zip',
    ]
    assert [candidate.name for candidate in preparation.disabled_candidates] == ['enabled.zip']

    full_override = ModScanner(
        tmp_path / 'Celeste',
        whitelist_path=Path('session-whitelist.txt'),
        temporary_blacklist_path=Path('session-blacklist.txt'),
        whitelist_full_override=True,
    ).prepare()

    assert [candidate.name for candidate in full_override.candidates] == ['blacklisted.zip']
    assert [candidate.name for candidate in full_override.disabled_candidates] == [
        'enabled.zip',
        'other.zip',
    ]


def test_scanner_matches_everest_platform_and_archive_case_rules(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'archive.zip', 'w') as archive:
        archive.writestr('EVEREST.YAML', '- Name: Archive\n')
    uppercase_zip = mods_dir / 'uppercase.ZIP'
    uppercase_zip.write_bytes(b'')

    directory = mods_dir / 'directory'
    directory.mkdir()
    (directory / 'EVEREST.YAML').write_text('- Name: Directory\n', encoding='utf-8')

    with ContentEntry(mods_dir / 'archive.zip') as mod_path:
        assert (mod_path / 'EVEREST.YAML').is_file()
        assert not (mod_path / 'everest.yaml').exists()
    assert [mod.metadata_name for mod in ModScanner(tmp_path / 'Celeste').scan().mods] == (
        ['Directory'] if (directory / 'everest.yaml').is_file() else []
    )
    with pytest.raises(BadContentEntry):
        ContentEntry(uppercase_zip)


def test_scanner_requires_everests_exact_virtual_bin_extension(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    write_zip_mod(mods_dir, 'extension.zip', 'Extension')
    with ZipFile(mods_dir / 'extension.zip', 'a') as archive:
        archive.writestr('Maps/Test/Ignored.BIN', b'map data')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert report.mods[0].map_files == _paths('Maps/Test/0_Map.bin')


def test_scanner_lists_disabled_root_map_mods_without_reading_dialog(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'root-map.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: RootMap\n')
        archive.writestr('Maps/RootMap.bin', b'map data')
    (mods_dir / 'blacklist.txt').write_text('root-map.zip\n', encoding='utf-8')

    mods = ModScanner(tmp_path / 'Celeste').scan_all()

    assert [(mod.metadata_name, mod.map_files, mod.dialogs) for mod in mods] == [
        ('RootMap', _paths('Maps/RootMap.bin'), {})
    ]


def test_scanner_keeps_enabled_root_maps_outside_campaigns(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'root-map.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: RootMap\n')
        archive.writestr('Maps/RootMap.bin', b'map data')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.map_files == _paths('Maps/RootMap.bin')


def test_scanner_preserves_yaml_name_and_version_as_text(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'version.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: 123\n  Version: 1.20\n')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert report.mods[0].metadata_name == '123'
    assert report.mods[0].metadata_version == '1.20'


def test_scanner_falls_back_to_everest_yml_manifest(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'fallback.zip', 'w') as archive:
        archive.writestr('everest.yml', '- Name: Fallback\n')
    with ZipFile(mods_dir / 'preferred.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Preferred\n')
        archive.writestr('everest.yml', '- Name: Fallback\n')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert [mod.metadata_name for mod in report.mods] == ['Fallback', 'Preferred']


def test_scanner_warns_and_skips_an_undecodable_manifest(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'broken.zip', 'w') as archive:
        archive.writestr('everest.yaml', b'\xb1')
    with ZipFile(mods_dir / 'working.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Working\n')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert [mod.metadata_name for mod in report.mods] == ['Working']
    assert [warning.model_dump() for warning in report.warnings] == [
        {
            'mod_filename': 'broken.zip',
            'file_path': 'everest.yaml',
            'message': '无法以 UTF-8 解码：invalid start byte',
        }
    ]


def test_scanner_warns_but_keeps_a_disabled_mod_with_invalid_metadata(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'broken.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Broken\n  Version: 1\n')
    with ZipFile(mods_dir / 'working.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Working\n')
    (mods_dir / 'blacklist.txt').write_text('broken.zip\n', encoding='utf-8')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert [mod.metadata_name for mod in report.mods] == ['Working']
    assert report.disabled_filenames == ['broken.zip']
    assert report.disabled_mod_names == []
    assert len(report.warnings) == 1
    assert report.warnings[0].mod_filename == 'broken.zip'
    assert report.warnings[0].file_path == 'everest.yaml'
    assert report.warnings[0].message.startswith('禁用 Mod 元数据无效：')


def test_scanner_warns_and_crawls_an_enabled_mod_with_invalid_metadata(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'broken.zip', 'w') as archive:
        archive.writestr(
            'everest.yaml',
            '- Name: Broken\n  Version: v1.1\n  Author: Ignored by Pist\n',
        )
        archive.writestr('Maps/Broken/Map.bin', b'map data')
    with ZipFile(mods_dir / 'working.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Working\n')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert [mod.metadata_name for mod in report.mods] == ['_zip_broken', 'Working']
    assert report.mods[0].metadata_version == '0.0.0-dummy'
    assert report.mods[0].map_files == _paths('Maps/Broken/Map.bin')
    assert len(report.warnings) == 1
    assert report.warnings[0].mod_filename == 'broken.zip'
    assert report.warnings[0].file_path == 'everest.yaml'
    assert report.warnings[0].message.startswith('Mod 元数据无效：')


def test_scanner_warns_and_ignores_an_undecodable_dialog(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'dialog.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Dialog\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Dialog/English.txt', b'\xb1')

    report = ModScanner(tmp_path / 'Celeste').scan()

    assert report.mods[0].dialogs == {}
    assert report.warnings[0].mod_filename == 'dialog.zip'
    assert report.warnings[0].file_path == 'Dialog/English.txt'


def test_manifest_rejects_non_string_version() -> None:
    with pytest.raises(ValidationError):
        EverestModMetadata.model_validate({'Name': 'Example', 'Version': 1})


def test_manifest_ignores_unmatched_metadata_fields_like_everest() -> None:
    metadata = EverestModMetadata.model_validate(
        {'Name': 'Example', 'Version': '1.0', 'Author': 'Ignored'}
    )

    assert metadata.name == 'Example'


@pytest.mark.parametrize('value', ('1', '1.2.3.4.5', '1.a', '1.0+build', '2147483648.0'))
def test_manifest_rejects_invalid_everest_version(value: str) -> None:
    with pytest.raises(ValidationError):
        EverestModMetadata.model_validate({'Name': 'Example', 'Version': value})


def test_version_keeps_display_text_and_compares_numeric_prefixes() -> None:
    installed = Version.parse('1.20.0-preview')

    assert str(installed) == '1.20.0-preview'
    assert installed.is_compatible_with(Version.parse('1.2'))


def test_version_preserves_missing_system_version_components_for_dependencies() -> None:
    assert not Version.parse('1.2').is_compatible_with(Version.parse('1.2.0'))
    assert not Version.parse('1.2.0').is_compatible_with(Version.parse('1.2.0.0'))
    assert Version.parse('1.2.0.0').is_compatible_with(Version.parse('1.2.0'))
    assert Version.parse('1.3').is_compatible_with(Version.parse('1.2.999.999'))


def test_manifest_serializes_a_version_as_its_original_text() -> None:
    metadata = EverestModMetadata.model_validate({'Name': 'Example', 'Version': '1.20.0-preview'})

    assert metadata.model_dump(mode='json')['version'] == '1.20.0-preview'


def test_installed_mod_requires_a_manifest_entry() -> None:
    with pytest.raises(ValidationError):
        ZipMod(filename='Example.zip', path=Path('Example.zip'), manifest=())


def test_mod_dependency_excludes_loader_entries() -> None:
    assert not is_mod_dependency('Celeste')
    assert not is_mod_dependency('Everest')
    assert not is_mod_dependency('EverestCore')
    assert is_mod_dependency('EVEREST')


def test_collab_submission_map_excludes_lobbies_and_gyms() -> None:
    assert is_collab_submission_map(MapInfo(file_path='Maps/Expert/Example.bin'))
    assert not is_collab_submission_map(MapInfo(file_path='Maps/0-Lobbies/Prologue.bin'))
    assert not is_collab_submission_map(MapInfo(file_path='Maps/Beginner Gym/Example.bin'))
    assert is_mod_dependency('FrostHelper')


def test_collab_journal_map_order_uses_numbered_map_icons(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Collab'
    maps = [
        MapInfo(file_path='Maps/Collab/Hard.bin'),
        MapInfo(file_path='Maps/Collab/Medium.bin'),
        MapInfo(file_path='Maps/Collab/ZZ-HeartSide.bin'),
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
    mod = make_installed_mod(
        source='directory',
        filename='Collab',
        path=str(mod_dir),
        metadata_name='Collab',
        metadata_version=None,
    )

    assert collab_journal_map_order(mod, maps) == [maps[1], maps[0], maps[2]]


def test_collab_journal_order_requires_exact_heartside_name() -> None:
    heartside_named_in_lowercase = MapInfo(
        file_path='Maps/Collab/zz-heartside.bin',
    )
    ordinary_map = MapInfo(file_path='Maps/Collab/Map.bin')

    ordered = collab_journal_map_order_from_icons(
        (heartside_named_in_lowercase, ordinary_map),
        {
            heartside_named_in_lowercase.file_path: 'Maps/Collab/1-HeartSide',
            ordinary_map.file_path: 'Maps/Collab/2-Map',
        },
    )

    assert ordered == [heartside_named_in_lowercase, ordinary_map]


def test_collab_journal_icon_fingerprint_changes_when_a_directory_map_changes(
    tmp_path: Path,
) -> None:
    map_info = MapInfo(file_path='Maps/Collab/Map.bin')
    map_path = tmp_path / map_info.file_path
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
    map_info = MapInfo(file_path='Maps/Collab/Map.bin')
    archive_path = tmp_path / 'Collab.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr(map_info.file_path.as_posix(), b'first')
    mod = make_installed_mod(
        source='zip',
        filename='Collab.zip',
        path=str(archive_path),
        metadata_name='Collab',
        metadata_version=None,
    )

    first = collab_journal_icon_fingerprint(mod, [map_info])
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr(map_info.file_path.as_posix(), b'second')

    assert collab_journal_icon_fingerprint(mod, [map_info]) != first


def test_scanner_keeps_side_assets_and_dialog_data_separate(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'sides.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Sides\n  Version: 1.0.0\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Maps/Test/Map-B.bin', b'map data')
        archive.writestr('Maps/Test/Map-C.bin', b'map data')
        archive.writestr('Dialog/English.txt', 'Test_Map=Map Name\n')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]
    maps = mod.maps
    assert [map_info.file_path for map_info in maps] == _paths(
        'Maps/Test/Map.bin',
        'Maps/Test/Map-B.bin',
        'Maps/Test/Map-C.bin',
    )
    assert mod.dialogs['en']['test_map'] == 'Map Name'


def test_scanner_sorts_map_paths_naturally_and_keeps_heart_side_last(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'order.zip', 'w') as archive:
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

    assert map_files == _paths(
        'Maps/Test/1-Beginner/Map.bin',
        'Maps/Test/1-Beginner/Map-B.bin',
        'Maps/Test/1-Beginner/Map-C.bin',
        'Maps/Test/2-Intermediate/Map.bin',
        'Maps/Test/10-Advanced/Map.bin',
        'Maps/Test/1-Beginner/ZZ-HeartSide.bin',
    )


def test_scanner_reads_all_supported_dialog_languages(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'languages.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Languages\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Dialog/English.txt', 'Test_Map=English Map\n')
        archive.writestr('Dialog/Japanese.txt', 'Test_Map=日本語マップ\n')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.dialogs['en']['test_map'] == 'English Map'
    assert mod.dialogs['ja']['test_map'] == '日本語マップ'


def test_scanner_uses_game_fallback_name_without_dialog(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'fallback.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Fallback\n  Version: 1.0.0\n')
        archive.writestr('Maps/Author/MicroMountain/Map.bin', b'map data')
        archive.writestr('Maps/Author/MicroMountain/Map-B.bin', b'map data')

    maps = ModScanner(tmp_path / 'Celeste').scan().mods[0].maps

    assert [map_info.file_path for map_info in maps] == _paths(
        'Maps/Author/MicroMountain/Map.bin',
        'Maps/Author/MicroMountain/Map-B.bin',
    )


def test_scanner_groups_collab_maps_under_their_lobbies(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'collab.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: TestCollab\n')
        archive.writestr('CollabUtils2CollabID.txt', 'TestCollab\n')
        archive.writestr('Maps/TestCollab/0-Lobbies/0-Prologue.bin', b'map data')
        archive.writestr('Maps/TestCollab/0-Lobbies/1-Beginner.bin', b'map data')
        archive.writestr('Maps/TestCollab/0-Gyms/1-Beginner.bin', b'map data')
        archive.writestr('Maps/TestCollab/1-Beginner/Map.bin', b'map data')
        archive.writestr(
            'Dialog/English.txt',
            'TestCollab_0_Lobbies=Test Collab\n'
            'TestCollab_0_Lobbies_0_Prologue=Prologue\n'
            'TestCollab_0_Lobbies_1_Beginner=Beginner Lobby\n'
            'TestCollab_1_Beginner_Map=Playable Map\n',
        )

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert [map_info.file_path for map_info in mod.maps] == _paths(
        'Maps/TestCollab/0-Gyms/1-Beginner.bin',
        'Maps/TestCollab/0-Lobbies/0-Prologue.bin',
        'Maps/TestCollab/0-Lobbies/1-Beginner.bin',
        'Maps/TestCollab/1-Beginner/Map.bin',
    )


def test_scanner_retains_collab_lobby_levelset_dialog_entries(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'collab.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: TestCollab\n')
        archive.writestr('CollabUtils2CollabID.txt', 'TestCollab\n')
        archive.writestr('Maps/TestCollab/0-Lobbies/1-Maps.bin', b'map data')
        archive.writestr(
            'Dialog/English.txt',
            'modname_TestCollab=Updater Name\n'
            'levelset_TestCollab_0_Lobbies=LevelSet Name\n'
            'TestCollab_0_Lobbies=Fallback Name\n',
        )

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.dialogs['en'] == {
        'modname_testcollab': 'Updater Name',
        'levelset_testcollab_0_lobbies': 'LevelSet Name',
        'testcollab_0_lobbies': 'Fallback Name',
    }


def test_scanner_groups_multiple_collab_levelsets_without_reading_lobby_bins(
    tmp_path: Path,
) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'collab.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: TestCollab\n')
        archive.writestr('CollabUtils2CollabID.txt', 'TestCollab\n')
        archive.writestr('Maps/TestCollab/0-Lobbies/1-Maps.bin', b'not parsed')
        archive.writestr('Maps/TestCollab/1-Maps/Map.bin', b'not parsed')
        archive.writestr('Maps/TestCollab/1-Submissions/Submission.bin', b'not parsed')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.map_files == _paths(
        'Maps/TestCollab/0-Lobbies/1-Maps.bin',
        'Maps/TestCollab/1-Maps/Map.bin',
        'Maps/TestCollab/1-Submissions/Submission.bin',
    )
