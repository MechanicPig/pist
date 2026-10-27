"""Correct registration order without changing record identities or public content."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.text import Text
from sheet_factory import FakeSheet, make_sheet_fields
from textual import events
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from berries.game.duration import Duration
from pist.records.models import MapRecord
from pist.records.order import plan_record_move
from pist.records.store import RecordSheetLink, RecordStorageError, RecordStore, RecordSyncState
from pist.records.sync import RecordSyncService
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.fields import load_sheet_fields
from pist.ui.records.app import RecordBrowserApp
from pist.ui.records.editor import RecordEditorScreen
from pist.ui.records.list import RecordListScreen
from pist.ui.records.order import RecordOrderScreen
from pist.ui.records.table import RecordTable

NOW = datetime(2026, 10, 13, tzinfo=UTC)


@pytest.mark.parametrize('size', ((80, 24), (160, 48)))
def test_drag_previews_final_row_order_without_changing_numbers(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    original = save_records(store)

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordListScreen)
            table = screen.query_one(RecordTable)
            cells = {row.key: table.get_row(row.key) for row in table.ordered_rows}
            await pilot.mouse_down(table, offset=(12, 1))
            await pilot.hover(table, offset=(12, 2))
            await pilot.pause()
            assert [table.get_row(row.key)[1].plain for row in table.ordered_rows] == [
                'A',
                'B',
                'C',
            ]
            assert screen.selected_id == 1
            # Repeated motion over the moved source must not undo the preview.
            await pilot.hover(table, offset=(14, 2))
            await pilot.pause()
            assert [table.get_row(row.key)[1].plain for row in table.ordered_rows] == [
                'A',
                'B',
                'C',
            ]
            await pilot.hover(table, offset=(12, 3))
            await pilot.pause()
            assert [table.get_row(row.key)[1].plain for row in table.ordered_rows] == [
                'A',
                'C',
                'B',
            ]
            assert [table.get_row(row.key)[0].plain for row in table.ordered_rows] == [
                '2',
                '3',
                '1',
            ]
            assert all(table.get_row(key) == values for key, values in cells.items())
            assert tuple(entry.record for entry in store.list_records()) == original
            await pilot.mouse_up(table, offset=(12, 3))
            await pilot.pause()
            assert isinstance(app.screen, RecordOrderScreen)
            expected = plan_record_move(original, 1, 3, before=False)
            assert str(app.screen.query_one('#record-order-changes', Static).content) == '\n'.join(
                f'{change.old_number} → {change.new_number} · {change.map_name}'
                for change in expected.changes
            )
            await pilot.click('#record-order-cancel')
            await app.workers.wait_for_complete()
            assert [table.get_row(row.key)[1].plain for row in table.ordered_rows] == [
                'B',
                'A',
                'C',
            ]
            await pilot.mouse_down(table, offset=(12, 1))
            await pilot.hover(table, offset=(12, 3))
            await pilot.hover(table, offset=(12, 7))
            await pilot.pause()
            assert [table.get_row(row.key)[1].plain for row in table.ordered_rows] == [
                'B',
                'A',
                'C',
            ]
            await pilot.hover(table, offset=(12, 3))
            await pilot.press('escape')
            await pilot.pause()
            assert [table.get_row(row.key)[1].plain for row in table.ordered_rows] == [
                'B',
                'A',
                'C',
            ]
            assert app.mouse_captured is None
            assert tuple(entry.record for entry in store.list_records()) == original

    asyncio.run(run())


def test_drag_suppresses_native_click_after_mouse_release() -> None:
    async def run() -> None:
        app = App[None]()
        async with app.run_test(size=(80, 24)) as pilot:
            table = RecordTable(id='drag-test')
            table.add_column('地图')
            table.add_row(Text('A'), key='1')
            table.add_row(Text('B'), key='2')
            table.reorder_keys = frozenset({'1', '2'})
            await app.mount(table)
            await pilot.pause()
            await pilot.mouse_down(table, offset=(3, 1))
            await pilot.hover(table, offset=(3, 2))
            await pilot.mouse_up(table, offset=(3, 2))
            await pilot.pause()
            assert table.cursor_row == 0
            # Pilot mouse_up omits the extra Click emitted by the native driver.
            table.post_message(
                events.Click(
                    table,
                    x=3,
                    y=2,
                    delta_x=0,
                    delta_y=0,
                    button=1,
                    shift=False,
                    meta=False,
                    ctrl=False,
                    style=table.get_style_at(3, 2),
                )
            )
            await pilot.pause()
            assert table.cursor_row == 0
            assert app.mouse_captured is None

    asyncio.run(run())


def test_filter_refresh_cancels_drag_preview_with_duplicate_names(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    for _ in range(3):
        store.save(
            MapRecord(
                created_at=NOW,
                map_name='Same',
                status='进行中',
                time_played=Duration.from_dotnet_ticks(600000000),
            )
        )
    original = store.list_records()

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordListScreen)
            table = screen.query_one(RecordTable)
            keys = [row.key.value for row in table.ordered_rows]
            await pilot.mouse_down(table, offset=(12, 1))
            await pilot.hover(table, offset=(12, 3))
            await pilot.pause()
            assert [row.key.value for row in table.ordered_rows] == [*keys[1:], keys[0]]
            assert keys[0] is not None
            assert screen.selected_id == int(keys[0])
            screen.query_one('#record-search', Input).value = 'Same'
            await pilot.pause()
            assert [row.key.value for row in table.ordered_rows] == keys
            assert app.mouse_captured is None
            await pilot.mouse_up(table, offset=(12, 3))
            table.post_message(
                events.Click(
                    table,
                    x=12,
                    y=3,
                    delta_x=0,
                    delta_y=0,
                    button=1,
                    shift=False,
                    meta=False,
                    ctrl=False,
                    style=table.get_style_at(12, 3),
                )
            )
            await pilot.pause()
            assert app.screen is screen
            assert store.list_records() == original

    asyncio.run(run())


def test_plain_row_click_still_opens_editor_without_reordering(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(RecordTable)
            await pilot.click(table, offset=(12, 1))
            await pilot.pause()
            # DataTable first selects a cell; clicking that selected cell activates it.
            await pilot.click(table, offset=(12, 1))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert app.mouse_captured is None
            app.screen.dismiss(None)
            await app.workers.wait_for_complete()
            assert tuple(entry.record for entry in store.list_records()) == records

    asyncio.run(run())


@pytest.mark.parametrize('size', ((80, 24), (160, 48)))
@pytest.mark.parametrize('cancel', (False, True))
@pytest.mark.parametrize('down', (False, True))
def test_row_drag_opens_ready_confirmation_without_editing_or_saving_early(
    tmp_path: Path, size: tuple[int, int], cancel: bool, down: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            table = app.screen.query_one(RecordTable)
            source, target = (0, 2) if down else (1, 0)
            await pilot.mouse_down(table, offset=(12, source + 1))
            await pilot.hover(table, offset=(12, target + 1))
            assert tuple(entry.record for entry in store.list_records()) == records
            await pilot.mouse_up(table, offset=(12, target + 1))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordOrderScreen)
            assert not screen.query('#record-order-target')
            assert str(screen.query_one('#record-order-changes', Static).content)
            assert not screen.query_one('#record-order-save', Button).disabled
            assert app.mouse_captured is None
            assert tuple(entry.record for entry in store.list_records()) == records
            assert await pilot.click('#record-order-cancel' if cancel else '#record-order-save')
            await app.workers.wait_for_complete()
            assert [entry.record.map_name for entry in store.list_records()] == (
                ['B', 'A', 'C'] if cancel else (['A', 'C', 'B'] if down else ['A', 'B', 'C'])
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    'drop', ('same', 'header', 'blank', 'outside', 'unstarted', 'unstarted-target', 'escape')
)
def test_invalid_or_cancelled_row_drag_never_changes_order(tmp_path: Path, drop: str) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    save_records(store)
    store.save(MapRecord(created_at=NOW, map_name='Unstarted', status='未开始'))
    original = store.list_records()

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            table = screen.query_one(RecordTable)
            source = 4 if drop == 'unstarted' else 1
            await pilot.mouse_down(table, offset=(12, source))
            await pilot.hover(table, offset=(12, 2))
            if drop == 'escape':
                await pilot.press('escape')
                await pilot.mouse_up(table, offset=(12, 2))
            elif drop == 'outside':
                await pilot.hover(offset=(1, table.region.bottom + 1))
                await pilot.mouse_up(offset=(1, table.region.bottom + 1))
            else:
                target = {
                    'same': 1,
                    'header': 0,
                    'blank': 7,
                    'unstarted': 1,
                    'unstarted-target': 4,
                }[drop]
                await pilot.hover(table, offset=(12, target))
                await pilot.mouse_up(table, offset=(12, target))
            await pilot.pause()
            assert app.screen is screen
            assert store.list_records() == original
            assert app.mouse_captured is None

    asyncio.run(run())


def test_drag_in_filtered_scrolled_list_uses_stable_ids_and_full_order(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    for number in range(1, 41):
        store.save(
            MapRecord(
                created_at=NOW,
                map_name=f'{"Visible" if number % 2 else "Hidden"} {number}',
                record_number=number,
            )
        )
    original = store.list_records()

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.screen.query_one('#record-search', Input).value = 'Visible'
            await pilot.pause()
            table = app.screen.query_one(RecordTable)
            table.scroll_to(y=5, x=3, animate=False)
            await pilot.pause()
            source_index = table.get_style_at(12, 2).meta['row']
            target_index = table.get_style_at(12, 5).meta['row']
            source_key = table.ordered_rows[source_index].key.value
            target_key = table.ordered_rows[target_index].key.value
            assert source_key is not None and target_key is not None and source_index >= 5
            await pilot.mouse_down(table, offset=(12, 2))
            await pilot.hover(table, offset=(12, 5))
            await pilot.mouse_up(table, offset=(12, 5))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordOrderScreen)
            expected = plan_record_move(
                tuple(entry.record for entry in original),
                int(source_key),
                int(target_key),
                before=False,
            )
            assert str(screen.query_one('#record-order-changes', Static).content) == '\n'.join(
                f'{change.old_number} → {change.new_number} · {change.map_name}'
                for change in expected.changes
            )
            assert any(change.map_name.startswith('Hidden') for change in expected.changes)
            await pilot.click('#record-order-save')
            await app.workers.wait_for_complete()
            assert store.load(int(source_key)).record_number == int(target_key)
            assert len(store.list_records()) == 40

    asyncio.run(run())


def save_records(store: RecordStore) -> tuple[MapRecord, ...]:
    return tuple(
        store.load(
            store.save(
                MapRecord(
                    created_at=NOW,
                    map_name=name,
                    time_played=Duration.from_dotnet_ticks(600000000),
                    status='通关',
                    save_slot=7,
                    notes=f'{name} notes',
                )
            )
        )
        for name in ('B', 'A', 'C')
    )


@pytest.mark.parametrize(
    ('source', 'target', 'before', 'names'),
    (
        (2, 1, True, ('A', 'B', 'C')),
        (1, 3, False, ('A', 'C', 'B')),
        (3, 1, True, ('C', 'B', 'A')),
        (1, 2, True, ('B', 'A', 'C')),
    ),
)
def test_move_preserves_id_contents_baselines_and_counter(
    tmp_path: Path, source: int, target: int, before: bool, names: tuple[str, ...]
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)
    for record in records:
        assert record.local_id is not None
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id,
                file_id='file',
                sheet_id='main',
                record_id=f'remote-{record.local_id}',
            ),
            {'编号': record.record_number, '备注': record.notes},
            submitted=record,
            confirmed_at=NOW,
        )
    links = {
        record.local_id: store.sheet_links(record.local_id) for record in records if record.local_id
    }
    plan = plan_record_move(records, source, target, before=before)
    store.apply_order(plan)
    entries = store.list_records()
    assert tuple(entry.record.map_name for entry in entries) == names
    assert tuple(entry.record.record_number for entry in entries) == (1, 2, 3)
    changed = {change.local_id for change in plan.changes}
    for record in records:
        assert record.local_id is not None
        after = store.load(record.local_id)
        assert after.model_copy(update={'record_number': record.record_number}) == record
        assert store.sheet_links(record.local_id) == links[record.local_id]
        assert store.sync_state(record.local_id) is (
            RecordSyncState.PENDING if record.local_id in changed else RecordSyncState.SYNCED
        )
    assert (
        store.load(
            store.save(
                MapRecord(
                    created_at=NOW,
                    map_name='New',
                    time_played=Duration.from_dotnet_ticks(600000000),
                    status='进行中',
                )
            )
        ).record_number
        == 4
    )


def test_order_plan_sorts_unsorted_numbers_and_ignores_unnumbered_records() -> None:
    records = (
        MapRecord(created_at=NOW, local_id=1, record_number=9, map_name='Later'),
        MapRecord(created_at=NOW, local_id=2, record_number=1, map_name='Earlier'),
        MapRecord(created_at=NOW, local_id=3, map_name='Unnumbered', status='未开始'),
    )
    plan = plan_record_move(records, 1, 2, before=True)
    assert plan.expected == ((2, 1), (1, 9))
    assert [(change.local_id, change.old_number, change.new_number) for change in plan.changes] == [
        (1, 9, 1),
        (2, 1, 9),
    ]


def test_unstarted_and_number_gaps_remain_unchanged(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = tuple(
        store.load(
            store.save(MapRecord(created_at=NOW, map_name=str(number), record_number=number))
        )
        for number in (1, 4, 9)
    )
    unstarted = store.load(
        store.save(MapRecord(created_at=NOW, map_name='Unstarted', status='未开始'))
    )
    assert records[0].local_id and records[2].local_id and unstarted.local_id
    store.apply_order(
        plan_record_move(records, records[2].local_id, records[0].local_id, before=True)
    )
    assert [entry.record.record_number for entry in store.list_records()] == [1, 4, 9, None]
    assert store.load(unstarted.local_id) == unstarted
    with pytest.raises(ValueError, match='已编号'):
        plan_record_move(
            (*records, unstarted), unstarted.local_id, records[0].local_id, before=True
        )


def test_stale_or_invalid_plan_cannot_change_records(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)
    plan = plan_record_move(records, 2, 1, before=True)
    store.apply_order(plan)
    after = store.list_records()
    with pytest.raises(ValueError, match='顺序已变化'):
        store.apply_order(plan)
    assert store.list_records() == after
    current = tuple(entry.record for entry in after)
    bad = plan_record_move(current, 1, 2, before=True)
    bad = bad.model_copy(
        update={'changes': (bad.changes[0].model_copy(update={'new_number': 99}),)}
    )
    with pytest.raises(ValueError, match='有效的顺序置换'):
        store.apply_order(bad)
    assert store.list_records() == after


def test_storage_failure_rolls_back_entire_number_permutation(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)
    original = store.list_records()
    with store.connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_number BEFORE UPDATE OF record_number ON records WHEN NEW.record_number = 2 BEGIN SELECT RAISE(ABORT, 'rejected'); END"
        )
    with pytest.raises(RecordStorageError, match='rejected'):
        store.apply_order(plan_record_move(records, 2, 1, before=True))
    assert store.list_records() == original


def test_number_projection_and_remote_number_conflict(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)
    client = FakeSheet()
    client.fields = make_sheet_fields(
        ('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 1), ('编号', 2)
    )
    service = RecordSyncService(store, client, 'source')
    record = records[0]
    assert record.local_id is not None
    values = encode_record_values(record, client.fields)
    client.rows['remote'] = values
    store.confirm_sheet_record(
        RecordSheetLink(
            local_id=record.local_id, file_id='file', sheet_id='main', record_id='remote'
        ),
        values,
        submitted=record,
        confirmed_at=NOW,
    )
    client.rows['remote']['编号'] = 9
    check = asyncio.run(service.check(record))
    assert [difference.title for difference in check.differences] == ['编号']
    with pytest.raises(ValueError, match='远端编号'):
        service.use_remote(record, check)
    assert (
        load_sheet_fields().apply_remote(record, client.rows['remote'], client.fields).record_number
        == 1
    )
    assert store.load(record.local_id).record_number == 1
    client.fields = make_sheet_fields(('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 1))
    missing = asyncio.run(service.check(record))
    assert missing.issues == ('缺少已确认同步的字段：编号，不能忽略编号确认同步。',)
    with pytest.raises(ValueError, match='缺少已确认同步'):
        asyncio.run(service.save(record, missing))


@pytest.mark.parametrize('existing', (False, True))
def test_first_number_allocation_is_published_after_local_save(
    tmp_path: Path, existing: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.fields = make_sheet_fields(('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('编号', 2))
    record = MapRecord(created_at=NOW, map_name='New', status='未开始')
    if existing:
        record = store.load(store.save(record))
        assert record.local_id is not None
        values = encode_record_values(record, client.fields)
        client.rows['remote'] = values
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id, file_id='file', sheet_id='main', record_id='remote'
            ),
            values,
            submitted=record,
            confirmed_at=NOW,
        )
    record = record.model_copy(
        update={'status': '进行中', 'time_played': Duration.from_dotnet_ticks(600000000)}
    )
    service = RecordSyncService(store, client, 'source')
    result = asyncio.run(service.save(record, asyncio.run(service.check(record))))
    assert result.synced and result.record.record_number == 1
    assert client.rows['remote' if existing else 'created']['编号'] == 1


@pytest.mark.parametrize('size', ((80, 24), (160, 48)))
@pytest.mark.parametrize('cancel', (False, True))
def test_order_ui_previews_and_commits_only_after_confirmation(
    tmp_path: Path, size: tuple[int, int], cancel: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)

    async def run() -> None:
        app = RecordBrowserApp(store)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert await pilot.click('#record-list-order')
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordOrderScreen)
            screen.query_one('#record-order-target', Input).value = '2'
            screen.query_one('#record-order-position', Select).value = False
            await pilot.pause()
            assert (
                len(str(screen.query_one('#record-order-changes', Static).content).splitlines())
                == 2
            )
            assert tuple(entry.record for entry in store.list_records()) == records
            assert await pilot.click('#record-order-cancel' if cancel else '#record-order-save')
            await app.workers.wait_for_complete()
            assert [entry.record.map_name for entry in store.list_records()] == (
                ['B', 'A', 'C'] if cancel else ['A', 'B', 'C']
            )
            assert not app.screen.query_one('#record-list-order', Button).disabled

    asyncio.run(run())


@pytest.mark.parametrize('failure', ('none', 'read', 'write', 'missing-number'))
def test_order_ui_syncs_exact_associations_or_keeps_number_changes_pending(
    tmp_path: Path, failure: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    records = save_records(store)
    client = FakeSheet()
    client.fields = make_sheet_fields(
        ('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 1), ('编号', 2)
    )
    for record in records:
        assert record.local_id is not None
        remote_id = f'remote-{record.local_id}'
        values = encode_record_values(record, client.fields)
        client.rows[remote_id] = values
        store.confirm_sheet_record(
            RecordSheetLink(
                local_id=record.local_id, file_id='file', sheet_id='main', record_id=remote_id
            ),
            values,
            submitted=record,
            confirmed_at=NOW,
        )
    if failure == 'missing-number':
        client.fields = make_sheet_fields(
            ('地图名', 1), ('Mod元数据名', 1), ('状态', 17), ('备注', 1)
        )
    client.read_error = failure == 'read'
    client.write_error = failure == 'write'

    async def run() -> None:
        app = RecordBrowserApp(store, client=client, source='source')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click('#record-list-order')
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordOrderScreen)
            screen.query_one('#record-order-target', Input).value = '2'
            screen.query_one('#record-order-position', Select).value = False
            await pilot.pause()
            await pilot.click('#record-order-save')
            await app.workers.wait_for_complete()
            assert [entry.record.map_name for entry in store.list_records()] == ['A', 'B', 'C']
            for id_ in (1, 2):
                assert store.sync_state(id_) is (
                    RecordSyncState.SYNCED if failure == 'none' else RecordSyncState.PENDING
                )
                assert store.sheet_links(id_)[0].record_id == f'remote-{id_}'
            assert store.sync_state(3) is RecordSyncState.SYNCED
            if failure == 'none':
                assert client.rows['remote-1']['编号'] == 2
                assert client.rows['remote-2']['编号'] == 1
                assert len(client.update_batches) == 1
            assert client.rows['remote-1']['备注'] == [{'type': 'text', 'text': 'B notes'}]

    asyncio.run(run())
