import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from textual.containers import Vertical, VerticalScroll
from textual.document._document import Selection
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    ListView,
    Select,
    Static,
    TextArea,
    Tree,
)

from pist.game.mods import Dependency, InstalledMod, LocalCampaign, LocalMap, ModScanReport
from pist.game.routes import MapLayout, MapRoom, MapRoute
from pist.game.saves import MapStats, SaveReader, SaveSlot
from pist.game.time import Time
from pist.gamebanana import GameBananaClient, GameBananaSubmission
from pist.local_data import LocalDataStore
from pist.records import MapRecord
from pist.settings import PistSettings, SettingsStore
from pist.sheet_report import ManualRecordField
from pist.types import RecordValues
from pist.ui.mods.browser import (
    DETAIL_SCROLL_ID,
    MAP_DETAIL_ID,
    AuthorSelectionScreen,
    DatePickerScreen,
    DialogAuthorSelectionScreen,
    MapItem,
    ModBrowserApp,
    ModTree,
    RecordAuthorField,
    RecordEditorScreen,
    RecordReferenceScreen,
    RecordRouteField,
    _plain_html,
    _recorded_maps_first,
    dependency_roots,
)


@dataclass(frozen=True, slots=True)
class StubMapEntityStats:
    record_values: RecordValues
    select_conflicts: tuple[object, ...] = ()


def test_gamebanana_html_description_is_rendered_as_plain_text() -> None:
    assert _plain_html('<p>适合初学者。<br>有进阶&nbsp;路线。</p>') == '适合初学者。\n有进阶 路线。'


def test_record_reference_and_collab_tags_are_shown_in_their_own_fields() -> None:
    map_info = LocalMap(
        file_path='Maps/Example/Map.bin',
        dialog_key='Example_Map',
        collab_credit_tags={'en': 'Beginner'},
    )
    collab_mod = InstalledMod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='ExampleCollab',
        maps=[map_info],
    )
    gamebanana = GameBananaSubmission.model_validate(
        {
            'name': 'Example',
            'submitter': 'Submitter',
            'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
            'description': '<p>Ignored for collabs.</p>',
        }
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods',
            blacklist_entries=[],
            skipped_blacklisted=[],
            mods=[collab_mod],
        )
    )
    app._selected_mod = collab_mod

    assert app._record_reference(map_info, gamebanana) == ('简介', 'Ignored for collabs.')
    assert app._collab_tags(map_info) == 'Beginner'


def test_record_reference_opens_full_text_on_double_click() -> None:
    record = MapRecord.model_validate(
        {
            'created_at': '2026-09-04T12:00:00Z',
            'mod_metadata_name': 'Example',
            'map_name': 'Map',
            'map_file': 'Maps/Example/Map.bin',
            'sid': 'Example/Map',
            'side': 'A',
            'save_slot': 0,
        }
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods', blacklist_entries=[], skipped_blacklisted=[], mods=[]
        )
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(RecordEditorScreen(record, reference=('简介', '完整介绍')))
            await pilot.pause()
            await pilot.double_click('#record-reference-summary')
            assert isinstance(app.screen, RecordReferenceScreen)

    asyncio.run(check())


def test_record_author_value_reopens_its_source_selector() -> None:
    record = MapRecord.model_validate(
        {
            'created_at': '2026-09-04T12:00:00Z',
            'mod_metadata_name': 'Example',
            'map_name': 'Map',
            'map_file': 'Maps/Example/Map.bin',
            'sid': 'Example/Map',
            'side': 'A',
            'save_slot': 0,
            'authors': ['Alice'],
        }
    )
    submission = GameBananaSubmission.model_validate(
        {
            'name': 'Example',
            'submitter': 'Submitter',
            'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
            'credits': [{'groupName': 'Creator', 'authors': [{'name': 'Alice'}]}],
        }
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods', blacklist_entries=[], skipped_blacklisted=[], mods=[]
        )
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(RecordEditorScreen(record, author_source=submission))
            await pilot.pause()
            await pilot.click(app.screen.query_one(RecordAuthorField))
            assert isinstance(app.screen, AuthorSelectionScreen)
            assert app.screen.query_one('#record-author-0', Checkbox).value

    asyncio.run(check())


