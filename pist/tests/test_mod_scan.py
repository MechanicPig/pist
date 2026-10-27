"""Application-only ordering and recovery policies for Mod scanning."""

import json
from pathlib import Path
from zipfile import ZipFile

from berries.game.content import ContentPath
from berries.game.mods import ModScanProgress, ModScanStage
from pist.mod_scan import ModScanner, map_sort_key


def test_display_sort_groups_sides_and_keeps_heart_side_last() -> None:
    paths = [
        ContentPath(path)
        for path in (
            'Maps/Test/10-Advanced/Map.bin',
            'Maps/Test/2-Intermediate/Map.bin',
            'Maps/Test/1-Beginner/Map-C.bin',
            'Maps/Test/1-Beginner/Map.bin',
            'Maps/Test/1-Beginner/Map-B.bin',
            'Maps/Test/1-Beginner/ZZ-HeartSide.bin',
        )
    ]
    assert sorted(paths, key=map_sort_key) == [paths[i] for i in (3, 4, 2, 1, 0, 5)]


def test_path_notices_skip_noncanonical_maps_and_preserve_severity(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'Example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Maps/Pack/A.bin', b'map')
        archive.writestr('Maps/Pack//A.bin', b'placeholder')
        archive.writestr('Maps/./B.bin', b'ignored')
        archive.writestr('Maps/../C.bin', b'ignored')
    scanner = ModScanner(tmp_path)
    report = scanner.scan()
    assert report.mods[0].map_files == [ContentPath('Maps/Pack/A.bin')]
    assert len(report.warnings) == 1
    assert report.warnings[0].severity == 'warning'
    assert '连续斜杠' in report.warnings[0].message
    assert len(report.infos) == 2
    assert all(message.severity == 'info' for message in report.infos)
    assert sum(line.startswith('[info]') for line in report.diagnostics) == 2
    assert report.model_dump(mode='json')['infos'][0]['severity'] == 'info'
    assert len(scanner.scan().infos) == 2


def test_application_scan_recovers_formats_diagnostics_and_sorts(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'Example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: invalid\n')
        archive.writestr('Maps/Example/Map-B.bin', b'')
        archive.writestr('Maps/Example/Map.bin', b'')
        archive.writestr('Dialog/English.txt', b'\xff')
        archive.writestr('Dialog/Japanese.txt', 'TITLE=Kept')
    progress: list[ModScanProgress] = []
    report = ModScanner(tmp_path).scan(on_progress=progress.append)
    assert report.mods[0].metadata_name == '_zip_Example'
    assert [path.name for path in report.mods[0].map_files] == ['Map.bin', 'Map-B.bin']
    assert report.mods[0].dialogs == {'ja': {'title': 'Kept'}}
    assert {warning.stage for warning in report.warnings} == {
        ModScanStage.MANIFEST,
        ModScanStage.DIALOG,
    }
    assert report.warnings[0].message.startswith('Mod 元数据无效：')
    assert 'Value error' not in report.warnings[0].message
    assert report.warnings[1].message.startswith('Dialog 读取失败：')
    assert progress[-1].completed == progress[-1].total == 1
    exported = json.loads(report.model_dump_json())
    assert exported['warnings'] == [warning.model_dump(mode='json') for warning in report.warnings]
    assert exported['mods'] == [mod.model_dump(mode='json') for mod in report.mods]
    assert 'source' not in exported['mods'][0]
    catalog = report.load_catalog(tmp_path)
    assert catalog.diagnostics == report.diagnostics
    assert catalog.catalog.campaigns[0].ref == 'Example'


def test_application_scan_keeps_report_diagnostics_independent_between_runs(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    source = mods_dir / 'Example.zip'
    source.write_bytes(b'Not a ZIP')
    scanner = ModScanner(tmp_path)
    first = scanner.scan()
    assert len(first.warnings) == 1
    with ZipFile(source, 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Fixed\n')
    second = scanner.scan()
    assert second.warnings == []
    assert len(first.warnings) == 1


def test_application_catalog_composes_scan_and_content_loading_diagnostics(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'Example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: invalid\n')
    directory = tmp_path / 'Content' / 'Dialog'
    directory.mkdir(parents=True)
    (directory / 'English.txt').write_bytes(b'\xff')
    report = ModScanner(tmp_path).scan()
    catalog = report.load_catalog(tmp_path)
    assert len(catalog.diagnostics) == 2
    assert catalog.diagnostics[0] == report.diagnostics[0]
    assert 'English.txt' in catalog.diagnostics[1]
