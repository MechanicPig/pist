"""Run the documented entry points against an isolated game and save slot."""

import os
import subprocess
import sys
from pathlib import Path
from struct import pack

import pytest

from test_support.save_docs import normal_save_xml

REPO_DIR = Path(__file__).parents[2]


@pytest.mark.parametrize('script', ['list_maps.py', 'inspect_map.py'])
def test_example_help(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_DIR / 'examples' / script), '--help'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'game_dir' in result.stdout


@pytest.mark.parametrize('custom', [False, True])
def test_inspect_example_reads_a_real_map_and_save(tmp_path: Path, custom: bool) -> None:
    game_dir = tmp_path / 'game'
    maps = game_dir / 'Content' / 'Maps'
    maps.mkdir(parents=True)
    # Minimal valid binary map with an empty root; not an arbitrary placeholder file.
    strings = b'\x0bCELESTE MAP\x04Test'
    lookup = pack('<H', 1) + b'\x03Map'
    root = pack('<HBH', 0, 0, 0)
    (maps / '1-ForsakenCity.bin').write_bytes(strings + lookup + root)
    saves = tmp_path / 'Saves'
    saves.mkdir()
    (saves / '0.celeste').write_text(
        normal_save_xml(
            '<SaveData><Areas><AreaStats SID="Celeste/1-ForsakenCity"><Modes>'
            '<AreaModeStats TimePlayed="170000" Deaths="2" />'
            '<AreaModeStats TimePlayed="0" Deaths="0" />'
            '<AreaModeStats TimePlayed="0" Deaths="0" />'
            '</Modes></AreaStats></Areas></SaveData>'
        ),
        encoding='utf-8',
    )
    override = tmp_path / 'entities.toml'
    override.write_text('[entities]\n', encoding='utf-8')
    rule_args = ['--local-rules', str(override)] if custom else ['--packaged-rules']
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_DIR / 'examples' / 'inspect_map.py'),
            str(game_dir),
            'Celeste/1-ForsakenCity',
            '--save-slot',
            '0',
            *rule_args,
        ],
        env={**os.environ, 'EVEREST_SAVEPATH': str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'Celeste/1-ForsakenCity [A]' in result.stdout
    assert 'Save 0: time_played=' in result.stdout
    assert 'deaths=2' in result.stdout