def test_record_main_room_field_opens_route_editor() -> None:
    record = MapRecord.model_validate(
        {
            'created_at': '2026-09-04T12:00:00Z',
            'mod_metadata_name': 'Example',
            'map_name': 'Map',
            'map_file': 'Maps/Example/Map.bin',
            'sid': 'Example/Map',
            'side': 'A',
            'save_slot': 0,
        }
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods', blacklist_entries=[], skipped_blacklisted=[], mods=[]
    )
    opened: list[None] = []

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test() as pilot:
            app.push_screen(RecordEditorScreen(record, edit_route=lambda: opened.append(None)))
            await pilot.pause()
            route_field = app.screen.query_one(RecordRouteField)
            assert route_field.render() == '单击编辑路线'
            await pilot.click(route_field)

    asyncio.run(check())
    assert opened == [None]


def test_record_confirmation_prefills_saved_manual_values() -> None:
    record = MapRecord.model_validate(
        {
            'created_at': '2026-09-04T12:00:00Z',
            'mod_metadata_name': 'Example',
            'map_name': 'Map',
            'map_file': 'Maps/Example/Map.bin',
            'sid': 'Example/Map',
            'side': 'A',
            'save_slot': 0,
            'record_values': {
                '主表': {
                    '标注难度': '专家',
                    '起始日期': '2026-09-01',
                    '评分': 8,
                    '备注': '好图',
                }
            },
        }
    )
    manual_fields = (
        ManualRecordField('标注难度', 17, ('高级', '专家')),
        ManualRecordField('起始日期', 4),
        ManualRecordField('评分', 2),
        ManualRecordField('备注', 1),
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods', blacklist_entries=[], skipped_blacklisted=[], mods=[]
        )
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(RecordEditorScreen(record, manual_fields=manual_fields))
            await pilot.pause()
            assert app.screen.query_one('#record-manual-0', Select).value == '专家'
            assert app.screen.query_one('#record-manual-1', Input).value == '2026-09-01'
            assert app.screen.query_one('#record-manual-2', Input).value == '8'
            assert app.screen.query_one('#record-manual-3', Input).value == '好图'

    asyncio.run(check())


def test_recorded_maps_are_displayed_before_unrecorded_maps() -> None:
    unrecorded = LocalMap(
        file_path='Maps/Example/Unrecorded.bin',
        dialog_key='Example_Unrecorded',
        names={'en': 'Unrecorded'},
    )
    recorded = LocalMap(
        file_path='Maps/Example/Recorded.bin',
        dialog_key='Example_Recorded',
        names={'en': 'Recorded'},
    )
    save_slot = SaveSlot(0, {('Example/Recorded', 0): MapStats(Time(1_000), 1)})

    assert _recorded_maps_first([unrecorded, recorded], save_slot) == [recorded, unrecorded]


def test_dependency_roots_hide_enabled_dependencies() -> None:
    helper = InstalledMod(
        source='zip',
        filename='Helper.zip',
        path='C:/Celeste/Mods/Helper.zip',
        metadata_name='Helper',
        metadata_version='1.0.0',
    )
    map_mod = InstalledMod(
        source='zip',
        filename='Map.zip',
        path='C:/Celeste/Mods/Map.zip',
        metadata_name='Map',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Helper', version='1.0.0')],
    )

    assert dependency_roots([helper, map_mod]) == [map_mod]


def test_mod_tree_does_not_expand_when_a_node_is_selected() -> None:
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[],
    )

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test():
            assert not app.query_one(ModTree).auto_expand

    asyncio.run(check())


