"""User configuration failures must not prevent importing independent readers."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('without_c_loader', (False, True))
def test_yaml_readers_prefer_c_loader_and_support_python_fallback(without_c_loader: bool) -> None:
    code = f"""
import yaml

if {without_c_loader!r}:
    yaml.__dict__.pop('CBaseLoader', None)
    yaml.__dict__.pop('CSafeLoader', None)
expected = getattr(yaml, 'CBaseLoader', yaml.BaseLoader)

from berries.game import map_hiders, mods

for module in (map_hiders, mods):
    assert module._BaseLoader is expected
    assert yaml.load('name: 01\\nflag: true\\ndate: 2026-10-18\\n', Loader=module._BaseLoader) == {{
        'name': '01', 'flag': 'true', 'date': '2026-10-18'
    }}

from berries.game.saves.codecs.yaml import nodes
from berries.game.saves.files.enders_blender import EndersBlenderSave
from io import BytesIO
assert nodes._SafeLoader is getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
save = EndersBlenderSave.read(BytesIO(b'mapDict_roomStat_firstClear_roomOrder: {{Map: [01, true, 2026-10-18]}}'))
assert save.first_clear.get('Map').room_order == ['01', 'true', '2026-10-18']
"""
    result = subprocess.run(
        [sys.executable, '-c', code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_invalid_local_entrances_do_not_affect_imports_or_packaged_defaults(tmp_path: Path) -> None:
    (tmp_path / 'map_entrances.toml').write_text('[broken', encoding='utf-8')
    env = dict(os.environ, BERRIES_DATA_DIR=str(tmp_path))
    imported = subprocess.run(
        [sys.executable, '-c', 'from berries.game.binmap import parse_map_bin'],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    loaded = subprocess.run(
        [
            sys.executable,
            '-c',
            (
                'from berries.map_entrances import load_default_map_entrance_rules; '
                'assert load_default_map_entrance_rules().rules'
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert loaded.returncode == 0, loaded.stderr
