"""Textual theme changes persist without a separate application switching path."""

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from catalog_factory import make_campaign_catalog

from pist.data_stores import DataStores
from pist.records.store import RecordStore
from pist.settings import PistSettings, SettingsStore
from pist.ui.maps.browser.app import MapBrowserApp
from pist.ui.messages import DiagnosticApp
from pist.ui.records.app import RecordBrowserApp
from pist.ui.theme import configure_theme
from pist.ui.tui import RefreshableCssApp


@pytest.mark.parametrize('workflow', ('maps', 'records'))
def test_theme_restore_and_changes_preserve_other_settings(
    tmp_path: Path, workflow: Literal['maps', 'records']
) -> None:
    settings_path = tmp_path / 'settings.json'
    store = SettingsStore(settings_path)
    original = PistSettings(theme='dracula', dialog_lang='en', game_dir=tmp_path / 'Celeste')
    store.save(original)
    initial_bytes = settings_path.read_bytes()

    def create_app() -> RefreshableCssApp[None]:
        if workflow == 'maps':
            return MapBrowserApp(
                make_campaign_catalog(mods=[], game_dir=tmp_path),
                settings_store=store,
                data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
            )
        return RecordBrowserApp(RecordStore(tmp_path / 'records.sqlite3'), settings_store=store)

    async def run() -> None:
        app = create_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == 'dracula'
            assert settings_path.read_bytes() == initial_bytes
            # A newer settings edit must not be overwritten by the theme observer.
            current = original.model_copy(update={'smartsheet_url': 'https://example.invalid'})
            store.save(current)
            app.theme = 'nord'
            await pilot.pause()
            assert store.load() == current.model_copy(update={'theme': 'nord'})
        restarted = create_app()
        async with restarted.run_test() as pilot:
            await pilot.pause()
            assert restarted.theme == 'nord'

    asyncio.run(run())


@pytest.mark.parametrize('theme', ('unregistered-theme', 'nord,textual-dark'))
def test_unavailable_theme_warns_without_rewriting_settings(tmp_path: Path, theme: str) -> None:
    store = SettingsStore(tmp_path / 'settings.json')
    settings = PistSettings(theme=theme)
    store.save(settings)

    async def run() -> None:
        app: DiagnosticApp[None] = DiagnosticApp()
        configure_theme(app, settings, store)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == 'textual-dark'
            assert app.diagnostic_count == 1
            assert store.load() == settings

    asyncio.run(run())


def test_theme_save_failure_preserves_selection_and_reports_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SettingsStore(tmp_path / 'settings.json')
    original = PistSettings()
    store.save(original)

    def fail_save(settings: PistSettings) -> None:
        raise OSError('settings file unavailable')

    monkeypatch.setattr(store, 'save', fail_save)

    async def run() -> None:
        app: DiagnosticApp[None] = DiagnosticApp()
        configure_theme(app, original, store)
        async with app.run_test() as pilot:
            app.theme = 'nord'
            await pilot.pause()
            assert app.theme == 'nord'
            assert store.load() == original
            assert app.diagnostic_count == 1

    asyncio.run(run())