def test_tree_marks_dependency_free_mod_as_leaf() -> None:
    helper = InstalledMod(
        source='zip',
        filename='Helper.zip',
        path='C:/Celeste/Mods/Helper.zip',
        metadata_name='Helper',
        metadata_version='1.0.0',
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods',
            blacklist_entries=[],
            skipped_blacklisted=[],
            mods=[helper],
        )
    )
    tree: Tree[InstalledMod] = Tree('已启用 Mod')

    app._add_tree_node(tree.root, helper, is_optional=False, ancestry=set())

    assert not tree.root.children[0].allow_expand


def test_tree_includes_enabled_disabled_and_missing_dependencies() -> None:
    enabled = InstalledMod(
        source='zip',
        filename='Enabled.zip',
        path='C:/Celeste/Mods/Enabled.zip',
        metadata_name='Enabled',
        metadata_version='1.0.0',
    )
    root = InstalledMod(
        source='zip',
        filename='Root.zip',
        path='C:/Celeste/Mods/Root.zip',
        metadata_name='Root',
        metadata_version='1.0.0',
        dependencies=[
            Dependency(name='Enabled', version='1.0.0'),
            Dependency(name='Disabled', version='2.0.0'),
        ],
        optional_dependencies=[Dependency(name='Missing', version='3.0.0')],
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods',
            blacklist_entries=['disabled.zip'],
            skipped_blacklisted=['disabled.zip'],
            disabled_mod_names=['Disabled'],
            mods=[root, enabled],
        )
    )
    tree: Tree[InstalledMod] = Tree('Mods')

    app._add_tree_node(tree.root, root, is_optional=False, ancestry=set())

    nodes = tree.root.children[0].children
    assert [node.data.metadata_name if node.data is not None else None for node in nodes] == [
        'Enabled',
        None,
        None,
    ]


def test_tree_tracks_only_the_current_dependency_path() -> None:
    root = InstalledMod(
        source='zip',
        filename='Root.zip',
        path='C:/Celeste/Mods/Root.zip',
        metadata_name='Root',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Child', version='1.0.0')],
    )
    child = InstalledMod(
        source='zip',
        filename='Child.zip',
        path='C:/Celeste/Mods/Child.zip',
        metadata_name='Child',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Root', version='1.0.0')],
    )
    app = ModBrowserApp(
        ModScanReport(
            mods_directory='C:/Celeste/Mods',
            blacklist_entries=[],
            skipped_blacklisted=[],
            mods=[root, child],
        )
    )
    tree: Tree[InstalledMod] = Tree('Mods')
    ancestry: set[str] = set()

    app._add_tree_node(tree.root, root, is_optional=False, ancestry=ancestry)

    assert ancestry == set()
    assert str(tree.root.children[0].children[0].children[0].label) == '↻ Root (循环依赖)'


def test_detail_pane_scrolls_with_shortcut() -> None:
    maps = [
        LocalMap(file_path=f'Maps/Test/{index}.bin', dialog_key=f'Test_{index}')
        for index in range(100)
    ]
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=maps,
            )
        ],
    )

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test(size=(80, 24)) as pilot:
            detail_scroll = app.query_one(f'#{DETAIL_SCROLL_ID}', VerticalScroll)
            await pilot.press('j')
            assert detail_scroll.scroll_y > 0

    asyncio.run(check())


def test_save_slot_shortcuts_redraw_selected_slot(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for number in (0, 2):
        (saves_dir / f'{number}.celeste').write_text('<SaveData />', encoding='utf-8')
    reader = SaveReader(tmp_path / 'Celeste')
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
            )
        ],
    )

    async def check() -> None:
        app = ModBrowserApp(report, save_reader=reader, save_slot=reader.load(0))
        async with app.run_test() as pilot:
            map_detail = app.query_one(f'#{MAP_DETAIL_ID}', Vertical)
            content = map_detail.query_one(Static)
            await pilot.press(']')
            assert app._save_slot is not None
            assert app._save_slot.number == 2
            assert map_detail.query_one(Static) is content
            await pilot.press('[')
            assert app._save_slot.number == 0

    asyncio.run(check())


