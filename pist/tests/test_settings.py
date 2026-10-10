from pathlib import Path

import pytest

from pist.__main__ import default_game_dir, default_sheet_source, set_default_setting
from pist.settings import PistSettings, RecordListMaxWidths, SettingsStore


@pytest.mark.parametrize('width', (0, -1, True, '20', 1.5))
def test_record_list_widths_require_positive_integers(width: object) -> None:
    with pytest.raises(ValueError):
        RecordListMaxWidths.model_validate({'map_name': width})


def test_record_list_widths_persist_and_default_missing_fields(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / 'settings.json')
    assert store.load().record_list_max_widths.map_name == 40
    settings = PistSettings.model_validate({'record_list_max_widths': {'map_name': 24}})
    store.save(settings)
    assert store.load() == settings
    assert store.load().record_list_max_widths.mod_name == 32


def test_failed_settings_replace_preserves_previous_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'settings.json'
    store = SettingsStore(path)
    original = PistSettings(theme='textual-light')
    store.save(original)

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError('replacement blocked')

    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(OSError, match='replacement blocked'):
        store.save(PistSettings(record_list_max_widths=RecordListMaxWidths(map_name=20)))
    assert store.load() == original
    assert list(tmp_path.iterdir()) == [path]


def test_settings_reject_empty_dialog_language_order() -> None:
    with pytest.raises(ValueError):
        PistSettings(dialog_languages=())


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
