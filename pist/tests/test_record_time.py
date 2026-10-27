"""Duration precision at record, SQLite, sheet and editing boundaries."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError
from sheet_factory import make_sheet_fields
from textual.app import App
from textual.widgets import Input

from berries.game.duration import Duration
from pist.records.models import MAX_RECORD_TICKS, MapRecord
from pist.records.store import RecordStore
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.fields import load_sheet_fields
from pist.ui.records.editor import RECORD_FIELD_ORDER, RecordEditorScreen

NOW = datetime(2026, 10, 16, tzinfo=UTC)


@pytest.mark.parametrize('ticks', (0, 1, 834_560_001, 1_838_450_060_001, MAX_RECORD_TICKS))
def test_record_storage_preserves_every_tick(tmp_path: Path, ticks: int) -> None:
    value = Duration.from_dotnet_ticks(ticks)
    record = MapRecord(created_at=NOW, map_name='Map', time_played=value)
    assert MapRecord.model_validate_json(record.model_dump_json()) == record
    store = RecordStore(tmp_path / 'records.sqlite3')
    local_id = store.save(record)
    assert store.load(local_id).time_played == value
    with store.connect() as conn:
        assert conn.execute('SELECT time_played, typeof(time_played) FROM records').fetchone() == (
            ticks,
            'integer',
        )


@pytest.mark.parametrize(
    'value',
    (
        Duration.from_dotnet_ticks(-1),
        Duration(Fraction(1, 3)),
        Duration.from_dotnet_ticks(MAX_RECORD_TICKS + 1),
    ),
)
def test_record_model_rejects_unrepresentable_time(value: Duration) -> None:
    with pytest.raises(ValidationError):
        MapRecord(created_at=NOW, map_name='Map', time_played=value)


@pytest.mark.parametrize('invalid', (-1, 'invalid', 1.5))
def test_sqlite_rejects_invalid_time_values(tmp_path: Path, invalid: object) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    local_id = store.save(MapRecord(created_at=NOW, map_name='Map'))
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute('UPDATE records SET time_played = ? WHERE id = ?', (invalid, local_id))
    assert store.load(local_id).time_played is None


@pytest.mark.parametrize('remote', ('0:01:23', '0:01:24', None))
def test_sheet_roundtrip_preserves_precision_unless_remote_time_changes(remote: str | None) -> None:
    value = Duration.from_dotnet_ticks(834_560_001)
    record = MapRecord(created_at=NOW, map_name='Map', time_played=value)
    fields = make_sheet_fields(('用时', 1))
    cells = encode_record_values(record, fields)
    assert cells == {'用时': [{'type': 'text', 'text': '0:01:23'}]}
    adopted = load_sheet_fields().apply_remote(
        record, {'用时': [] if remote is None else [{'type': 'text', 'text': remote}]}, fields
    )
    assert adopted.time_played == (
        None if remote is None else value if remote == '0:01:23' else Duration.from_seconds(84)
    )


@pytest.mark.parametrize('text', ('0:01:23.1', '1:60:00', '-1:00:00', '999999999:00:00'))
def test_sheet_time_requires_fixed_seconds_and_a_valid_record_range(text: str) -> None:
    record = MapRecord(created_at=NOW, map_name='Map')
    with pytest.raises(ValueError):
        load_sheet_fields().apply_remote(
            record, {'用时': [{'type': 'text', 'text': text}]}, make_sheet_fields(('用时', 1))
        )


@pytest.mark.parametrize(
    'initial_ticks,replacement,expected_ticks',
    (
        (834_560_001, None, 834_560_001),
        (834_560_001, '1:23.456', 834_560_001),
        (834_560_001, '1:23.000', 830_000_000),
        (834_560_001, '1:24.123', 841_230_000),
        (834_560_001, '0:00.001', 10_000),
        (834_560_001, '1:02:03.456', 37_234_560_000),
        (834_560_001, '', None),
        (0, None, 0),
        (1, None, 1),
        (MAX_RECORD_TICKS, None, MAX_RECORD_TICKS),
    ),
)
def test_record_editor_preserves_or_explicitly_replaces_time(
    initial_ticks: int, replacement: str | None, expected_ticks: int | None
) -> None:
    results: list[MapRecord | None] = []
    value = Duration.from_dotnet_ticks(initial_ticks)

    async def run() -> None:
        app = App[None](css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')
        async with app.run_test(size=(160, 55)) as pilot:
            screen = RecordEditorScreen(
                MapRecord(created_at=NOW, map_name='Map', time_played=value)
            )
            await app.push_screen(screen, results.append)
            control = screen.query_one(f'#record-manual-{RECORD_FIELD_ORDER.index("用时")}', Input)
            assert control.value == str(value)
            if replacement is None:
                name = screen.query_one(
                    f'#record-manual-{RECORD_FIELD_ORDER.index("地图名")}', Input
                )
                name.value = 'Edited Map'
            if replacement is not None:
                control.value = replacement
            assert await pilot.click('#record-confirm-save')
            await pilot.pause()

    asyncio.run(run())
    assert len(results) == 1 and results[0] is not None
    expected = None if expected_ticks is None else Duration.from_dotnet_ticks(expected_ticks)
    assert results[0].time_played == expected
    if replacement is None:
        assert results[0].map_name == 'Edited Map'


@pytest.mark.parametrize(
    'text',
    (
        '-1:00.000',
        '999999999:00:00.000',
        '1:60.000',
        '1:00:60.000',
        '1:2:03.000',
        '1:23.1234567',
        '1:23.1234',
        '1:23.',
        '1:23.a',
        '01:00.000',
        ' 1:00.000',
    ),
)
def test_record_editor_keeps_invalid_time_open_for_correction(text: str) -> None:
    results: list[MapRecord | None] = []

    async def run() -> None:
        app = App[None](css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')
        async with app.run_test(size=(160, 55)) as pilot:
            screen = RecordEditorScreen(MapRecord(created_at=NOW, map_name='Map'))
            await app.push_screen(screen, results.append)
            control = screen.query_one(f'#record-manual-{RECORD_FIELD_ORDER.index("用时")}', Input)
            control.value = text
            assert await pilot.click('#record-confirm-save')
            await pilot.pause()
            assert app.screen is screen
            assert control.value == text
            assert not results

    asyncio.run(run())