def test_save_slot_switch_keeps_campaign_collapsed(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for number in (0, 1):
        (saves_dir / f'{number}.celeste').write_text('<SaveData />', encoding='utf-8')
    reader = SaveReader(tmp_path / 'Celeste')
    campaign = LocalCampaign(
        directory='Maps/Test',
        dialog_key='Test',
        names={'en': 'Test'},
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                campaigns=[campaign],
            )
        ],
    )

    async def check() -> None:
        app = ModBrowserApp(report, save_reader=reader, save_slot=reader.load(0))
        async with app.run_test() as pilot:
            section = app.query_one(Collapsible)
            section.collapsed = True
            await pilot.press(']')
            assert app.query_one(Collapsible) is section
            assert section.collapsed

    asyncio.run(check())


def test_save_slot_switch_reorders_maps_by_recorded_status() -> None:
    first = LocalMap(file_path='Maps/Test/First.bin', dialog_key='Test_First')
    second = LocalMap(file_path='Maps/Test/Second.bin', dialog_key='Test_Second')
    third = LocalMap(file_path='Maps/Test/Third.bin', dialog_key='Test_Third')
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[first, second, third],
            )
        ],
    )
    first_slot = SaveSlot(
        0,
        {
            ('Test/First', 0): MapStats(Time(1_000), 1),
            ('Test/Second', 0): MapStats(Time(1_000), 1, completed=True),
        },
    )
    second_slot = SaveSlot(
        1,
        {
            ('Test/Second', 0): MapStats(Time(1_000), 1),
            ('Test/Third', 0): MapStats(Time(1_000), 1, completed=True),
        },
    )
    third_slot = SaveSlot(2, {('Test/Third', 0): MapStats(Time(1_000), 1)})

    async def check() -> None:
        app = ModBrowserApp(report, save_slot=first_slot)
        async with app.run_test():
            map_list = app.query_one(ListView)
            assert [item.map_info for item in map_list.query(MapItem)] == [second, first, third]
            app._save_slot = second_slot
            await app._refresh_save_stats()
            assert [item.map_info for item in map_list.query(MapItem)] == [third, second, first]
            app._save_slot = third_slot
            await app._refresh_save_stats()
            assert [item.map_info for item in map_list.query(MapItem)] == [third, first, second]

    asyncio.run(check())


def test_map_selection_requires_right_click_to_write_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = LocalMap(
        file_path='Maps/Example/Map.bin',
        dialog_key='Example_Map',
        names={'en': 'Example Map'},
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')

    save_slot = SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)})
    monkeypatch.setattr(
        'pist.ui.mods.browser.load_map_entity_stats_from_path',
        lambda *_args, **_kwargs: StubMapEntityStats({'主表': {'红草莓数': 2}}),
    )

    async def check() -> None:
        app = ModBrowserApp(report, local_data=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            map_list = app.query_one(ListView)
            map_list.focus()
            await pilot.press('enter')
            with pytest.raises(ValueError, match='No saved local record'):
                local_data.load_record(1)
            await pilot.click(map_list.query_one(MapItem), button=3)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.map_name == 'Example Map'
    assert record.mod_metadata_name == 'Example'
    assert record.record_values == {'主表': {'红草莓数': 2}}


def test_record_includes_saved_main_room_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = LocalMap(
        file_path='Maps/Example/Map.bin',
        dialog_key='Example_Map',
        names={'en': 'Example Map'},
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    local_data.save_route(MapRoute(map_file=map_info.file_path, rooms=('start', 'middle', 'goal')))
    monkeypatch.setattr(
        'pist.ui.mods.browser.load_map_entity_stats_from_path',
        lambda *_args, **_kwargs: StubMapEntityStats({'主表': {'红草莓数': 2}}),
    )

    async def check() -> None:
        app = ModBrowserApp(
            report,
            local_data=local_data,
            save_slot=SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)}),
        )
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(MapItem), button=3)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.record_values == {'主表': {'红草莓数': 2, '主房间数': 3}}


