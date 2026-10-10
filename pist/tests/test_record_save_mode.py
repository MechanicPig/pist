import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Select, Static

from pist.records.models import MapRecord
from pist.ui.records.editor import RecordSaveModeScreen


@pytest.mark.parametrize(
    ('button', 'expected'),
    (
        ('record-save-add', 0),
        ('record-save-update', 8),
        ('record-save-cancel', None),
    ),
)
def test_record_save_mode_uses_explicit_target(button: str, expected: int | None) -> None:
    candidates = tuple(
        MapRecord(
            local_id=local_id,
            record_number=number,
            created_at=datetime(2026, 10, 4, tzinfo=UTC),
            mod_metadata_name='Example',
            map_name='Map',
        )
        for local_id, number in ((3, 144), (8, 167))
    )
    results: list[int | None] = []

    async def run() -> None:
        app = App(css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')
        async with app.run_test(size=(120, 40)) as pilot:
            await app.push_screen(RecordSaveModeScreen(candidates, save_slot=0), results.append)
            await pilot.pause()
            app.screen.query_one('#record-save-target', Select).value = 8
            await pilot.pause()
            assert await pilot.click(f'#{button}')
            await pilot.pause()

    asyncio.run(run())
    assert results == [expected]


@pytest.mark.parametrize(
    ('save_slot', 'candidate_slots', 'target_id'),
    (
        (0, (0,), 1),
        (0, (1,), None),
        (0, (None,), None),
        (None, (None,), None),
        (0, (1, None, 0), 3),
    ),
)
def test_record_save_mode_defaults_only_to_known_same_slot(
    save_slot: int | None, candidate_slots: tuple[int | None, ...], target_id: int | None
) -> None:
    candidates = tuple(
        MapRecord(
            local_id=index,
            record_number=index,
            created_at=datetime(2026, 10, 4, tzinfo=UTC),
            mod_metadata_name='Example',
            map_name='Map',
            save_slot=slot,
        )
        for index, slot in enumerate(candidate_slots, start=1)
    )

    async def run() -> None:
        app = App(css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')
        async with app.run_test(size=(120, 40)) as pilot:
            await app.push_screen(RecordSaveModeScreen(candidates, save_slot=save_slot))
            await pilot.pause()
            selector = app.screen.query_one('#record-save-target', Select)
            assert selector.value == (Select.NULL if target_id is None else target_id)
            update = app.screen.query_one('#record-save-update', Button)
            assert update.disabled == (target_id is None)
            assert app.focused is app.screen.query_one(
                '#record-save-add' if target_id is None else '#record-save-update', Button
            )
            selector.value = 1
            await pilot.pause()
            assert not update.disabled
            warning = app.screen.query_one('#record-save-warning', Static)
            assert warning.display == (save_slot is None or candidates[0].save_slot != save_slot)
            if target_id is None:
                selector.value = Select.NULL
                await pilot.pause()
                assert update.disabled
                assert not warning.display
            else:
                selector.value = target_id
                await pilot.pause()
                assert not warning.display

    asyncio.run(run())
