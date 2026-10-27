import json
import logging
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import TypeAdapter, ValidationError

from berries import GameInstallation
from berries.containers import CaseFoldDict
from berries.game.content import BadContentEntry, ContentEntry, ContentPath
from berries.game.everest import ModMetadata, Version
from berries.game.maps import MapFilePath, VanillaMap
from berries.game.mods import (
    DirMod,
    ModScanError,
    ModScanner,
    ModScanProgress,
    ModScanStage,
    ZipMod,
    is_mod_dependency,
)

MOD_SCAN_GAME_DATA_DIR = Path(__file__).parent / 'data' / 'mod_scan_game'


def test_scanner_retains_valid_dialogs_after_decode_and_parse_failures(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Dialog/English.txt', b'\xff')
        archive.writestr('Dialog/French.txt', 'TITLE={+TITLE}')
        archive.writestr('Dialog/Japanese.txt', 'TITLE=Kept')
    failures: list[ModScanError] = []
    report = ModScanner(tmp_path, on_error=failures.append).scan()
    assert report.mods[0].dialogs == {'ja': {'title': 'Kept'}}
    assert {error.file_path for error in failures} == {
        'Dialog/English.txt',
        'Dialog/French.txt',
    }
    assert all(error.path.name == 'example.zip' for error in failures)
    assert all(error.stage == ModScanStage.DIALOG for error in failures)


def materialize_mod_scan_game(destination: Path) -> Path:
    """Materialize the readable Mod scanner fixture under ``destination``."""
    game_dir = destination / 'Celeste'
    mods_dir = game_dir / 'Mods'
    mods_dir.mkdir(parents=True)
    shutil.copy2(MOD_SCAN_GAME_DATA_DIR / 'blacklist.txt', mods_dir / 'blacklist.txt')
    for source in (MOD_SCAN_GAME_DATA_DIR / 'archives').iterdir():
        with ZipFile(mods_dir / source.name, 'w', compression=ZIP_DEFLATED) as archive:
            for path in source.rglob('*'):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
    for source in (MOD_SCAN_GAME_DATA_DIR / 'directories').iterdir():
        shutil.copytree(source, mods_dir / source.name)
    return game_dir


def _paths(*values: str) -> tuple[ContentPath, ...]:
    return tuple(ContentPath(value) for value in values)


def test_map_path_requires_a_maps_bin_content_path() -> None:
    map_info = TypeAdapter(MapFilePath).validate_python(r'Maps\Test\Map.bin')

    assert map_info == ContentPath('Maps/Test/Map.bin')
    assert isinstance(map_info, ContentPath)
    with pytest.raises(ValidationError):
        TypeAdapter(MapFilePath).validate_python('Dialog/Test.txt')


def test_map_path_internal_construction_uses_a_content_path() -> None:
    path = ContentPath('Maps/Test/Map.bin')
    map_info = VanillaMap(path, Path('Content'))

    assert map_info.file_path is path
    assert map_info.campaign_dir == ContentPath('Maps/Test')


def test_map_path_accepts_external_json_paths() -> None:
    map_info = TypeAdapter(MapFilePath).validate_json(json.dumps(r'Maps\Test\Map.bin'))

    assert isinstance(map_info, ContentPath)
    assert map_info == ContentPath('Maps/Test/Map.bin')


@pytest.mark.parametrize('path', ['Maps/Test/Map.BIN', 'Dialog/Test.bin', 'Map.bin'])
def test_map_path_validates_both_typed_and_external_paths(path: str) -> None:
    with pytest.raises(ValidationError) as typed_error:
        TypeAdapter(MapFilePath).validate_python(ContentPath(path))
    with pytest.raises(ValidationError) as external_error:
        TypeAdapter(MapFilePath).validate_python(path)

    assert typed_error.value.errors()[0]['loc'] == ()
    assert external_error.value.errors()[0]['loc'] == ()


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


def test_scanner_reads_enabled_zip_and_directory_mods(tmp_path: Path) -> None:
    scanner = ModScanner(materialize_mod_scan_game(tmp_path))
    progress: list[ModScanProgress] = []
    report = scanner.scan(on_progress=progress.append)

    assert [mod.metadata_name for mod in report.mods] == ['EnabledZip', 'DirectoryMod']
    assert isinstance(report.mods[0], ZipMod)
    assert isinstance(report.mods[1], DirMod)
    assert report.disabled_filenames == ['disabled.zip']
    assert report.disabled_mod_names == ['DisabledZip']
    assert tuple(report.mods[0].map_files) == _paths('Maps/Test/0_Map.bin')
    assert tuple(report.mods[1].map_files) == _paths('Maps/Directory/0-Map.bin')
    assert report.mods[0].collab_id == 'TestCollab'
    assert isinstance(report.mods[0].dialogs['en'], CaseFoldDict)
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
    assert progress[0] == ModScanProgress(0, 3)
    assert [event.path.name for event in progress[1:] if event.path is not None] == [
        'disabled.zip',
        'enabled.zip',
        'DirectoryMod',
    ]
    assert [event.completed for event in progress] == [0, 1, 2, 3]
    assert progress[1].disabled

    exported = json.loads(report.model_dump_json())
    assert exported['mods'] == [mod.model_dump(mode='json') for mod in report.mods]
    assert all('source' not in mod for mod in exported['mods'])
    assert exported['mods'][0]['map_files'] == ['Maps/Test/0_Map.bin']
    assert exported['mods'][0]['dialogs']['en']['test_0_map'] == 'English Map'


@pytest.mark.parametrize('read_dialogs', (False, True))
def test_scanner_all_packages_controls_dialog_reads_independently(
    tmp_path: Path, read_dialogs: bool
) -> None:
    game_dir = materialize_mod_scan_game(tmp_path)
    with ZipFile(game_dir / 'Mods' / 'disabled.zip', 'a') as archive:
        archive.writestr('Dialog/English.txt', 'TITLE=Disabled')
    enabled = ModScanner(game_dir).scan(read_dialogs=read_dialogs)
    all_mods = ModScanner(game_dir).scan(include_disabled=True, read_dialogs=read_dialogs)
    assert [mod.filename for mod in enabled.mods] == ['enabled.zip', 'DirectoryMod']
    assert [mod.filename for mod in all_mods.mods] == [
        'disabled.zip',
        'enabled.zip',
        'DirectoryMod',
    ]
    assert all_mods.disabled_mod_names == enabled.disabled_mod_names == ['DisabledZip']
    assert [mod.filename for mod in all_mods.enabled_mods] == [mod.filename for mod in enabled.mods]
    assert bool(all_mods.mods[0].dialogs) == read_dialogs
    assert bool(all_mods.mods[1].dialogs) == read_dialogs
    assert all_mods.mods[2].dialogs == {}


def test_scanner_callbacks_can_abort_and_own_their_diagnostics(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    target = mods_dir / 'broken.zip'
    target.write_bytes(b'Not a ZIP')

    def abort(error: ModScanError) -> None:
        assert error.stage == ModScanStage.PACKAGE
        raise PermissionError('Callback stopped scan')

    with pytest.raises(PermissionError, match='Callback stopped scan'):
        ModScanner(tmp_path, on_error=abort).scan()
    failures: list[ModScanError] = []
    scanner = ModScanner(tmp_path, on_error=failures.append)
    first = scanner.scan()
    assert len(failures) == 1
    assert 'warnings' not in first.model_dump()
    with ZipFile(target, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Fixed\n')
    second = scanner.scan()
    assert second.mods[0].metadata_name == 'Fixed'
    assert 'warnings' not in second.model_dump()
    assert len(failures) == 1

    def stop_progress(progress: ModScanProgress) -> None:
        if progress.completed:
            raise RuntimeError('Progress stopped scan')

    with pytest.raises(RuntimeError, match='Progress stopped scan'):
        scanner.scan(on_progress=stop_progress)


def test_scanner_can_read_one_package_outside_game_mods_directory(tmp_path: Path) -> None:
    with ZipFile(tmp_path / 'single.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Single\n')
    mod = ModScanner(tmp_path / 'NotAnInstallation').read_mod(tmp_path / 'single.zip')
    assert mod is not None and mod.metadata_name == 'Single'


def test_installation_scan_exposes_scope_and_callbacks(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'disabled.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Disabled\n')
        archive.writestr('Dialog/English.txt', b'\xff')
    (mods_dir / 'blacklist.txt').write_text('disabled.zip', encoding='utf-8')
    failures: list[ModScanError] = []
    progress: list[ModScanProgress] = []
    report = GameInstallation(tmp_path).scan_mods(
        include_disabled=True, on_error=failures.append, on_progress=progress.append
    )
    assert len(report.mods) == 1
    assert report.enabled_mods == ()
    assert failures[0].stage == ModScanStage.DIALOG and failures[0].disabled
    assert progress[-1].completed == 1 and progress[-1].disabled
    failures.clear()
    GameInstallation(tmp_path).scan_mods(
        include_disabled=True, read_dialogs=False, on_error=failures.append
    )
    assert failures == []


def test_scanner_keeps_a_map_that_is_also_a_zip_directory_prefix(tmp_path: Path) -> None:
    archive_path = tmp_path / 'mod.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Maps/Pack.bin', b'outer-map')
        archive.writestr('Maps/Pack.bin/A.bin', b'inner-map')
    mod = ModScanner(tmp_path).read_mod(archive_path)
    assert mod is not None
    assert tuple(mod.map_files) == _paths('Maps/Pack.bin', 'Maps/Pack.bin/A.bin')


@pytest.mark.parametrize('archive', [False, True])
def test_scanner_silently_ignores_hidden_resources_but_not_hidden_mod_roots(
    tmp_path: Path, archive: bool, caplog: pytest.LogCaptureFixture
) -> None:
    files = {
        'everest.yaml': '- Name: Example\n  Version: 1.0.0\n',
        'Maps/Visible/A.bin': 'map',
        'Maps/Visible/A.extra.bin': 'map',
        'Maps/.hidden/A.bin': 'ignored',
        'Maps/Visible/.nested/A.bin': 'ignored',
        'Maps/Visible/.hidden.bin': 'ignored',
        'Dialog/.English.txt': 'TITLE=Ignored',
        'Dialog/.hidden/English.txt': 'TITLE=Ignored',
        'Dialog/English.txt': 'TITLE=Kept',
    }
    path = tmp_path / ('.Example.zip' if archive else '.Example')
    if archive:
        with ZipFile(path, 'w') as output:
            for name, value in files.items():
                output.writestr(name, value)
            output.writestr('Maps/.hidden//Invalid.bin', 'ignored')
            output.writestr('Maps/.hidden/../Invalid.bin', 'ignored')
    else:
        for name, value in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding='utf-8')
    caplog.set_level(logging.INFO, logger='berries.game.mods')
    mod = ModScanner(tmp_path).read_mod(path)
    assert mod is not None
    assert set(mod.map_files) == set(_paths('Maps/Visible/A.bin', 'Maps/Visible/A.extra.bin'))
    assert mod.dialogs == {'en': {'title': 'Kept'}}
    assert caplog.records == []
    # The generic content tree still exposes hidden entries; only loading filters them.
    with mod.open() as root:
        assert (root / 'Maps/.hidden/A.bin').is_file()


def test_scanner_reports_dot_segments_as_info_and_repeated_separators_as_warning(
    tmp_path: Path,
) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    write_zip_mod(mods_dir, 'valid.zip', 'Valid')
    with ZipFile(mods_dir / 'valid.zip', 'a') as archive:
        archive.writestr('../unrelated.txt', 'ignored')
        archive.writestr('Maps/./Ignored.bin', b'ignored')
        archive.writestr('Maps/Test//Ignored.bin', b'ignored')
        archive.writestr('Maps/.hidden//Ignored.bin', b'ignored')
        archive.writestr('Maps/.hidden/../Ignored.bin', b'ignored')

    failures: list[ModScanError] = []
    notices: list[tuple[Path, str, str, bool]] = []

    def notice(path: Path, raw: str, severity: str, disabled: bool) -> None:
        notices.append((path, raw, severity, disabled))

    report = ModScanner(
        tmp_path / 'Celeste', on_error=failures.append, on_path_notice=notice
    ).scan()

    assert [mod.metadata_name for mod in report.mods] == ['Valid']
    assert tuple(report.mods[0].map_files) == _paths('Maps/Test/0_Map.bin')
    assert failures == []
    assert [(raw, severity) for _, raw, severity, _ in notices] == [
        ('../unrelated.txt', 'info'),
        ('Maps/./Ignored.bin', 'info'),
        ('Maps/Test//Ignored.bin', 'warning'),
    ]
    assert all(path.name == 'valid.zip' and not disabled for path, _, _, disabled in notices)


def test_default_path_notices_use_logging_levels_without_aborting_scan(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    archive_path = tmp_path / 'Example.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Maps/./Ignored.bin', b'ignored')
        archive.writestr('Maps/Pack//Ignored.bin', b'ignored')
    caplog.set_level(logging.INFO, logger='berries.game.mods')
    mod = ModScanner(tmp_path).read_mod(archive_path)
    assert mod is not None and mod.map_files == []
    assert [record.levelno for record in caplog.records] == [logging.INFO, logging.WARNING]


def test_path_notice_callback_failure_propagates_and_closes_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / 'Example.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Maps/./Ignored.bin', b'ignored')

    def reject(path: Path, raw: str, severity: str, disabled: bool) -> None:
        raise RuntimeError('notice rejected')

    with pytest.raises(RuntimeError, match='notice rejected'):
        ModScanner(tmp_path, on_path_notice=reject).read_mod(archive_path)
    archive_path.unlink()