@pytest.mark.parametrize('trigger', ('shortcut', 'double_click'))
def test_map_preview_triggers_and_persists_highlighted_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    layout = MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184)))
    monkeypatch.setattr('pist.ui.mods.browser.load_map_layout', lambda *_: layout)
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    started = asyncio.Event()
    finish = asyncio.Event()

    class Preview:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def preview(self):
            started.set()
            await finish.wait()
            return MapRoute(map_file=map_info.file_path, rooms=('start',))

    monkeypatch.setattr('pist.ui.mods.browser.MapPreview', Preview)

    async def check() -> None:
        app = ModBrowserApp(report, local_data=local_data)
        async with app.run_test() as pilot:
            map_item = app.query_one(MapItem)
            if trigger == 'shortcut':
                await pilot.click(map_item)
                await pilot.press('p')
            else:
                await pilot.double_click(map_item)
            await started.wait()
            previous_theme = app.theme
            await pilot.press('t')
            assert app.theme != previous_theme
            finish.set()
            await pilot.pause()

    asyncio.run(check())
    route = local_data.load_route(map_info.file_path)
    assert route is not None
    assert route.rooms == ('start',)


def test_map_selected_in_detail_edits_credit_authors_from_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    submission = GameBananaSubmission.model_validate(
        {
            'name': 'Example Mod',
            'submitter': 'Submitter',
            'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
            'credits': [
                {'groupName': 'Creator', 'authors': [{'name': 'Alice', 'role': 'Mapper'}]},
                {
                    'groupName': 'Special Thanks',
                    'authors': [{'name': 'Bob', 'role': 'Translation'}],
                },
            ],
        }
    )

    class StubGameBananaClient(GameBananaClient):
        async def lookup(self, metadata_name: str) -> GameBananaSubmission | None:
            assert metadata_name == 'Example'
            return submission

    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    save_slot = SaveSlot(0, {('Example/Map', 0): MapStats(Time(1_000), 1)})
    monkeypatch.setattr(
        'pist.ui.mods.browser.load_map_entity_stats_from_path',
        lambda *_args, **_kwargs: StubMapEntityStats({}),
    )

    async def check() -> None:
        app = ModBrowserApp(
            report,
            local_data=local_data,
            save_slot=save_slot,
            gamebanana_client=StubGameBananaClient(),
        )
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(MapItem), button=3)
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click(app.screen.query_one(RecordAuthorField))
            assert isinstance(app.screen, AuthorSelectionScreen)
            await pilot.click('#record-author-0')
            await pilot.pause()
            await pilot.click('#author-select-save')
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.pause()
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.authors == ('Alice',)
    assert record.credits[1].group_name == 'Special Thanks'


def test_collab_map_edits_multiple_dialog_authors_from_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = LocalMap(
        file_path='Maps/Expert/Example.bin',
        dialog_key='Expert_Example',
        author_texts={'en': 'by Alice Smith and Bob Jones'},
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                collab_id='ExampleCollab',
                maps=[map_info],
            )
        ],
    )
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    save_slot = SaveSlot(0, {('Expert/Example', 0): MapStats(Time(1_000), 1)})
    monkeypatch.setattr(
        'pist.ui.mods.browser.load_map_entity_stats_from_path',
        lambda *_args, **_kwargs: StubMapEntityStats({}),
    )

    async def check() -> None:
        app = ModBrowserApp(report, local_data=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(MapItem), button=3)
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click(app.screen.query_one(RecordAuthorField))
            assert isinstance(app.screen, DialogAuthorSelectionScreen)
            text_area = app.screen.query_one(TextArea)
            text_area.selection = Selection((0, 3), (0, 14))
            app.screen.query_one('#dialog-author-add', Button).press()
            await pilot.pause()
            text_area.selection = Selection((0, 19), (0, 28))
            assert text_area.selected_text == 'Bob Jones'
            app.screen.query_one('#dialog-author-add', Button).press()
            await pilot.pause()
            assert app.screen.query_one('#dialog-author-0', Input).value == 'Alice Smith'
            second = app.screen.query_one('#dialog-author-1', Input)
            assert second.value == 'Bob Jones'
            second.value = 'Robert Jones'
            app.screen.query_one('#dialog-author-0', Input).value = ''
            app.screen.query_one('#dialog-author-remove-0', Button).press()
            await pilot.pause()
            assert app.screen.query_one('#dialog-author-0', Input).value == 'Robert Jones'
            await pilot.click('#dialog-author-save')
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.authors == ('Robert Jones',)


