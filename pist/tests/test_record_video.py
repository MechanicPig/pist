import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input

from pist.records.models import MapRecord, merge_saved_record
from pist.records.store import RecordStore
from pist.ui.records.editor import RecordEditorScreen


@pytest.mark.parametrize('value', ('https://example.com/new', '', 'invalid', 'https://[bad'))
def test_record_editor_preserves_edits_or_removes_video_link(value: str) -> None:
    record = MapRecord(
        created_at=datetime(2026, 10, 10, tzinfo=UTC),
        map_name='Map',
        video_url='https://example.com/old',
    )
    results: list[MapRecord | None] = []

    async def run() -> None:
        app = App(css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(RecordEditorScreen(record), results.append)
            await pilot.pause()
            assert app.screen.query_one('#record-video-url', Input).value == record.video_url
            app.screen.query_one('#record-video-url', Input).value = value
            await pilot.click('#record-confirm-save')
            await pilot.pause()
            if value.startswith('invalid') or value == 'https://[bad':
                assert isinstance(app.screen, RecordEditorScreen)

    asyncio.run(run())
    if value in {'invalid', 'https://[bad'}:
        assert results == []
    else:
        assert results == [record.model_copy(update={'video_url': value or None})]


def test_video_link_is_retained_when_refreshing_game_stats_and_persisting(tmp_path: Path) -> None:
    saved = MapRecord(
        created_at=datetime(2026, 10, 10, tzinfo=UTC),
        map_name='Map',
        mod_metadata_name='Example',
        map_file='Maps/Map.bin',
        video_url='https://example.com/video',
    )
    current = saved.model_copy(update={'video_url': None, 'deaths': 10})
    merged = merge_saved_record(current, saved)
    assert merged.video_url == saved.video_url
    assert merged.deaths == 10
    store = RecordStore(tmp_path / 'records.sqlite3')
    assert store.load(store.save(merged)).video_url == saved.video_url


@pytest.mark.parametrize('url', ('invalid', 'file:///some/file', 'javascript:alert(1)'))
def test_map_record_rejects_non_http_recording_urls(url: str) -> None:
    with pytest.raises(ValueError):
        MapRecord(created_at=datetime(2026, 10, 10, tzinfo=UTC), map_name='Map', video_url=url)


@pytest.mark.parametrize('url', ('invalid', 'file:///some/file', 'javascript:alert(1)'))
def test_map_record_rejects_non_http_mod_urls(url: str) -> None:
    with pytest.raises(ValueError):
        MapRecord(created_at=datetime(2026, 10, 10, tzinfo=UTC), map_name='Map', mod_url=url)
