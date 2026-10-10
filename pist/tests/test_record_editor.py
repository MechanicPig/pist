import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sheet_factory import FakeSheet, make_sheet_fields
from textual.app import App
from textual.widgets import Button, Input, Select, Static, TextArea

from pist.records.fields import RECORD_ATTRIBUTES
from pist.records.models import MapRecord
from pist.records.store import RecordSheetLink, RecordStore, RecordSyncState
from pist.records.sync import RecordSyncService
from pist.smartsheet.encoding import encode_record_values
from pist.smartsheet.report import ManualRecordField
from pist.ui.records.controller import RecordEditorController
from pist.ui.records.editor import (
    RECORD_FIELD_ORDER,
    RecordEditorScreen,
    RecordSaveModeScreen,
)
from pist.ui.records.recovery import RecordAddRecoveryScreen, RecordAddRetryScreen
from pist.ui.records.sync import RecordConflictScreen, RecordOfflineScreen

NOW = datetime(2026, 10, 10, tzinfo=UTC)
CSS = Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss'


def test_one_line_text_scrolls_with_cursor_without_scrollbars(record: MapRecord) -> None:
    async def run() -> None:
        app = App[None]()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = RecordEditorScreen(record.model_copy(update={'notes': '长备注' * 80}))
            await app.push_screen(screen)
            await pilot.pause()
            field = screen._manual_field('备注')
            assert field is not None
            control = screen.query_one(f'#{screen._manual_field_id(field)}', Input)
            control.focus()
            await pilot.press('end')
            await pilot.pause()
            assert control.scroll_x > 0
            assert not control.show_horizontal_scrollbar
            assert not control.show_vertical_scrollbar
            assert control.region.height == 1
            await pilot.press('home')
            await pilot.pause()
            assert control.scroll_x == 0
            assert control.value == '长备注' * 80

    asyncio.run(run())


def test_sid_stays_one_line_and_read_only_with_cursor_navigation(record: MapRecord) -> None:
    sid = 'Coolors/Merging Madness/' + 'Long map name ' * 30

    async def run() -> None:
        app = App[None]()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = RecordEditorScreen(record.model_copy(update={'sid': sid}))
            await app.push_screen(screen)
            await pilot.pause()
            control = screen.query_one('#record-sid', Input)
            assert control.region.height == 1
            control.focus()
            await pilot.press('end')
            await pilot.pause()
            assert control.scroll_x > 0
            await pilot.press('x', 'backspace', 'delete', 'ctrl+shift+a', 'ctrl+x')
            assert app.clipboard == sid
            await pilot.press('ctrl+v', 'home')
            await pilot.pause()
            assert control.value == sid
            assert control.scroll_x == 0
            assert control.region.height == 1
            assert not control.show_horizontal_scrollbar

    asyncio.run(run())


def test_tags_use_one_line_input_and_preserve_individual_values(record: MapRecord) -> None:
    async def run() -> None:
        app = App[None]()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = RecordEditorScreen(record.model_copy(update={'tags': ('a,b', '特殊')}))
            await app.push_screen(screen)
            await pilot.pause()
            field = screen._manual_field('标签')
            assert field is not None
            control = screen.query_one(f'#{screen._manual_field_id(field)}', Input)
            assert control.value == '"a,b",特殊'
            values = screen._manual_values()
            assert values is not None and values['tags'] == ('a,b', '特殊')
            control.value = '"a,b", 新标签, 新标签'
            values = screen._manual_values()
            assert values is not None and values['tags'] == ('a,b', '新标签')
            control.value = ''
            values = screen._manual_values()
            assert values is not None and values['tags'] == ()

    asyncio.run(run())