def test_record_confirmation_saves_optional_manual_main_record_values() -> None:
    record = MapRecord.model_validate(
        {
            'created_at': '2026-09-04T12:00:00Z',
            'mod_metadata_name': 'Example',
            'map_name': 'Map',
            'map_file': 'Maps/Example/Map.bin',
            'sid': 'Example/Map',
            'side': 'A',
            'save_slot': 0,
            'record_values': {'主表': {'红草莓数': 2}},
        }
    )
    manual_fields = (
        ManualRecordField('体感难度', 17, ('高级',)),
        ManualRecordField('难度子阶', 17, ('低',)),
        ManualRecordField('标注难度', 17, ('专家',)),
        ManualRecordField('标注难度子阶', 17, ('高',)),
        ManualRecordField('起始日期', 4),
        ManualRecordField('状态', 17, ('通关', '进行中')),
        ManualRecordField('评分', 2),
        ManualRecordField('备注', 1),
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods', blacklist_entries=[], skipped_blacklisted=[], mods=[]
    )
    saved: list[MapRecord] = []

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test() as pilot:
            app.push_screen(
                RecordEditorScreen(record, manual_fields=manual_fields),
                lambda result: saved.append(result) if result is not None else None,
            )
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.query_one('#record-manual-0', Select).value = '高级'
            app.screen.query_one('#record-manual-1', Select).value = '低'
            app.screen.query_one('#record-manual-2', Select).value = '专家'
            app.screen.query_one('#record-manual-3', Select).value = '高'
            await pilot.pause()
            app.screen.query_one('#record-manual-4-picker', Button).press()
            await pilot.pause()
            assert isinstance(app.screen, DatePickerScreen)
            app.screen.query_one('#date-picker-day-1', Button).press()
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            app.screen.query_one('#record-manual-4', Input).value = '2026-09-12'
            app.screen.query_one('#record-manual-5', Select).value = '通关'
            app.screen.query_one('#record-manual-6', Input).value = '8'
            app.screen.query_one('#record-manual-7', Input).value = '好图'
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    assert saved == [
        record.model_copy(
            update={
                'record_values': {
                    '主表': {
                        '红草莓数': 2,
                        '体感难度': '高级',
                        '难度子阶': '低',
                        '标注难度': '专家',
                        '标注难度子阶': '高',
                        '起始日期': '2026-09-12',
                        '状态': '通关',
                        '评分': 8,
                        '备注': '好图',
                    }
                }
            }
        )
    ]


def test_map_without_record_does_not_open_record_confirmation(tmp_path: Path) -> None:
    map_info = LocalMap(file_path='Maps/Example/Map.bin', dialog_key='Example_Map')
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    save_slot = SaveSlot(0, {('Example/Map', 0): MapStats(Time(), 1)})

    async def check() -> None:
        app = ModBrowserApp(report, local_data=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(MapItem), button=3)
            assert not isinstance(app.screen, RecordEditorScreen)

    asyncio.run(check())
    with pytest.raises(ValueError, match='No saved local record'):
        local_data.load_record(1)


def test_theme_shortcut_persists_selection(tmp_path: Path) -> None:
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[],
    )
    settings_store = SettingsStore(tmp_path / 'settings.json')

    async def check() -> None:
        app = ModBrowserApp(report, settings_store=settings_store)
        async with app.run_test() as pilot:
            await pilot.press('t')
            assert app.theme == 'textual-light'

    asyncio.run(check())
    assert settings_store.load().theme == 'textual-light'


