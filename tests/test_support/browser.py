"""Run dependency-free browser module tests when a local Node runtime is available."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from aiohttp import ClientSession


def run_browser_tests(test_dir: Path) -> None:
    """Execute JavaScript behavior tests without adding a frontend package or build step."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Browser module tests require Node.js; Python-only environments can omit it.')
    test_files = sorted(test_dir.glob('*.test.mjs'))
    assert test_files, f'No browser module tests found in {test_dir}'
    result = subprocess.run(
        [node, '--test', *map(str, test_files)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def load_browser_modules(session: ClientSession, url: str) -> set[str]:
    """Check that the actual page's complete ES module import graph is served as JavaScript."""
    pending = ['map_preview.js']
    loaded: set[str] = set()
    while pending:
        name = pending.pop()
        if name in loaded:
            continue
        async with session.get(f'{url}/assets/{name}') as response:
            assert response.status == 200, name
            assert response.content_type == 'text/javascript', name
            source = await response.text()
            assert source, name
        loaded.add(name)
        pending.extend(re.findall(r"""from\s+['"]\./([^/'"]+\.js)['"]""", source))
    return loaded