@pytest.mark.parametrize('size', ((80, 24), (120, 40), (180, 60)))
@pytest.mark.parametrize('extra', ('none', 'author', 'route', 'both'))
def test_editor_labels_and_order_are_independent_of_remote_column_order(
    record: MapRecord, size: tuple[int, int], extra: str
) -> None:
    remote_fields = tuple(
        ManualRecordField(title, 1) for title in reversed(RECORD_ATTRIBUTES) if title != '作者'
    )

    async def run() -> None:
        app = App[None]()
        async with app.run_test(size=size) as pilot:
            screen = RecordEditorScreen(
                record,
                manual_fields=remote_fields,
                author_source='Author' if extra in ('author', 'both') else None,
                edit_route=(lambda: None) if extra in ('route', 'both') else None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            labels = list(screen.query('.record-field-label').results(Static))
            expected = (
                [
                    'Mod 名',
                    'Mod 元数据名',
                    '地图名',
                    '作者',
                    'SID',
                    '合集标签',
                    '更新时间',
                    'Mod 链接',
                ]
                + (['作者来源'] if extra in ('author', 'both') else [])
                + [
                    '存档编号',
                    '存档通关参考',
                    '起始日期',
                    '结束日期',
                    '用时',
                    '死亡数',
                    '红草莓数',
                    '月莓数',
                    '磁带',
                    '水晶之心',
                    '主房间数',
                    '状态',
                ]
                + (['路线'] if extra in ('route', 'both') else [])
                + [
                    'SL 使用',
                    '视频链接',
                    '体感难度',
                    '难度子阶',
                    '标注难度',
                    '难度子阶',
                    '评分',
                    '标签',
                    '备注',
                ]
            )
            sections = list(screen.query('.record-section-title').results(Static))
            assert [str(section.content) for section in sections] == [
                '地图信息',
                '初见记录',
                '评价与备注',
            ]
            assert all(section.content_region.height == 1 for section in sections)
            assert [str(label.content) for label in labels] == expected
            assert set(RECORD_FIELD_ORDER) == set(RECORD_ATTRIBUTES) - {'更新时间'}
            assert all(label.region.height == 1 for label in labels)
            assert all(control.styles.margin == (0, 0, 0, 0) for control in screen.query(Input))
            for control in screen.query('.record-list-control').results(TextArea):
                assert control.region.height == 1
                assert control.styles.margin == (0, 0, 0, 0)
                assert control.styles.padding == (0, 0, 0, 0)
                assert control.content_region == control.region
                control.focus()
                await pilot.pause()
                assert control.content_region == control.region
            grid = screen.query_one('#record-fields')
            assert grid.has_class('record-fields-narrow') is (size[0] < 120)
            # Text controls still refer to protocol titles, not the display labels.
            field = screen._manual_field('Mod元数据名')
            assert field is not None
            assert screen.query_one(f'#{screen._manual_field_id(field)}', Input).value == 'Example'
            if size[0] >= 120:
                for difficulty, tier in (('体感难度', '难度子阶'), ('标注难度', '标注难度子阶')):
                    fields = [screen._manual_field(title) for title in (difficulty, tier)]
                    assert all(field is not None for field in fields)
                    controls = [
                        screen.query_one(f'#{screen._manual_field_id(field)}')
                        for field in fields
                        if field is not None
                    ]
                    assert controls[0].region.y == controls[1].region.y

    asyncio.run(run())


@pytest.fixture
def record() -> MapRecord:
    return MapRecord(
        created_at=NOW,
        map_name='Map',
        mod_metadata_name='Example',
        map_file='Maps/Map.bin',
        save_slot=0,
        time_played='0:01:00',
        status='进行中',
        notes='A',
    )


def editor(record: MapRecord, fields: tuple[ManualRecordField, ...]) -> RecordEditorScreen:
    return RecordEditorScreen(record, manual_fields=fields)


@pytest.mark.parametrize('choice', ('bind', 'retry', 'cancel', 'local', 'cancel-retry'))
@pytest.mark.parametrize('size', ((120, 40), (80, 24)))
def test_uncertain_add_recovery_requires_explicit_choice_and_preserves_protection_on_cancel(
    tmp_path: Path, record: MapRecord, choice: str, size: tuple[int, int]
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service = RecordSyncService(store, client, 'source')
    client.write_error = True
    saved = asyncio.run(service.save(record, asyncio.run(service.check(record)))).record
    client.write_error = False
    assert saved.local_id is not None
    if choice == 'bind':
        client.rows['candidate'] = encode_record_values(saved, client.fields)

    async def run() -> None:
        assert saved.local_id is not None
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=size) as pilot:
            worker = app.run_worker(controller._prepare(saved))
            await pilot.pause()
            assert isinstance(app.screen, RecordAddRecoveryScreen)
            assert app.screen.query_one('#record-add-bind', Button).disabled
            assert app.screen.query_one('#record-add-target', Select).value is Select.NULL
            if choice == 'bind':
                assert app.screen.query_one('#record-add-retry', Button).disabled
                app.screen.query_one('#record-add-target', Select).value = 'candidate'
                await pilot.pause()
                assert await pilot.click('#record-add-bind')
            elif choice in ('retry', 'cancel-retry'):
                await pilot.click('#record-add-retry')
                await pilot.pause()
                assert isinstance(app.screen, RecordAddRetryScreen)
                assert store.sheet_add_attempts(saved.local_id)
                if choice == 'retry':
                    await pilot.click('#record-add-retry-confirmed')
                else:
                    await pilot.press('escape')
                    await pilot.pause()
                    assert isinstance(app.screen, RecordAddRecoveryScreen)
                    await pilot.press('escape')
            else:
                await pilot.click(f'#record-add-{choice}')
            result = await asyncio.wait_for(worker.wait(), timeout=5)
            if choice == 'bind':
                assert result is not None and result[0] is not None
                assert result[0].differences == ()
                assert store.sheet_links(saved.local_id)[0].confirmed_values is None
            elif choice == 'retry':
                assert result is not None and result[0] is not None
                assert result[0].remote is None
            elif choice == 'local':
                assert result is not None and result[0] is None and result[1] is not None
            else:
                assert result == (None, None)

    asyncio.run(run())
    assert bool(store.sheet_add_attempts(saved.local_id)) == (
        choice in ('cancel', 'local', 'cancel-retry')
    )
    assert len(client.writes) == 1  # Recovery itself never writes remote data.


@pytest.mark.parametrize('bind', (False, True))
def test_editing_resumes_normal_conflict_and_save_flow_after_recovery(
    tmp_path: Path, record: MapRecord, bind: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    service = RecordSyncService(store, client, 'source')
    client.write_error = True
    saved = asyncio.run(service.save(record, asyncio.run(service.check(record)))).record
    client.write_error = False
    if bind:
        client.rows['candidate'] = encode_record_values(saved, client.fields)
        client.rows['candidate']['备注'] = [{'text': 'Remote change'}]

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=(120, 40)) as pilot:
            worker = app.run_worker(controller.edit(saved, editor))
            await pilot.pause()
            await pilot.click('#record-save-update')
            await pilot.pause()
            assert isinstance(app.screen, RecordAddRecoveryScreen)
            if bind:
                app.screen.query_one('#record-add-target', Select).value = 'candidate'
                await pilot.pause()
                await pilot.click('#record-add-bind')
                await pilot.pause()
                assert isinstance(app.screen, RecordConflictScreen)
                await pilot.click('#record-conflict-local')
            else:
                await pilot.click('#record-add-retry')
                await pilot.pause()
                await pilot.click('#record-add-retry-confirmed')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')
            await asyncio.wait_for(worker.wait(), timeout=5)

    asyncio.run(run())
    assert saved.local_id is not None
    assert store.sync_state(saved.local_id) is RecordSyncState.SYNCED
    assert store.sheet_add_attempts(saved.local_id) == ()
    assert [id_ for id_, _ in client.writes] == ([None, 'candidate'] if bind else [None, None])


@pytest.mark.parametrize('sync', (False, True))
def test_storage_failure_is_visible_without_writing_remote_or_crashing_worker(
    tmp_path: Path, record: MapRecord, monkeypatch: pytest.MonkeyPatch, sync: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    with store.connect() as conn:
        conn.execute("""CREATE TRIGGER reject_record_insert BEFORE INSERT ON records BEGIN
            SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    client = FakeSheet()
    messages: list[tuple[str, str]] = []

    async def run() -> None:
        app = App[None](css_path=CSS)

        def notify(message: str, *, severity: str = 'information', **kwargs: object) -> None:
            messages.append((message, severity))

        monkeypatch.setattr(app, 'notify', notify)
        controller = RecordEditorController(
            app,
            store,
            client=client if sync else None,
            source='source' if sync else None,
            manual_fields=(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            worker = app.run_worker(controller.edit(record, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')
            await worker.wait()
            assert not isinstance(app.screen, RecordOfflineScreen)
        assert not controller.allow_offline

    asyncio.run(run())
    assert messages and messages[-1][1] == 'error'
    assert 'simulated storage failure' in messages[-1][0]
    assert client.writes == []
    assert store.matching_ids(record) == ()


@pytest.mark.parametrize('choice', ('add', 'update', 'cancel'))
def test_cross_slot_save_adds_by_default_or_explicitly_overwrites_without_accumulating(
    tmp_path: Path, record: MapRecord, choice: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    saved = store.load(store.save(record.model_copy(update={'deaths': 2})))
    current = record.model_copy(
        update={'save_slot': 1, 'time_played': '0:02:00', 'deaths': 3, 'notes': 'new experience'}
    )

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(app, store, client=None, source=None, manual_fields=())
        async with app.run_test(size=(120, 40)) as pilot:
            app.run_worker(controller.edit(current, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordSaveModeScreen)
            if choice == 'update':
                app.screen.query_one('#record-save-target', Select).value = saved.local_id
                await pilot.pause()
            await pilot.click(f'#record-save-{choice}')
            await pilot.pause()
            if choice == 'cancel':
                assert store.matching_ids(record) == (saved.local_id,)
                assert store.load(1) == saved
                return
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')
            await pilot.pause()

        latest = store.load(1 if choice == 'update' else 2)
        assert latest.save_slot == 1
        assert latest.time_played == '0:02:00'
        assert latest.deaths == 3
        if choice == 'update':
            assert latest.record_number == saved.record_number
            assert latest.notes == saved.notes
            assert store.matching_ids(record) == (1,)
        else:
            assert latest.record_number != saved.record_number
            assert latest.notes == current.notes
            assert store.load(1) == saved
            assert store.matching_ids(record) == (1, 2)

    asyncio.run(run())


def test_offline_consent_is_reused_only_within_current_controller(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.read_error = True

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=(120, 40)) as pilot:
            app.run_worker(controller.edit(record, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordOfflineScreen)
            await pilot.click('#record-offline-local')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')
            await pilot.pause()
            assert store.sync_state(1) is RecordSyncState.PENDING
            assert controller.allow_offline
            other = record.model_copy(update={'map_file': 'Maps/Other.bin', 'map_name': 'Other'})
            app.run_worker(controller.edit(other, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')
            await pilot.pause()
            new_controller = RecordEditorController(
                app, store, client=client, source='source', manual_fields=()
            )
            assert not new_controller.allow_offline
        assert len(store.matching_ids(other)) == 1

    asyncio.run(run())
    assert client.writes == []


@pytest.mark.parametrize('choice', ('local', 'remote', 'only-local', 'cancel'))
def test_default_sync_rechecks_remote_changes_and_obeys_choice(
    tmp_path: Path, record: MapRecord, choice: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = store.load(store.save(record))
    assert saved.local_id is not None
    values = encode_record_values(saved, client.fields)
    client.rows['target'] = deepcopy(values)
    store.confirm_sheet_record(
        RecordSheetLink(
            local_id=saved.local_id, file_id='file', sheet_id='main', record_id='target'
        ),
        values,
        submitted=saved,
        confirmed_at=NOW,
    )

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=(120, 40)) as pilot:
            app.run_worker(controller.edit(record, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordSaveModeScreen)
            await pilot.click('#record-save-update')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            client.rows['target']['备注'] = [{'text': 'remote change'}]
            app.screen.query_one('#record-video-url', Input).value = 'https://example.com/video'
            await pilot.click('#record-confirm-save')
            await pilot.pause()
            assert isinstance(app.screen, RecordConflictScreen)
            assert client.writes == []
            assert store.load(1) == saved
            await pilot.click(f'#record-conflict-{choice}')
            await pilot.pause()
            if choice in {'local', 'remote'}:
                assert len(client.writes) == (1 if choice == 'local' else 0)
                latest = store.load(1)
                assert latest.video_url == (
                    'https://example.com/video' if choice == 'local' else None
                )
                assert latest.notes == ('A' if choice == 'local' else 'remote change')
                assert store.sync_state(1) is RecordSyncState.SYNCED
            elif choice == 'only-local':
                assert store.load(1).video_url == 'https://example.com/video'
                assert store.sync_state(1) is RecordSyncState.PENDING
                assert store.sheet_links(1)[0].confirmed_values == values
                assert client.rows['target']['备注'] == [{'text': 'remote change'}]
                assert client.writes == []
            else:
                assert store.load(1) == saved
                assert client.writes == []

    asyncio.run(run())


@pytest.mark.parametrize('button', ('record-offline-local', 'record-offline-cancel'))
def test_schema_failure_permits_local_save_but_never_remote_write(
    tmp_path: Path, record: MapRecord, button: str
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.fields = make_sheet_fields(('地图名', 1))

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=(120, 40)) as pilot:
            app.run_worker(controller.edit(record, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordOfflineScreen)
            await pilot.click(f'#{button}')
            await pilot.pause()
            if button == 'record-offline-local':
                assert isinstance(app.screen, RecordEditorScreen)
                await pilot.click('#record-confirm-save')
                await pilot.pause()
                assert store.sync_state(1) is RecordSyncState.PENDING
            assert not controller.allow_offline

    asyncio.run(run())
    assert len(store.matching_ids(record)) == (1 if button == 'record-offline-local' else 0)
    assert client.writes == []


def test_conflict_at_open_can_be_saved_locally_without_changing_baseline(
    tmp_path: Path, record: MapRecord
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    saved = store.load(store.save(record))
    assert saved.local_id is not None
    baseline = encode_record_values(saved, client.fields)
    store.confirm_sheet_record(
        RecordSheetLink(
            local_id=saved.local_id, file_id='file', sheet_id='main', record_id='target'
        ),
        baseline,
        submitted=saved,
        confirmed_at=NOW,
    )
    client.rows['target'] = {**baseline, '备注': [{'text': 'remote change'}]}

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=(120, 40)) as pilot:
            app.run_worker(controller.edit(record, editor))
            await pilot.pause()
            await pilot.click('#record-save-update')
            await pilot.pause()
            assert isinstance(app.screen, RecordConflictScreen)
            await pilot.click('#record-conflict-only-local')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.query_one('#record-video-url', Input).value = 'https://example.com/video'
            await pilot.click('#record-confirm-save')
            await pilot.pause()
        assert store.load(1).video_url == 'https://example.com/video'
        assert store.sync_state(1) is RecordSyncState.PENDING
        assert store.sheet_links(1)[0].confirmed_values == baseline
        assert not controller.allow_offline

    asyncio.run(run())
    assert client.writes == []


@pytest.mark.parametrize('failure', (False, True))
def test_new_record_defaults_to_sync_and_keeps_failed_submission_pending(
    tmp_path: Path, record: MapRecord, failure: bool
) -> None:
    store = RecordStore(tmp_path / 'records.sqlite3')
    client = FakeSheet()
    client.write_error = failure

    async def run() -> None:
        app = App[None](css_path=CSS)
        controller = RecordEditorController(
            app, store, client=client, source='source', manual_fields=()
        )
        async with app.run_test(size=(120, 40)) as pilot:
            app.run_worker(controller.edit(record, editor))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')
            await pilot.pause()
            assert len(client.writes) == 1 and client.writes[0][0] is None
            assert store.load(1).record_number == 1
            assert store.sync_state(1) is (
                RecordSyncState.PENDING if failure else RecordSyncState.SYNCED
            )

    asyncio.run(run())
