import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from berries.game.mods import ModScanReport
from pist import __main__ as cli
from pist.credentials.store import CredentialStore
from pist.records.lock import RecordWriterBusyError, record_writer_lock
from pist.settings import SettingsStore
from pist.ui.maps.browser import app as browser
from pist.ui.records import app as record_browser


def test_record_lock_rejects_same_directory_but_allows_independent_data(tmp_path: Path) -> None:
    with record_writer_lock(tmp_path / 'first'):
        with (
            pytest.raises(RecordWriterBusyError),
            record_writer_lock(tmp_path / 'first' / 'unused' / '..'),
        ):
            pytest.fail('The same data directory must not admit a second writer')
        with record_writer_lock(tmp_path / 'second'):
            pass
    with record_writer_lock(tmp_path / 'first'):
        pass


def test_record_lock_releases_after_workflow_exception(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='workflow failed'), record_writer_lock(tmp_path):
        raise ValueError('workflow failed')
    with record_writer_lock(tmp_path):
        pass


def test_record_lock_preserves_non_contention_io_errors(tmp_path: Path) -> None:
    invalid = tmp_path / 'not-a-directory'
    invalid.write_text('file', encoding='utf-8')
    with pytest.raises(RuntimeError) as caught, record_writer_lock(invalid):
        pytest.fail('An invalid data directory must not admit a writer')
    assert not isinstance(caught.value, RecordWriterBusyError)
    assert isinstance(caught.value.__cause__, OSError)


def test_record_lock_is_cross_process_and_released_after_termination(tmp_path: Path) -> None:
    code = """import sys
from pathlib import Path
from pist.records.lock import record_writer_lock

with record_writer_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, '-c', code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(RecordWriterBusyError), record_writer_lock(tmp_path):
            pytest.fail('Another process owns the lock')
    finally:
        process.terminate()
        process.communicate(timeout=10)
    with record_writer_lock(tmp_path):
        pass


@pytest.mark.parametrize('entry', ('browser', 'sync', 'records'))
def test_busy_record_entries_stop_before_opening_ui_or_reading_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    monkeypatch.setattr(browser, 'record_writer_lock', lambda: record_writer_lock(tmp_path))
    monkeypatch.setattr(cli, 'record_writer_lock', lambda: record_writer_lock(tmp_path))
    monkeypatch.setattr(record_browser, 'record_writer_lock', lambda: record_writer_lock(tmp_path))

    def unexpected_access(*args: object, **kwargs: object) -> None:
        pytest.fail('A busy entry must stop before accessing records or creating the UI')

    monkeypatch.setattr(browser, 'MapBrowserApp', unexpected_access)
    monkeypatch.setattr(cli, 'RecordStore', unexpected_access)
    monkeypatch.setattr(record_browser, 'RecordStore', unexpected_access)
    report = ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[])
    with record_writer_lock(tmp_path), pytest.raises(RecordWriterBusyError):
        if entry == 'browser':
            asyncio.run(browser.browse_maps(report))
        elif entry == 'sync':
            asyncio.run(cli.sync_saved_record(Mock(spec=CredentialStore), 'source', 1))
        else:
            asyncio.run(record_browser.browse_records(settings_store=Mock(spec=SettingsStore)))


def test_browser_holds_record_lock_until_ui_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browser, 'record_writer_lock', lambda: record_writer_lock(tmp_path))
    monkeypatch.setattr(browser.CollabLobbyOverrideStore, 'load', lambda _: None)

    class FakeBrowser:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_async(self) -> None:
            with pytest.raises(RecordWriterBusyError), record_writer_lock(tmp_path):
                pytest.fail('The browser released its lock before exiting')

    monkeypatch.setattr(browser, 'MapBrowserApp', FakeBrowser)
    report = ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[])
    asyncio.run(browser.browse_maps(report))
    with record_writer_lock(tmp_path):
        pass