def test_scanner_preserves_all_metadata_entries_for_one_pkg(tmp_path: Path) -> None:
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
        with ZipFile(mods_dir / filename, 'w') as archive:
            archive.writestr('everest.yaml', f'- Name: {filename}\n')
    (mods_dir / 'blacklist.txt').write_text('blacklisted.zip\n', encoding='utf-8')
    (mods_dir / 'session-blacklist.txt').write_text('enabled.zip\n', encoding='utf-8')
    (mods_dir / 'session-whitelist.txt').write_text('blacklisted.zip\n', encoding='utf-8')

    scanner = ModScanner(
        tmp_path / 'Celeste',
        whitelist_path=Path('session-whitelist.txt'),
        temporary_blacklist_path=Path('session-blacklist.txt'),
    )
    report = scanner.scan()

    assert [mod.filename for mod in report.mods] == [
        'blacklisted.zip',
        'other.zip',
    ]
    assert report.disabled_filenames == ['enabled.zip']

    full_override = ModScanner(
        tmp_path / 'Celeste',
        whitelist_path=Path('session-whitelist.txt'),
        temporary_blacklist_path=Path('session-blacklist.txt'),
        whitelist_full_override=True,
    ).scan()

    assert [mod.filename for mod in full_override.mods] == ['blacklisted.zip']
    assert full_override.disabled_filenames == [
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

    assert tuple(report.mods[0].map_files) == _paths('Maps/Test/0_Map.bin')


def test_scanner_lists_disabled_root_map_mods_without_reading_dialog(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'root-map.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: RootMap\n')
        archive.writestr('Maps/RootMap.bin', b'map data')
    (mods_dir / 'blacklist.txt').write_text('root-map.zip\n', encoding='utf-8')

    mods = ModScanner(tmp_path / 'Celeste').scan(include_disabled=True, read_dialogs=False).mods

    assert [(mod.metadata_name, tuple(mod.map_files), mod.dialogs) for mod in mods] == [
        ('RootMap', _paths('Maps/RootMap.bin'), {})
    ]


def test_scanner_keeps_enabled_root_maps_outside_campaigns(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'root-map.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: RootMap\n')
        archive.writestr('Maps/RootMap.bin', b'map data')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert tuple(mod.map_files) == _paths('Maps/RootMap.bin')


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

    failures: list[ModScanError] = []
    report = ModScanner(tmp_path / 'Celeste', on_error=failures.append).scan()

    assert [mod.metadata_name for mod in report.mods] == ['Working']
    assert len(failures) == 1
    assert isinstance(failures[0].cause, UnicodeDecodeError)
    assert failures[0].stage == ModScanStage.TEXT
    assert failures[0].file_path == 'everest.yaml'
    with pytest.raises(ModScanError) as raised:
        ModScanner(tmp_path / 'Celeste').scan()
    assert raised.value.__cause__ is raised.value.cause


def test_scanner_warns_but_keeps_a_disabled_mod_with_invalid_metadata(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'broken.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Broken\n  Version: 1\n')
    with ZipFile(mods_dir / 'working.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Working\n')
    (mods_dir / 'blacklist.txt').write_text('broken.zip\n', encoding='utf-8')

    failures: list[ModScanError] = []
    report = ModScanner(tmp_path / 'Celeste', on_error=failures.append).scan()

    assert [mod.metadata_name for mod in report.mods] == ['Working']
    assert report.disabled_filenames == ['broken.zip']
    assert report.disabled_mod_names == []
    assert len(failures) == 1
    assert failures[0].path.name == 'broken.zip'
    assert failures[0].file_path == 'everest.yaml'
    assert failures[0].stage == ModScanStage.MANIFEST
    assert failures[0].disabled
    assert isinstance(failures[0].cause, ValidationError)


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

    failures: list[ModScanError] = []
    report = ModScanner(tmp_path / 'Celeste', on_error=failures.append).scan()

    assert [mod.metadata_name for mod in report.mods] == ['_zip_broken', 'Working']
    assert report.mods[0].metadata_version == '0.0.0-dummy'
    assert tuple(report.mods[0].map_files) == _paths('Maps/Broken/Map.bin')
    assert len(failures) == 1
    assert failures[0].path.name == 'broken.zip'
    assert failures[0].file_path == 'everest.yaml'
    assert failures[0].stage == ModScanStage.MANIFEST
    assert not failures[0].disabled


def test_scanner_warns_and_ignores_an_undecodable_dialog(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'dialog.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Dialog\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Dialog/English.txt', b'\xb1')

    failures: list[ModScanError] = []
    report = ModScanner(tmp_path / 'Celeste', on_error=failures.append).scan()

    assert report.mods[0].dialogs == {}
    assert failures[0].path.name == 'dialog.zip'
    assert failures[0].file_path == 'Dialog/English.txt'


def test_manifest_rejects_non_string_version() -> None:
    with pytest.raises(ValidationError):
        ModMetadata.model_validate({'Name': 'Example', 'Version': 1})


def test_manifest_ignores_unmatched_metadata_fields_like_everest() -> None:
    metadata = ModMetadata.model_validate(
        {'Name': 'Example', 'Version': '1.0', 'Author': 'Ignored'}
    )

    assert metadata.name == 'Example'


@pytest.mark.parametrize('value', ('1', '1.2.3.4.5', '1.a', '1.0+build', '2147483648.0'))
def test_manifest_rejects_invalid_everest_version(value: str) -> None:
    with pytest.raises(ValidationError):
        ModMetadata.model_validate({'Name': 'Example', 'Version': value})


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
    metadata = ModMetadata.model_validate({'Name': 'Example', 'Version': '1.20.0-preview'})

    assert metadata.model_dump(mode='json')['version'] == '1.20.0-preview'


def test_installed_mod_requires_a_manifest_entry() -> None:
    with pytest.raises(ValidationError):
        ZipMod(filename='Example.zip', path=Path('Example.zip'), manifest=())


def test_mod_dependency_excludes_loader_entries() -> None:
    assert not is_mod_dependency('Celeste')
    assert not is_mod_dependency('Everest')
    assert not is_mod_dependency('EverestCore')
    assert is_mod_dependency('EVEREST')
    assert is_mod_dependency('FrostHelper')


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
    assert tuple(mod.map_files) == _paths(
        'Maps/Test/Map-B.bin',
        'Maps/Test/Map-C.bin',
        'Maps/Test/Map.bin',
    )
    assert mod.dialogs['en']['test_map'] == 'Map Name'


def test_scanner_preserves_content_traversal_order_without_display_sorting(tmp_path: Path) -> None:
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

    assert tuple(map_files) == _paths(
        'Maps/Test/1-Beginner/Map-B.bin',
        'Maps/Test/1-Beginner/Map-C.bin',
        'Maps/Test/1-Beginner/Map.bin',
        'Maps/Test/1-Beginner/ZZ-HeartSide.bin',
        'Maps/Test/10-Advanced/Map.bin',
        'Maps/Test/2-Intermediate/Map.bin',
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


def test_scanner_matches_dialog_language_filenames_without_regard_to_case(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'languages.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Languages\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr('Dialog/english.txt', 'Test_Map=Map Name\n')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.dialogs['en']['test_map'] == 'Map Name'


@pytest.mark.parametrize('dialog_path', ('dialog/English.txt', 'Dialog/English.TXT'))
def test_scanner_keeps_dialog_directory_and_extension_case_sensitive(
    tmp_path: Path, dialog_path: str
) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'languages.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Languages\n')
        archive.writestr('Maps/Test/Map.bin', b'map data')
        archive.writestr(dialog_path, 'Test_Map=Map Name\n')

    mod = ModScanner(tmp_path / 'Celeste').scan().mods[0]

    assert mod.dialogs == {}


def test_scanner_uses_game_fallback_name_without_dialog(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Celeste' / 'Mods'
    mods_dir.mkdir(parents=True)
    with ZipFile(mods_dir / 'fallback.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Fallback\n  Version: 1.0.0\n')
        archive.writestr('Maps/Author/MicroMountain/Map.bin', b'map data')
        archive.writestr('Maps/Author/MicroMountain/Map-B.bin', b'map data')

    map_files = ModScanner(tmp_path / 'Celeste').scan().mods[0].map_files

    assert tuple(map_files) == _paths(
        'Maps/Author/MicroMountain/Map-B.bin',
        'Maps/Author/MicroMountain/Map.bin',
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

    assert tuple(mod.map_files) == _paths(
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

    assert tuple(mod.map_files) == _paths(
        'Maps/TestCollab/0-Lobbies/1-Maps.bin',
        'Maps/TestCollab/1-Maps/Map.bin',
        'Maps/TestCollab/1-Submissions/Submission.bin',
    )
