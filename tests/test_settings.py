from pathlib import Path

import pytest

from pist.__main__ import default_game_dir, default_sheet_source, set_default_setting
from pist.models import PistSettings
from pist.settings import SettingsStore


def test_settings_store_uses_dark_theme_until_saved(tmp_path: Path) -> None:
    path = tmp_path / 'settings.json'
    store = SettingsStore(path)

    assert store.load().theme == 'textual-dark'

    store.save(PistSettings(theme='textual-light'))

    assert store.load().theme == 'textual-light'

    store.save(PistSettings(theme='textual-light', dialog_languages=('ja', 'en')))

    assert store.load().dialog_languages == ('ja', 'en')


def test_default_game_and_sheet_settings_can_be_set_and_cleared(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / 'settings.json')
    game_dir = tmp_path / 'Celeste'
    sheet_url = 'https://docs.qq.com/smartsheet/example'

    set_default_setting(store, 'game_dir', game_dir)
    set_default_setting(store, 'smartsheet_url', sheet_url)

    settings = store.load()
    assert default_game_dir(None, settings) == game_dir
    assert default_sheet_source(None, settings) == sheet_url

    set_default_setting(store, 'game_dir', None)
    set_default_setting(store, 'smartsheet_url', None)

    settings = store.load()
    with pytest.raises(ValueError, match='game-dir'):
        default_game_dir(None, settings)
    with pytest.raises(ValueError, match='smartsheet-url'):
        default_sheet_source(None, settings)
