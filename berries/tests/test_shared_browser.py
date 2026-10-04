from pathlib import Path

from test_support.browser import run_browser_tests


def test_shared_map_browser_modules() -> None:
    run_browser_tests(Path(__file__).parent / 'browser')