def test_theme_shortcut_preserves_game_settings(tmp_path: Path) -> None:
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[],
    )
    settings_store = SettingsStore(tmp_path / 'settings.json')
    settings_store.save(
        PistSettings(
            game_dir=Path('C:/Celeste'),
            smartsheet_url='https://docs.qq.com/smartsheet/example',
        )
    )

    async def check() -> None:
        app = ModBrowserApp(report, settings_store=settings_store)
        async with app.run_test() as pilot:
            await pilot.press('t')

    asyncio.run(check())
    settings = settings_store.load()
    assert settings.game_dir == Path('C:/Celeste')
    assert settings.smartsheet_url == 'https://docs.qq.com/smartsheet/example'


def test_escape_is_bound_to_quit() -> None:
    assert ('escape', 'quit', '退出') in ModBrowserApp.BINDINGS


def test_page_keys_are_not_bound_to_save_slot_switching() -> None:
    assert ('pageup', 'previous_save_slot', '上一存档') not in ModBrowserApp.BINDINGS
    assert ('pagedown', 'next_save_slot', '下一存档') not in ModBrowserApp.BINDINGS


def test_campaigns_are_expanded_collapsible_sections() -> None:
    campaign = LocalCampaign(
        directory='Maps/TestCollab/1-Beginner',
        dialog_key='TestCollab_0_Lobbies_1_Beginner',
        kind='collab_lobby',
        names={'zh-cn': '新手大厅', 'en': 'Beginner Lobby'},
        maps=[LocalMap(file_path='Maps/TestCollab/1-Beginner/Map.bin', dialog_key='Map')],
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='TestCollab.zip',
                path='C:/Celeste/Mods/TestCollab.zip',
                metadata_name='TestCollab',
                metadata_version='1.0.0',
                campaigns=[campaign],
            )
        ],
    )

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test():
            campaign_section = app.query_one('.campaign-section', Collapsible)
        assert not campaign_section.collapsed
        assert campaign_section.has_class('campaign-section')

    asyncio.run(check())


def test_collab_lobby_maps_are_available_in_a_separate_preview_section() -> None:
    lobby = LocalMap(
        file_path='Maps/TestCollab/0-Lobbies/1-Maps.bin',
        dialog_key='TestCollab_0_Lobbies_1_Maps',
    )
    campaign_map = LocalMap(
        file_path='Maps/TestCollab/1-Maps/Map.bin', dialog_key='TestCollab_1_Maps_Map'
    )
    campaign = LocalCampaign(
        directory='Maps/TestCollab/1-Maps',
        dialog_key='TestCollab_0_Lobbies_1_Maps',
        maps=[campaign_map],
    )
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='TestCollab.zip',
                path='C:/Celeste/Mods/TestCollab.zip',
                metadata_name='TestCollab',
                metadata_version='1.0.0',
                collab_id='TestCollab',
                maps=[lobby, campaign_map],
                campaigns=[campaign],
            )
        ],
    )

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test():
            lobby_section = next(
                widget for widget in app.query(Collapsible) if widget.title == '大厅'
            )
            assert not lobby_section.collapsed
            assert lobby_section.has_class('campaign-section')
            assert [item.map_info for item in lobby_section.query(MapItem)] == [lobby]

    asyncio.run(check())


def test_campaign_map_lists_do_not_fill_the_remaining_detail_height() -> None:
    campaigns = [
        LocalCampaign(
            directory=f'Maps/Test/{name}',
            dialog_key=name,
            names={'en': name},
            maps=[LocalMap(file_path=f'Maps/Test/{name}/Map.bin', dialog_key=f'{name}_Map')],
        )
        for name in ('Prologue', 'Beginner')
    ]
    report = ModScanReport(
        mods_directory='C:/Celeste/Mods',
        blacklist_entries=[],
        skipped_blacklisted=[],
        mods=[
            InstalledMod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                campaigns=campaigns,
            )
        ],
    )

    async def check() -> None:
        app = ModBrowserApp(report)
        async with app.run_test(size=(100, 40)):
            sections = list(app.query(Collapsible))
            assert len(sections) == 2
            assert sections[0].size.height < 20
            assert sections[1].region.y <= 20

    asyncio.run(check())
