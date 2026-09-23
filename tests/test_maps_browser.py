import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from struct import pack
from typing import Literal

import pytest
from textual.containers import VerticalScroll
from textual.document._document import Selection
from textual.widgets import Button, Checkbox, Input, Select, SelectionList, Static, TextArea

from pist.collab_lobbies import (
    CollabLobbyOverride,
    CollabLobbyOverrides,
    CollabLobbyOverrideStore,
)
from pist.entities.classification import CollectedEntityRuleIssue, CollectedEntityRuleIssueStatus
from pist.entities.map_entity_id import MapEntityID
from pist.game import collab as game_collab
from pist.game import dialog
from pist.game.content import ContentPath
from pist.game.duration import Duration
from pist.game.levels import (
    Level,
    LevelSide,
    LoadedModMap,
    LoadedVanillaMap,
    assemble_mod_levels,
)
from pist.game.maps import MapInfo
from pist.game.mods import InstalledMod, ModScanReport
from pist.game.routes import MapLayout, MapRoom, MapRoute
from pist.game.saves import MapStats, SaveReader, SaveSlot, sid_for_map_file
from pist.gamebanana import GameBananaClient, GameBananaSubmission
from pist.local_data import LocalDataStore
from pist.map_preview import MapPreviewMode
from pist.records import MapRecord, MapRecordProgress
from pist.settings import PistSettings, SettingsStore
from pist.sheet_report import ManualRecordField
from pist.types import RecordValues
from pist.ui.maps.browser import (
    DETAIL_SCROLL_ID,
    AuthorSelectionScreen,
    CampaignItem,
    CampaignList,
    CollabMapList,
    CollectedEntityRulesScreen,
    DatePickerScreen,
    DialogAuthorSelectionScreen,
    MapBrowserApp,
    MapItem,
    MapList,
    RecordAuthorField,
    RecordEditorScreen,
    RecordReferenceScreen,
    RecordRouteField,
)
from pist.ui.maps.browser.app import (
    _plain_html,
)
from pist.ui.maps.browser.campaign_list import CampaignListToggle
from pist.ui.maps.browser.collab_list import LobbyMapItem, SideLobbyMapItem
from pist.ui.maps.browser.collab_order import maps_by_campaign_icon_order, maps_by_progress
from pist.ui.maps.browser.map_list import MapSideButton, SideMapItem, _map_side_groups
from pist.ui.maps.browser.records import _reference_summary
from tests.mod_factory import make_installed_mod

type LevelSelection = tuple[Level, LevelSide]


def _map_ref(mod: InstalledMod, map_info: MapInfo) -> tuple[Level, LevelSide]:
    loaded_map = LoadedModMap(map_info, mod)
    level = Level(
        sid=sid_for_map_file(map_info.file_path),
        dialog_key=dialog.dialog_key_for_map_file(map_info.file_path),
        maps_by_side={LevelSide.A: loaded_map},
    )
    return level, LevelSide.A


def _map_infos(maps: Iterable[tuple[Level, LevelSide]]) -> tuple[MapInfo, ...]:
    return tuple(level.maps_by_side[side].map_info for level, side in maps)


@dataclass(frozen=True, slots=True)
class StubMapEntityStats:
    record_values: RecordValues
    select_conflicts: tuple[object, ...] = ()

    def count(self, _kind: str) -> int:
        return 0

    def exists(self, _kind: str) -> bool:
        return False

    def has_stat_kind(self, _kind: str) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class StubMapEntityRecordReview:
    stats: StubMapEntityStats
    collected_issues: tuple[CollectedEntityRuleIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class StubMapEntityRecordSource:
    load_review: Callable[[], StubMapEntityRecordReview]

    def review(self, **_kwargs: object) -> StubMapEntityRecordReview:
        return self.load_review()


def write_map_with_icon(path: Path, icon: str) -> None:
    """Write the smallest binary map carrying one top-level ``meta.Icon`` value."""
    lookup = ('Map', 'meta', 'Icon', icon)
    indices = {value: index for index, value in enumerate(lookup)}

    def element(name: str, attrs: list[bytes], children: list[bytes]) -> bytes:
        return b''.join(
            (
                pack('<H', indices[name]),
                pack('<B', len(attrs)),
                *attrs,
                pack('<H', len(children)),
                *children,
            )
        )

    meta = element('meta', [pack('<H', indices['Icon']) + b'\x05' + pack('<H', indices[icon])], [])
    root = element('Map', [], [meta])
    data = b'\x0bCELESTE MAP\x0bExample/Map' + pack('<H', len(lookup))
    data += b''.join(bytes((len(value),)) + value.encode() for value in lookup) + root
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_gamebanana_html_description_is_rendered_as_plain_text() -> None:
    assert _plain_html('<p>适合初学者。<br>有进阶&nbsp;路线。</p>') == '适合初学者。\n有进阶 路线。'


def test_reference_summary_normalizes_all_whitespace() -> None:
    assert (
        _reference_summary('简介', '  第一段\n\t第二段   第三段\r\n')
        == '[b]简介[/b]\n第一段 第二段 第三段'
    )


def test_record_reference_and_collab_tags_are_shown_in_their_own_fields() -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    collab_mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='ExampleCollab',
        dialogs={'en': {'Example_Map_collabcreditstags': 'Beginner'}},
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
    app = MapBrowserApp(
        ModScanReport(
            mods_dir='C:/Celeste/Mods',
            disabled_filenames=[],
            mods=[collab_mod],
        )
    )
    assert app._record_reference(map_info, gamebanana) == ('简介', 'Ignored for collabs.')
    assert app._collab_tags(_map_ref(collab_mod, map_info)[0]) == 'Beginner'


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
    app = MapBrowserApp(ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[]))

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
    app = MapBrowserApp(ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[]))

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
    report = ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[])
    opened: list[None] = []

    async def check() -> None:
        app = MapBrowserApp(report)
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
    app = MapBrowserApp(ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[]))

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(RecordEditorScreen(record, manual_fields=manual_fields))
            await pilot.pause()
            assert app.screen.query_one('#record-manual-0', Select).value == '专家'
            assert app.screen.query_one('#record-manual-1', Input).value == '2026-09-01'
            assert app.screen.query_one('#record-manual-2', Input).value == '8'
            assert app.screen.query_one('#record-manual-3', Input).value == '好图'

    asyncio.run(check())


def test_campaign_list_contains_one_item_per_campaign() -> None:
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[],
    )

    async def check() -> None:
        app = MapBrowserApp(report)
        async with app.run_test():
            assert list(app.query_one(CampaignList).query(CampaignItem)) == []

    asyncio.run(check())


def test_campaign_list_merges_maps_from_multiple_mod_sources() -> None:
    first_map = MapInfo(file_path='Maps/Pack/First.bin')
    second_map = MapInfo(file_path='Maps/Pack/Second.bin')
    first = make_installed_mod(
        source='zip',
        filename='First.zip',
        path='C:/Celeste/Mods/First.zip',
        metadata_name='First',
        metadata_version='1.0.0',
        maps=[first_map],
    )
    second = make_installed_mod(
        source='zip',
        filename='Second.zip',
        path='C:/Celeste/Mods/Second.zip',
        metadata_name='Second',
        metadata_version='1.0.0',
        maps=[second_map],
    )
    report = ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[first, second])

    async def check() -> None:
        app = MapBrowserApp(report)
        async with app.run_test():
            assert len(app.query_one(CampaignList).query(CampaignItem)) == 1
            active_maps = [item.loaded_map for item in app.query(MapItem)]
            assert all(isinstance(active_map, LoadedModMap) for active_map in active_maps)
            assert [
                active_map.mod for active_map in active_maps if isinstance(active_map, LoadedModMap)
            ] == [
                first,
                second,
            ]

    asyncio.run(check())


def test_browser_groups_helper_hidden_campaigns_in_a_collapsible() -> None:
    helper_map = MapInfo(file_path='Maps/Helper/Test.bin')
    visible_map = MapInfo(file_path='Maps/Visible/Map.bin')
    helper = make_installed_mod(
        source='zip',
        filename='Helper.zip',
        path='C:/Celeste/Mods/Helper.zip',
        metadata_name='AltSidesHelper',
        metadata_version='1.0.0',
        maps=[helper_map],
    )
    visible = make_installed_mod(
        source='zip',
        filename='Visible.zip',
        path='C:/Celeste/Mods/Visible.zip',
        metadata_name='Visible',
        metadata_version='1.0.0',
        maps=[visible_map],
    )
    hider = make_installed_mod(
        source='zip',
        filename='HelperTestMapHider.zip',
        path='C:/Celeste/Mods/HelperTestMapHider.zip',
        metadata_name='HelperTestMapHider',
        metadata_version='1.0.0',
    )
    app = MapBrowserApp(
        ModScanReport(
            mods_dir='C:/Celeste/Mods',
            disabled_filenames=[],
            mods=[helper, visible, hider],
        )
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            campaign_list = app.query_one(CampaignList)
            assert [
                item.campaign.directory.as_posix() for item in campaign_list.query(CampaignItem)
            ] == ['Maps/Visible']
            buttons = {button.direction: button for button in app.query(CampaignListToggle)}
            assert buttons[-1].has_class('-hidden')
            assert not buttons[1].has_class('-hidden')
            hidden_button = buttons[1]
            await pilot.click(hidden_button)
            for _ in range(10):
                await pilot.pause()
                if (
                    app._selected_campaign is app._hidden_campaigns[0]
                    and [
                        item.campaign.directory.as_posix()
                        for item in campaign_list.query(CampaignItem)
                    ]
                    == ['Maps/Helper']
                    and _map_infos(app.query_one(MapList).maps) == (helper_map,)
                    and campaign_list.has_focus
                    and campaign_list.highlighted_child is not None
                    and campaign_list.highlighted_child.highlighted
                ):
                    break
            else:
                pytest.fail('选择隐藏 Campaign 后未显示对应地图')
            assert not buttons[-1].has_class('-hidden')
            assert buttons[1].has_class('-hidden')
            visible_button = buttons[-1]
            await pilot.click(visible_button)
            for _ in range(10):
                await pilot.pause()
                if (
                    app._selected_campaign is app._campaigns[0]
                    and [
                        item.campaign.directory.as_posix()
                        for item in campaign_list.query(CampaignItem)
                    ]
                    == ['Maps/Visible']
                    and _map_infos(app.query_one(MapList).maps) == (visible_map,)
                ):
                    break
            else:
                pytest.fail('返回地图集后未显示对应地图')

    asyncio.run(check())


def test_browser_keeps_an_only_hidden_campaign_browsable() -> None:
    helper_map = MapInfo(file_path='Maps/Helper/Test.bin')
    helper = make_installed_mod(
        source='zip',
        filename='Helper.zip',
        path='C:/Celeste/Mods/Helper.zip',
        metadata_name='AltSidesHelper',
        metadata_version='1.0.0',
        maps=[helper_map],
    )
    hider = make_installed_mod(
        source='zip',
        filename='HelperTestMapHider.zip',
        path='C:/Celeste/Mods/HelperTestMapHider.zip',
        metadata_name='HelperTestMapHider',
        metadata_version='1.0.0',
    )
    app = MapBrowserApp(
        ModScanReport(
            mods_dir='C:/Celeste/Mods',
            disabled_filenames=[],
            mods=[helper, hider],
        )
    )

    async def check() -> None:
        async with app.run_test():
            assert app._campaigns == ()
            assert app._selected_campaign is app._hidden_campaigns[0]
            assert _map_infos(app.query_one(MapList).maps) == (helper_map,)

    asyncio.run(check())


def test_browser_renders_hidden_collab_group_as_its_direct_map_list() -> None:
    lobby = MapInfo(file_path='Maps/Example/0-Lobbies/1-Maps.bin')
    visible_map = MapInfo(file_path='Maps/Example/1-Maps/Visible.bin')
    hidden_map = MapInfo(file_path='Maps/Example/1-Submissions/Hidden.bin')
    collab = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby, visible_map, hidden_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='C:/Celeste/Mods/CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    app = MapBrowserApp(
        ModScanReport(
            mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[collab, collab_utils]
        )
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(
                next(button for button in app.query(CampaignListToggle) if button.direction > 0)
            )
            for _ in range(20):
                await pilot.pause(0.05)
                if app._selected_campaign is app._hidden_campaigns[0]:
                    break
            else:
                pytest.fail('未切换到隐藏的 Collab 地图组')
            hidden_item = next(
                item
                for item in app.query_one(CampaignList).query(CampaignItem)
                if item.campaign.directory.as_posix() == 'Maps/Example/1-Submissions'
            )
            await pilot.click(hidden_item)
            for _ in range(20):
                await pilot.pause(0.05)
                if app._selected_campaign is hidden_item.campaign:
                    break
            else:
                pytest.fail('未选中指定的隐藏 Collab 地图组')
            collab_list = app.query_one(CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            else:
                pytest.fail('隐藏的 Collab 地图组未完成日志图标读取')
            assert _map_infos(collab_list.query_one(MapList).maps) == (hidden_map,)

    asyncio.run(check())


def test_browser_selects_a_visible_campaign_before_hidden_campaigns() -> None:
    helper_map = MapInfo(file_path='Maps/Helper/Test.bin')
    visible_map = MapInfo(file_path='Maps/Visible/Map.bin')
    helper = make_installed_mod(
        source='zip',
        filename='Helper.zip',
        path='C:/Celeste/Mods/Helper.zip',
        metadata_name='AltSidesHelper',
        metadata_version='1.0.0',
        maps=[helper_map],
    )
    visible = make_installed_mod(
        source='zip',
        filename='Visible.zip',
        path='C:/Celeste/Mods/Visible.zip',
        metadata_name='Visible',
        metadata_version='1.0.0',
        maps=[visible_map],
    )
    hider = make_installed_mod(
        source='zip',
        filename='HelperTestMapHider.zip',
        path='C:/Celeste/Mods/HelperTestMapHider.zip',
        metadata_name='HelperTestMapHider',
        metadata_version='1.0.0',
    )

    app = MapBrowserApp(
        ModScanReport(
            mods_dir='C:/Celeste/Mods',
            disabled_filenames=[],
            mods=[helper, visible, hider],
        )
    )

    async def check() -> None:
        async with app.run_test():
            assert app._selected_campaign is app._campaigns[0]
            assert _map_infos(tuple(app._campaigns[0].iter_sides())) == (visible_map,)

    asyncio.run(check())


def test_browser_separates_vanilla_and_ungrouped_mod_maps(tmp_path: Path) -> None:
    content_maps = tmp_path / 'Content' / 'Maps'
    content_maps.mkdir(parents=True)
    (content_maps / '0-Intro.bin').touch()
    loose_map = MapInfo(file_path='Maps/Loose.bin')
    loose_mod = make_installed_mod(
        source='zip',
        filename='Loose.zip',
        path='C:/Celeste/Mods/Loose.zip',
        metadata_name='Loose',
        metadata_version='1.0.0',
        maps=[loose_map],
    )
    report = ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[loose_mod])

    app = MapBrowserApp(report)

    assert [campaign.fallback_name for campaign in app._campaigns] == ['原版地图', '未归类地图']
    assert [
        level.maps_by_side[side].source_name for level, side in app._campaigns[0].iter_sides()
    ] == ['原版']


def test_collab_campaign_expands_lobby_from_journal_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prologue = MapInfo(file_path='Maps/Example/0-Lobbies/0-Prologue.bin')
    lobby = MapInfo(file_path='Maps/Example/0-Lobbies/1-Easy.bin')
    gym = MapInfo(file_path='Maps/Example/0-Gyms/1-Easy.bin')
    easy_map = MapInfo(file_path='Maps/Example/1-Easy/Map.bin')
    hard_map = MapInfo(file_path='Maps/Example/2-Hard/Map.bin')
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        dialogs={'en': {'Example_Easy': 'Easy', 'Example_Hard': 'Hard'}},
        maps=[prologue, lobby, gym, easy_map, hard_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='C:/Celeste/Mods/CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    shared_lobbies = tmp_path / 'shared-collab-lobbies.toml'
    shared_lobbies.write_text('', encoding='utf-8')
    lobby_store = CollabLobbyOverrideStore(shared_lobbies, tmp_path / 'local-collab-lobbies.toml')
    app = MapBrowserApp(
        ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[mod, collab_utils]),
        local_data=LocalDataStore(tmp_path / 'local-data.sqlite3'),
        collab_lobby_overrides=lobby_store.load(),
        collab_lobby_store=lobby_store,
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_fingerprint', lambda _loaded_map: 'first'
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_references',
        lambda _loaded_map: game_collab.JournalReferences(('Example/1-Easy',)),
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            lobby_item = app.query_one(LobbyMapItem)
            record_previewed: list[LevelSelection] = []

            async def preview_record(level: Level, side: LevelSide) -> None:
                record_previewed.append((level, side))

            app._preview_record = preview_record
            assert _map_infos(
                next(
                    campaign
                    for campaign in app._hidden_campaigns
                    if campaign.directory.as_posix() == 'Maps/Example/2-Hard'
                ).iter_sides()
            ) == (hard_map,)
            assert _map_infos(
                next(
                    campaign
                    for campaign in app._hidden_campaigns
                    if campaign.directory.as_posix() == 'Maps/Example/0-Gyms'
                ).iter_sides()
            ) == (gym,)
            assert record_previewed == []
            map_lists = list(app.query(MapList))
            assert _map_infos(map_lists[0].maps) == (prologue, lobby)
            assert lobby_item.collapsed
            assert lobby_item.submission_maps.styles.display == 'none'
            assert lobby_item.submission_maps.styles.layer == 'content'
            highlighted_lobby = map_lists[0].highlighted_child
            await pilot.click(lobby_item, offset=(0, 0))
            assert not lobby_item.collapsed
            assert lobby_item.submission_maps.styles.display == 'block'
            assert map_lists[0].highlighted_child is highlighted_lobby
            for _ in range(20):
                await pilot.pause(0.05)
                if child_lists := list(lobby_item.query(MapList)):
                    break
            else:
                pytest.fail('Lobby submission map list did not finish loading.')
            await app._refresh_save_stats()
            assert list(lobby_item.query(MapList)) == child_lists
            child_lists = list(lobby_item.query(MapList))
            assert all(
                not map_list.has_class('is-loading') for map_list in app.query(CollabMapList)
            )
            assert [map_list.maps for map_list in app.query(CollabMapList)] == [
                tuple(
                    next(
                        campaign
                        for campaign in app._hidden_campaigns
                        if campaign.directory.as_posix() == 'Maps/Example/1-Easy'
                    ).iter_sides()
                ),
            ]
            map_lists[0].index = 0
            await pilot.click(child_lists[0].query_one(MapItem), offset=(3, 0))
            assert isinstance(app.focused, MapList)
            assert app.focused is not map_lists[0]
            assert map_lists[0].index == 0
            lobby_item.post_message(
                MapItem.Clicked(lobby_item, button=1, chain=2, screen_x=0, screen_y=0)
            )
            await pilot.pause()
            assert record_previewed == [(lobby_item.level, lobby_item.side)]
            menu_x = 10
            menu_y = 2
            lobby_item.post_message(
                MapItem.Clicked(
                    lobby_item,
                    button=3,
                    chain=1,
                    screen_x=menu_x,
                    screen_y=menu_y,
                )
            )
            await pilot.pause()
            action_dialog = app.screen.query_one('#map-action-dialog')
            assert action_dialog.offset == (menu_x, menu_y)
            assert action_dialog.size.height == 3
            incorrectly_translated = action_dialog.region.translate(action_dialog.offset)
            outside_point = next(
                (x, y)
                for y in range(app.size.height)
                for x in range(app.size.width)
                if incorrectly_translated.contains(x, y) and not action_dialog.region.contains(x, y)
            )
            await pilot.click(offset=outside_point)
            await pilot.pause()
            assert not list(app.screen.query('#map-action-dialog'))
            await pilot.double_click(lobby_item, offset=(0, 0))
            await pilot.pause()
            assert record_previewed == [(lobby_item.level, lobby_item.side)]
            assert lobby_item.collapsed
            lobby_item.post_message(
                MapItem.Clicked(lobby_item, button=3, chain=1, screen_x=0, screen_y=0)
            )
            await pilot.pause()
            await pilot.click('#map-action-edit-lobby')
            await pilot.pause()
            selection = app.screen.query_one(SelectionList)
            assert not list(app.screen.query('#lobby-campaign-save-shared'))
            assert not list(app.screen.query('#lobby-campaign-remove-shared'))
            assert [
                selection.get_option_at_index(index).value
                for index in range(selection.option_count)
            ][:3] == ['Example/0-Gyms', 'Example/1-Easy', 'Example/2-Hard']
            assert selection.selected == ['Example/1-Easy']
            selection.select('Example/2-Hard')
            await pilot.click('#lobby-campaign-save-local')
            await pilot.pause()
            assert lobby_store.load().campaigns_for(lobby_item.level.sid, lobby_item.side) == (
                'Example/1-Easy',
                'Example/2-Hard',
            )
            visible_maps = (
                *map_lists[0].maps,
                *(
                    loaded_map
                    for loaded_list in app.query(CollabMapList)
                    for loaded_map in loaded_list.maps
                ),
            )
            assert gym not in _map_infos(visible_maps)

    asyncio.run(check())


def test_lobby_journal_refs_reuse_cache_across_browser_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lobby = MapInfo(file_path='Maps/Example/0-Lobbies/1-Easy.bin')
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path=str(tmp_path / 'Example.zip'),
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby],
    )
    report = ModScanReport(mods_dir=str(tmp_path), disabled_filenames=[], mods=[mod])
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    level, side = _map_ref(mod, lobby)
    loaded_map = level.maps_by_side[side]
    calls = 0

    def journal_references(_loaded_map: LoadedModMap) -> game_collab.JournalReferences:
        nonlocal calls
        calls += 1
        return game_collab.JournalReferences(('Example/1-Easy',))

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_fingerprint', lambda _loaded_map: 'first'
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_references', journal_references
    )

    first = MapBrowserApp(report, local_data=local_data)
    second = MapBrowserApp(report, local_data=local_data)
    assert first._load_lobby_journal_refs(loaded_map).campaign_refs == ('Example/1-Easy',)
    assert second._load_lobby_journal_refs(loaded_map).campaign_refs == ('Example/1-Easy',)
    assert calls == 1


def test_collab_lobby_override_replaces_journal_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lobby = MapInfo(file_path='Maps/Example/0-Lobbies/1-Easy.bin')
    automatic_map = MapInfo(file_path='Maps/Example/1-Easy/Map.bin')
    configured_map = MapInfo(file_path='Maps/Example/2-Hard/Map.bin')
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path=str(tmp_path / 'Example.zip'),
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby, automatic_map, configured_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path=str(tmp_path / 'CollabUtils2.zip'),
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    overrides = CollabLobbyOverrides(
        lobbies=(
            CollabLobbyOverride(
                lobby='Example/0-Lobbies/1-Easy',
                side=LevelSide.A,
                campaigns=('Example/2-Hard',),
            ),
        )
    )
    app = MapBrowserApp(
        ModScanReport(mods_dir=str(tmp_path), disabled_filenames=[], mods=[mod, collab_utils]),
        local_data=LocalDataStore(tmp_path / 'local-data.sqlite3'),
        collab_lobby_overrides=overrides,
    )

    def unexpected_journal_lookup(_loaded_map: LoadedModMap) -> game_collab.JournalReferences:
        pytest.fail('Configured lobby side should not inspect JournalTrigger values.')

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_references', unexpected_journal_lookup
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            lobby_item = app.query_one(LobbyMapItem)
            await pilot.click(lobby_item, offset=(0, 0))
            for _ in range(20):
                await pilot.pause(0.05)
                if child_lists := list(lobby_item.query(MapList)):
                    break
            else:
                pytest.fail('Configured Lobby map list did not finish loading.')
            assert _map_infos(child_lists[0].maps) == (configured_map,)

    asyncio.run(check())


def test_collab_lobby_orders_journal_maps_from_multiple_source_mods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lobby = MapInfo(file_path='Maps/Example/0-Lobbies/1-Easy.bin')
    base_map = MapInfo(file_path='Maps/Example/1-Easy/Base.bin')
    addon_map = MapInfo(file_path='Maps/Example/1-Easy/Addon.bin')
    base_mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path=str(tmp_path / 'Example.zip'),
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby, base_map],
    )
    addon_mod = make_installed_mod(
        source='zip',
        filename='ExampleAddon.zip',
        path=str(tmp_path / 'ExampleAddon.zip'),
        metadata_name='ExampleAddon',
        metadata_version='1.0.0',
        maps=[addon_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path=str(tmp_path / 'CollabUtils2.zip'),
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    report = ModScanReport(
        mods_dir=str(tmp_path),
        disabled_filenames=[],
        mods=[base_mod, addon_mod, collab_utils],
    )
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_references',
        lambda _loaded_map: game_collab.JournalReferences(('Example/1-Easy',)),
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_fingerprint', lambda _loaded_map: 'first'
    )
    icon_calls: list[tuple[str, tuple[str, ...]]] = []

    def fingerprint(mod: InstalledMod, maps: tuple[MapInfo, ...]) -> str:
        return f'{mod.path}:{",".join(map_info.file_path.as_posix() for map_info in maps)}'

    def iter_icons(
        mod: InstalledMod, maps: tuple[MapInfo, ...]
    ) -> Iterable[tuple[ContentPath, str]]:
        map_files = tuple(map_info.file_path for map_info in maps)
        icon_calls.append((str(mod.path), tuple(path.as_posix() for path in map_files)))
        for map_file in map_files:
            order = '1-addon' if map_file.name == 'Addon.bin' else '2-base'
            yield map_file, f'areas/Example/meters/{order}'

    monkeypatch.setattr(
        'pist.ui.maps.browser.collab_order.game_mods.collab_journal_icon_fingerprint',
        fingerprint,
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.collab_order.game_mods.iter_collab_journal_map_icons',
        iter_icons,
    )

    async def check() -> None:
        app = MapBrowserApp(report, local_data=local_data)
        submission_campaign = next(
            campaign
            for campaign in app._hidden_campaigns
            if campaign.directory.as_posix() == 'Maps/Example/1-Easy'
        )
        assert any(
            isinstance(widget, CollabMapList) for widget in app._map_widgets(submission_campaign)
        )
        async with app.run_test(size=(100, 40)) as pilot:
            lobby_item = app.query_one(LobbyMapItem)
            await pilot.click(lobby_item, offset=(0, 0))
            for _ in range(20):
                await pilot.pause(0.05)
                child_lists = list(lobby_item.query(MapList))
                if child_lists:
                    break
            else:
                pytest.fail('Lobby journal maps from multiple Mods did not finish loading.')
            assert [item.map_info for item in child_lists[0].query(MapItem)] == [
                addon_map,
                base_map,
            ]
            assert {path for path, _map_files in icon_calls} == {
                str(base_mod.path),
                str(addon_mod.path),
            }

    asyncio.run(check())


def test_collab_lobby_sides_resolve_their_own_journal_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lobby_a = MapInfo(file_path='Maps/Example/0-Lobbies/1-Easy.bin')
    lobby_b = MapInfo(file_path='Maps/Example/0-Lobbies/1-Easy-B.bin')
    easy_map = MapInfo(file_path='Maps/Example/1-Easy/Map.bin')
    hard_map = MapInfo(file_path='Maps/Example/2-Hard/Map.bin')
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='C:/Celeste/Mods/Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='Example',
        maps=[lobby_a, lobby_b, easy_map, hard_map],
    )
    collab_utils = make_installed_mod(
        source='zip',
        filename='CollabUtils2.zip',
        path='C:/Celeste/Mods/CollabUtils2.zip',
        metadata_name='CollabUtils2',
        metadata_version='1.0.0',
    )
    calls: list[str] = []

    def journal_references(loaded_map: LoadedModMap) -> game_collab.JournalReferences:
        map_file = str(loaded_map.info.file_path)
        calls.append(map_file)
        levelset = 'Example/2-Hard' if map_file.endswith('-B.bin') else 'Example/1-Easy'
        return game_collab.JournalReferences((levelset,))

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_references', journal_references
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_collab.journal_fingerprint',
        lambda loaded_map: loaded_map.info.file_path.as_posix(),
    )
    app = MapBrowserApp(
        ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[mod, collab_utils]),
        local_data=LocalDataStore(tmp_path / 'local-data.sqlite3'),
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            lobby_item = app.query_one(SideLobbyMapItem)
            await pilot.click(lobby_item, offset=(0, 0))

            for _ in range(20):
                await pilot.pause(0.05)
                child_lists = list(lobby_item.query(MapList))
                if child_lists and _map_infos(child_lists[0].maps) == (easy_map,):
                    break
            else:
                pytest.fail('A 面大厅未展示自己的日志地图。')

            next_button = next(
                button for button in lobby_item.query(MapSideButton) if button.direction > 0
            )
            await pilot.click(next_button)
            for _ in range(20):
                await pilot.pause(0.05)
                child_lists = list(lobby_item.query(MapList))
                if child_lists and _map_infos(child_lists[0].maps) == (hard_map,):
                    break
            else:
                pytest.fail('B 面大厅未展示自己的日志地图。')

            previous_button = next(
                button for button in lobby_item.query(MapSideButton) if button.direction < 0
            )
            await pilot.click(previous_button)
            for _ in range(20):
                await pilot.pause(0.05)
                child_lists = list(lobby_item.query(MapList))
                if child_lists and _map_infos(child_lists[0].maps) == (easy_map,):
                    break
            else:
                pytest.fail('切回 A 面后未恢复已解析的日志地图。')

            assert calls == [str(lobby_a.file_path), str(lobby_b.file_path)]

    asyncio.run(check())


def test_campaign_detail_exposes_only_overrides_of_its_active_maps() -> None:
    first = make_installed_mod(
        source='zip',
        filename='First.zip',
        path='C:/Celeste/Mods/First.zip',
        metadata_name='First',
        metadata_version='1.0.0',
        maps=[MapInfo(file_path='Maps/Pack/Map.bin')],
    )
    second = make_installed_mod(
        source='zip',
        filename='Second.zip',
        path='C:/Celeste/Mods/Second.zip',
        metadata_name='Second',
        metadata_version='1.0.0',
        maps=[MapInfo(file_path='Maps/Pack/Map.bin')],
    )
    app = MapBrowserApp(
        ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[first, second])
    )
    campaign = app._campaigns[0]

    assert len(app._campaign_overrides(campaign)) == 1


def test_detail_pane_scrolls_with_shortcut() -> None:
    maps = [MapInfo(file_path=f'Maps/Test/{index}.bin') for index in range(100)]
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
        app = MapBrowserApp(report)
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
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[MapInfo(file_path='Maps/Test/Map.bin')],
            )
        ],
    )

    async def check() -> None:
        app = MapBrowserApp(report, save_reader=reader, save_slot=reader.load(0))
        async with app.run_test() as pilot:
            map_list = app.query_one(MapList)
            await pilot.press(']')
            assert app._save_slot is not None
            assert app._save_slot.number == 2
            assert app.query_one(MapList) is map_list
            await pilot.press('[')
            assert app._save_slot.number == 0

    asyncio.run(check())


def test_save_slot_switch_keeps_selected_campaign_list(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for number in (0, 1):
        (saves_dir / f'{number}.celeste').write_text('<SaveData />', encoding='utf-8')
    reader = SaveReader(tmp_path / 'Celeste')
    map_info = MapInfo(file_path='Maps/Test/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )

    async def check() -> None:
        app = MapBrowserApp(report, save_reader=reader, save_slot=reader.load(0))
        async with app.run_test() as pilot:
            map_list = app.query_one(MapList)
            await pilot.press(']')
            assert app.query_one(MapList) is map_list

    asyncio.run(check())


def test_ordinary_campaign_maps_keep_path_order_when_switching_save_slots() -> None:
    first = MapInfo(file_path='Maps/Test/First.bin')
    second = MapInfo(file_path='Maps/Test/Second.bin')
    third = MapInfo(file_path='Maps/Test/Third.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[third, first, second],
            )
        ],
    )
    first_slot = SaveSlot(
        0,
        {
            ('Test/First', 0): MapStats(Duration.from_milliseconds(1_000), 1),
            ('Test/Second', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True),
        },
    )
    second_slot = SaveSlot(
        1,
        {
            ('Test/Second', 0): MapStats(Duration.from_milliseconds(1_000), 1),
            ('Test/Third', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True),
        },
    )
    third_slot = SaveSlot(2, {('Test/Third', 0): MapStats(Duration.from_milliseconds(1_000), 1)})

    async def check() -> None:
        app = MapBrowserApp(report, save_slot=first_slot)
        async with app.run_test():
            map_list = app.query_one(MapList)
            assert [item.map_info for item in map_list.query(MapItem)] == [first, second, third]
            app._save_slot = second_slot
            await app._refresh_save_stats()
            assert [item.map_info for item in map_list.query(MapItem)] == [first, second, third]
            app._save_slot = third_slot
            await app._refresh_save_stats()
            assert [item.map_info for item in map_list.query(MapItem)] == [first, second, third]

    asyncio.run(check())


def test_save_slot_switch_restores_highlight_when_selected_map_keeps_its_index() -> None:
    first = MapInfo(file_path='Maps/Test/First.bin')
    second = MapInfo(file_path='Maps/Test/Second.bin')
    third = MapInfo(file_path='Maps/Test/Third.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
        5,
        {
            ('Test/First', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True),
            ('Test/Second', 0): MapStats(Duration.from_milliseconds(1_000), 1),
        },
    )
    second_slot = SaveSlot(
        6,
        {
            ('Test/First', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True),
            ('Test/Third', 0): MapStats(Duration.from_milliseconds(1_000), 1),
        },
    )

    async def check() -> None:
        app = MapBrowserApp(report, save_slot=first_slot)
        async with app.run_test() as pilot:
            map_list = app.query_one(MapList)
            first_item = map_list.query_one(MapItem)
            assert map_list.highlighted_child is first_item
            assert first_item.highlighted

            app._save_slot = second_slot
            await app._refresh_save_stats()

            selected_item = map_list.query_one(MapItem)
            assert selected_item.map_info is first
            assert map_list.highlighted_child is selected_item
            assert selected_item.highlighted
            await pilot.click(selected_item)
            assert selected_item.highlighted

    asyncio.run(check())


def test_save_slot_switch_preserves_selected_b_side_and_its_save_slot() -> None:
    a_side = MapInfo(file_path='Maps/Test/Map.bin')
    b_side = MapInfo(file_path='Maps/Test/Map-B.bin')
    other = MapInfo(file_path='Maps/Test/Other.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[a_side, b_side, other],
            )
        ],
    )
    first_slot = SaveSlot(
        0,
        {('Test/Other', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True)},
    )
    second_slot = SaveSlot(
        1,
        {('Test/Map', 1): MapStats(Duration.from_milliseconds(1_000), 1, completed=True)},
    )

    async def check() -> None:
        app = MapBrowserApp(report, save_slot=first_slot)
        async with app.run_test():
            map_list = app.query_one(MapList)
            side_item = next(item for item in map_list.map_items if isinstance(item, SideMapItem))
            map_list.index = map_list.map_items.index(side_item)
            side_item.switch_side(MapSideButton.Clicked(1))
            assert side_item.loaded_map.map_info is b_side

            app._save_slot = second_slot
            await app._refresh_save_stats()

            selected = map_list.highlighted_child
            assert isinstance(selected, SideMapItem)
            assert selected.loaded_map.map_info is b_side
            content = str(selected.query_one('.map-item-content', Static).render())
            assert '0:00:01' in content

    asyncio.run(check())


@pytest.mark.parametrize(
    ('sides',),
    [
        ((None, 'C'),),
        (('B', 'C'),),
        (('B',),),
    ],
)
def test_incomplete_map_sides_remain_independent_browser_levels(
    sides: tuple[Literal['B', 'C'] | None, ...],
) -> None:
    mod = make_installed_mod(
        source='zip',
        filename='Test.zip',
        path='C:/Celeste/Mods/Test.zip',
        metadata_name='Test',
        metadata_version='1.0.0',
    )
    loaded_maps = tuple(
        LoadedModMap(
            MapInfo(
                file_path=f'Maps/Test/Map{("-" + side) if side else ""}.bin',
            ),
            mod,
        )
        for side in sides
    )
    maps = tuple(
        (level, side) for level in assemble_mod_levels(loaded_maps) for side in level.maps_by_side
    )

    assert _map_side_groups(maps) == tuple((level, (side,)) for level, side in maps)


def test_complete_a_b_map_sides_share_one_browser_level() -> None:
    mod = make_installed_mod(
        source='zip',
        filename='Test.zip',
        path='C:/Celeste/Mods/Test.zip',
        metadata_name='Test',
        metadata_version='1.0.0',
    )
    levels = assemble_mod_levels(
        (
            LoadedModMap(MapInfo(file_path='Maps/Test/Map.bin'), mod),
            LoadedModMap(MapInfo(file_path='Maps/Test/Map-B.bin'), mod),
        )
    )
    level = levels[0]

    assert _map_side_groups(tuple((level, side) for side in level.maps_by_side)) == (
        (level, (LevelSide.A, LevelSide.B)),
    )


def test_map_selection_requires_right_click_to_write_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                dialogs={'en': {'Example_Map': 'Example Map'}},
                maps=[map_info],
            )
        ],
    )
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')

    save_slot = SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)})
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({'主表': {'红草莓数': 2}}))
        ),
    )

    async def check() -> None:
        app = MapBrowserApp(report, local_data=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            map_list = app.query_one(MapList)
            map_list.focus()
            await pilot.press('enter')
            with pytest.raises(ValueError, match='No saved local record'):
                local_data.load_record(1)
            await pilot.double_click(map_list.query_one(MapItem))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert app.screen._progress is MapRecordProgress.RAN_BEFORE
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.map_name == 'Example Map'
    assert record.mod_metadata_name == 'Example'
    assert record.record_values == {'主表': {'红草莓数': 2}}


@pytest.mark.parametrize(
    ('status', 'blocks_record'),
    (
        (CollectedEntityRuleIssueStatus.UNMATCHED, True),
        (CollectedEntityRuleIssueStatus.EXCLUDED, True),
        (CollectedEntityRuleIssueStatus.UNREVIEWED_VARIANT, True),
        (CollectedEntityRuleIssueStatus.NOT_FOUND, False),
    ),
)
def test_collected_entity_rule_review_controls_record_editing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: CollectedEntityRuleIssueStatus,
    blocks_record: bool,
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    source_load_count = 0
    review_count = 0

    def load_source(*_args: object, **_kwargs: object) -> StubMapEntityRecordSource:
        nonlocal source_load_count
        source_load_count += 1

        def load_review() -> StubMapEntityRecordReview:
            nonlocal review_count
            review_count += 1
            return StubMapEntityRecordReview(
                StubMapEntityStats({}),
                (CollectedEntityRuleIssue(MapEntityID('room', 3), status, 'Example/UnknownBerry'),),
            )

        return StubMapEntityRecordSource(load_review)

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source', load_source
    )
    save_slot = SaveSlot(
        0,
        {
            ('Example/Map', 0): MapStats(
                Duration.from_milliseconds(1_000),
                1,
                collected_strawberries=frozenset({MapEntityID('room', 3)}),
            )
        },
    )

    async def check() -> None:
        app = MapBrowserApp(
            report,
            local_data=LocalDataStore(tmp_path / '.pist/local-data.sqlite3'),
            save_slot=save_slot,
        )
        async with app.run_test() as pilot:
            map_item = app.query_one(MapItem)
            await pilot.double_click(map_item)
            assert isinstance(app.screen, CollectedEntityRulesScreen) is blocks_record
            if blocks_record:
                await pilot.click('#collected-entity-rules-refresh')
                await pilot.pause()
                assert isinstance(app.screen, CollectedEntityRulesScreen)
                assert source_load_count == 1
                assert review_count == 2
            else:
                assert isinstance(app.screen, RecordEditorScreen)
                assert source_load_count == 1
                assert review_count == 1

    asyncio.run(check())


def test_refreshing_collected_entity_rules_resumes_record_editing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ],
    )
    source_load_count = 0
    review_count = 0

    def load_source(*_args: object, **_kwargs: object) -> StubMapEntityRecordSource:
        nonlocal source_load_count
        source_load_count += 1

        def load_review() -> StubMapEntityRecordReview:
            nonlocal review_count
            review_count += 1
            issues = (
                (
                    CollectedEntityRuleIssue(
                        MapEntityID('room', 3),
                        CollectedEntityRuleIssueStatus.UNMATCHED,
                        'Example/UnknownBerry',
                    ),
                )
                if review_count == 1
                else ()
            )
            return StubMapEntityRecordReview(StubMapEntityStats({}), issues)

        return StubMapEntityRecordSource(load_review)

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source', load_source
    )
    save_slot = SaveSlot(
        0,
        {
            ('Example/Map', 0): MapStats(
                Duration.from_milliseconds(1_000),
                1,
                collected_strawberries=frozenset({MapEntityID('room', 3)}),
            )
        },
    )

    async def check() -> None:
        app = MapBrowserApp(
            report,
            local_data=LocalDataStore(tmp_path / '.pist/local-data.sqlite3'),
            save_slot=save_slot,
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(MapItem))
            assert isinstance(app.screen, CollectedEntityRulesScreen)
            await pilot.click('#collected-entity-rules-refresh')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            assert source_load_count == 1
            assert review_count == 2

    asyncio.run(check())


def test_record_includes_saved_main_room_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
    local_data.save_route(
        MapRoute(map_file=map_info.file_path.as_posix(), rooms=('start', 'middle', 'goal'))
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({'主表': {'红草莓数': 2}}))
        ),
    )

    async def check() -> None:
        app = MapBrowserApp(
            report,
            local_data=local_data,
            save_slot=SaveSlot(
                0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)}
            ),
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(MapItem))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.record_values == {'主表': {'红草莓数': 2, '主房间数': 3}}


@pytest.mark.parametrize('trigger', ('shortcut', 'context_menu'))
def test_map_preview_starts_in_preview_mode_without_implicit_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
    monkeypatch.setattr('pist.ui.maps.browser.app.routes.load_loaded_map_layout', lambda *_: layout)
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    started = asyncio.Event()
    finish = asyncio.Event()
    preview_options: list[dict[str, object]] = []

    class Preview:
        def __init__(self, *_: object, **options: object) -> None:
            preview_options.append(options)

        async def preview(self):
            started.set()
            await finish.wait()

    monkeypatch.setattr('pist.ui.maps.browser.app.MapPreview', Preview)

    async def check() -> None:
        app = MapBrowserApp(report, local_data=local_data)
        async with app.run_test() as pilot:
            map_item = app.query_one(MapItem)
            if trigger == 'shortcut':
                await pilot.click(map_item)
                await pilot.press('p')
            else:
                await pilot.click(map_item, button=3)
                await pilot.click('#map-action-preview')
            await started.wait()
            previous_theme = app.theme
            await pilot.press('t')
            assert app.theme != previous_theme
            finish.set()
            await pilot.pause()

    asyncio.run(check())
    assert local_data.load_route(map_info.file_path.as_posix()) is None
    assert preview_options[0]['initial_mode'] is MapPreviewMode.PREVIEW
    assert 'read_only' not in preview_options[0]
    routable_maps = preview_options[0]['routable_maps']
    assert isinstance(routable_maps, dict)
    routable_level, routable_side = routable_maps['Example/Map']
    assert isinstance(routable_level.maps_by_side[routable_side], LoadedModMap)


def test_vanilla_route_editor_starts_in_review_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_maps = tmp_path / 'Content' / 'Maps'
    content_maps.mkdir(parents=True)
    (content_maps / '1-ForsakenCity.bin').touch()
    report = ModScanReport(mods_dir=str(tmp_path / 'Mods'), disabled_filenames=[], mods=[])
    layout = MapLayout((MapRoom('start', 0, 0, 320, 184),))
    monkeypatch.setattr('pist.ui.maps.browser.app.routes.load_loaded_map_layout', lambda *_: layout)
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    started = asyncio.Event()
    preview_sources: list[object] = []

    class Preview:
        def __init__(self, source: object, *_: object, **options: object) -> None:
            preview_sources.append(source)
            assert options['initial_mode'] is MapPreviewMode.REVIEW
            assert 'read_only' not in options

        async def preview(self) -> None:
            started.set()

    monkeypatch.setattr('pist.ui.maps.browser.app.MapPreview', Preview)

    async def check() -> None:
        app = MapBrowserApp(report, local_data=local_data)
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(MapItem), button=3)
            await pilot.click('#map-action-edit-route')
            await started.wait()
            await pilot.pause()

    asyncio.run(check())
    assert len(preview_sources) == 1
    assert isinstance(preview_sources[0], LoadedVanillaMap)
    assert local_data.load_route('Maps/1-ForsakenCity.bin') is None


def test_map_selected_in_detail_edits_credit_authors_from_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
    save_slot = SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1)})
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        app = MapBrowserApp(
            report,
            local_data=local_data,
            save_slot=save_slot,
            gamebanana_client=StubGameBananaClient(),
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(MapItem))
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click(app.screen.query_one(RecordAuthorField))
            await pilot.pause()
            assert isinstance(app.screen, AuthorSelectionScreen)
            await pilot.click('#record-author-0')
            await pilot.pause()
            await pilot.click('#author-select-save')
            await pilot.pause()
            assert isinstance(app.screen, RecordEditorScreen)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.load_record(1)
    assert record.authors == ('Alice',)
    assert record.credits[1].group_name == 'Special Thanks'


def test_collab_map_edits_multiple_dialog_authors_from_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path='Maps/Expert/Example.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                collab_id='ExampleCollab',
                dialogs={'en': {'Expert_Example_author': 'by Alice Smith and Bob Jones'}},
                maps=[map_info],
            )
        ],
    )
    local_data = LocalDataStore(tmp_path / '.pist/local-data.sqlite3')
    save_slot = SaveSlot(0, {('Expert/Example', 0): MapStats(Duration.from_milliseconds(1_000), 1)})
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        app = MapBrowserApp(report, local_data=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(MapItem))
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
    report = ModScanReport(mods_dir='C:/Celeste/Mods', disabled_filenames=[], mods=[])
    saved: list[MapRecord] = []

    async def check() -> None:
        app = MapBrowserApp(report)
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


def test_map_without_record_does_not_open_record_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path='Maps/Example/Map.bin')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
    save_slot = SaveSlot(0, {('Example/Map', 0): MapStats(Duration.from_milliseconds(), 1)})
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.routes.load_loaded_map_entity_record_source',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        app = MapBrowserApp(report, local_data=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(MapItem))
            assert not isinstance(app.screen, RecordEditorScreen)

    asyncio.run(check())
    with pytest.raises(ValueError, match='No saved local record'):
        local_data.load_record(1)


def test_theme_shortcut_persists_selection(tmp_path: Path) -> None:
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[],
    )
    settings_store = SettingsStore(tmp_path / 'settings.json')

    async def check() -> None:
        app = MapBrowserApp(report, settings_store=settings_store)
        async with app.run_test() as pilot:
            await pilot.press('t')
            assert app.theme == 'textual-light'

    asyncio.run(check())
    assert settings_store.load().theme == 'textual-light'


def test_theme_shortcut_preserves_game_settings(tmp_path: Path) -> None:
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
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
        app = MapBrowserApp(report, settings_store=settings_store)
        async with app.run_test() as pilot:
            await pilot.press('t')

    asyncio.run(check())
    settings = settings_store.load()
    assert settings.game_dir == Path('C:/Celeste')
    assert settings.smartsheet_url == 'https://docs.qq.com/smartsheet/example'


def test_escape_is_bound_to_quit() -> None:
    assert ('escape', 'quit', '退出') in MapBrowserApp.BINDINGS


def test_page_keys_are_not_bound_to_save_slot_switching() -> None:
    assert ('pageup', 'previous_save_slot', '上一存档') not in MapBrowserApp.BINDINGS
    assert ('pagedown', 'next_save_slot', '下一存档') not in MapBrowserApp.BINDINGS


def test_campaigns_are_top_level_list_entries() -> None:
    maps = [MapInfo(file_path='Maps/TestCollab/1-Beginner/Map.bin')]
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='zip',
                filename='TestCollab.zip',
                path='C:/Celeste/Mods/TestCollab.zip',
                metadata_name='TestCollab',
                metadata_version='1.0.0',
                collab_id='TestCollab',
                maps=maps,
            )
        ],
    )

    async def check() -> None:
        app = MapBrowserApp(report)
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            campaign_list = app.query_one(CampaignList)
            items = list(campaign_list.query(CampaignItem))
            assert len(items) == 1
            assert items[0].campaign is app._campaigns[0]
            assert isinstance(app.query_one(CollabMapList), CollabMapList)

    asyncio.run(check())


def test_collab_map_list_orders_maps_and_reuses_cached_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = [
        MapInfo(file_path='Maps/Test/Hard.bin'),
        MapInfo(file_path='Maps/Test/Easy.bin'),
        MapInfo(file_path='Maps/Test/Normal.bin'),
    ]
    for map_info, icon in zip(
        maps,
        (
            'areas/Test/meters/2-hard',
            'areas/Test/meters/1-easy',
            'areas/Test/meters/3-normal',
        ),
        strict=True,
    ):
        write_map_with_icon(tmp_path / map_info.file_path, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=str(tmp_path),
        metadata_name='Test',
        metadata_version=None,
        collab_id='Test',
        maps=maps,
    )
    report = ModScanReport(mods_dir=str(tmp_path), disabled_filenames=[], mods=[mod])
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    first_slot = SaveSlot(
        0,
        {
            ('Test/Easy', 0): MapStats(Duration.from_milliseconds(1_000), 1),
            ('Test/Normal', 0): MapStats(Duration.from_milliseconds(1_000), 1),
            (
                'Test/Hard',
                0,
            ): MapStats(Duration.from_milliseconds(1_000), 1, single_run_completed=True),
        },
    )
    second_slot = SaveSlot(
        1,
        {('Test/Easy', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True)},
    )

    async def map_order(app: MapBrowserApp) -> list[str]:
        async with app.run_test(size=(100, 40)) as pilot:
            collab_list = app.query_one(CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            assert not collab_list.has_class('is-loading')
            map_list = collab_list.query_one(MapList)
            assert collab_list.size.height > 1
            return [item.map_info.file_path.as_posix() for item in map_list.query(MapItem)]

    assert asyncio.run(
        map_order(MapBrowserApp(report, local_data=local_data, save_slot=first_slot))
    ) == [
        'Maps/Test/Hard.bin',
        'Maps/Test/Easy.bin',
        'Maps/Test/Normal.bin',
    ]

    def unexpected_map_read(*_: object) -> object:
        raise AssertionError('A cached Collab map list must not read map files again.')

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.game_mods.iter_collab_journal_map_icons', unexpected_map_read
    )

    assert asyncio.run(
        map_order(MapBrowserApp(report, local_data=local_data, save_slot=first_slot))
    ) == [
        'Maps/Test/Hard.bin',
        'Maps/Test/Easy.bin',
        'Maps/Test/Normal.bin',
    ]

    async def switched_map_order() -> list[str]:
        app = MapBrowserApp(report, local_data=local_data, save_slot=first_slot)
        async with app.run_test(size=(100, 40)) as pilot:
            collab_list = app.query_one(CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            app._save_slot = second_slot
            await app._refresh_save_stats()
            return [
                item.map_info.file_path.as_posix()
                for item in collab_list.query_one(MapList).query(MapItem)
            ]

    assert asyncio.run(switched_map_order()) == [
        'Maps/Test/Easy.bin',
        'Maps/Test/Hard.bin',
        'Maps/Test/Normal.bin',
    ]


def test_collab_map_order_sorts_icons_per_campaign_before_progress() -> None:
    first_maps = (
        MapInfo(file_path='Maps/First/Hard.bin'),
        MapInfo(file_path='Maps/First/Easy.bin'),
    )
    second_maps = (
        MapInfo(file_path='Maps/Second/WithoutIcon.bin'),
        MapInfo(file_path='Maps/Second/WithIcon.bin'),
    )
    mod = make_installed_mod(
        source='zip',
        filename='Collab.zip',
        path='C:/Celeste/Mods/Collab.zip',
        metadata_name='Collab',
        metadata_version=None,
        maps=[*first_maps, *second_maps],
    )
    first_group = tuple(_map_ref(mod, map_info) for map_info in first_maps)
    second_group = tuple(_map_ref(mod, map_info) for map_info in second_maps)
    icon_order = maps_by_campaign_icon_order(
        (first_group, second_group),
        {
            first_maps[0].file_path.as_posix(): 'areas/First/2-hard',
            first_maps[1].file_path.as_posix(): 'areas/First/1-easy',
            second_maps[0].file_path.as_posix(): None,
            second_maps[1].file_path.as_posix(): 'areas/Second/1-map',
        },
    )

    assert _map_infos(icon_order) == (
        first_maps[1],
        first_maps[0],
        second_maps[0],
        second_maps[1],
    )

    save_slot = SaveSlot(
        0,
        {('Second/WithIcon', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True)},
    )
    assert _map_infos(maps_by_progress(icon_order, save_slot)) == (
        second_maps[1],
        first_maps[1],
        first_maps[0],
        second_maps[0],
    )


def test_collab_map_list_uses_the_latest_save_slot_after_loading_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = [
        MapInfo(file_path='Maps/Test/Hard.bin'),
        MapInfo(file_path='Maps/Test/Easy.bin'),
    ]
    for map_info, icon in zip(
        maps,
        ('areas/Test/meters/2-hard', 'areas/Test/meters/1-easy'),
        strict=True,
    ):
        write_map_with_icon(tmp_path / map_info.file_path, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=str(tmp_path),
        metadata_name='Test',
        metadata_version=None,
        collab_id='Test',
        maps=maps,
    )
    report = ModScanReport(mods_dir=str(tmp_path), disabled_filenames=[], mods=[mod])
    first_slot = SaveSlot(
        0,
        {('Test/Hard', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True)},
    )
    second_slot = SaveSlot(
        1,
        {('Test/Easy', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True)},
    )
    remove_started = asyncio.Event()
    resume_remove = asyncio.Event()
    original_remove_children = CollabMapList.remove_children

    async def pause_first_remove(self: CollabMapList) -> None:
        if not remove_started.is_set():
            remove_started.set()
            await resume_remove.wait()
        await original_remove_children(self)

    monkeypatch.setattr(CollabMapList, 'remove_children', pause_first_remove)

    async def check() -> list[str]:
        app = MapBrowserApp(report, save_slot=first_slot)
        async with app.run_test(size=(100, 40)) as pilot:
            await asyncio.wait_for(remove_started.wait(), timeout=1)
            app._save_slot = second_slot
            await app._refresh_save_stats()
            resume_remove.set()
            collab_list = app.query_one(CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            return [
                item.map_info.file_path.as_posix()
                for item in collab_list.query_one(MapList).query(MapItem)
            ]

    assert asyncio.run(check()) == ['Maps/Test/Easy.bin', 'Maps/Test/Hard.bin']


def test_collab_map_list_keeps_selected_side_and_new_slot_stats_when_reordered(
    tmp_path: Path,
) -> None:
    a_side = MapInfo(file_path='Maps/Test/Map.bin')
    b_side = MapInfo(file_path='Maps/Test/Map-B.bin')
    other = MapInfo(file_path='Maps/Test/Other.bin')
    maps = [a_side, b_side, other]
    for map_info, icon in zip(
        maps,
        ('areas/Test/meters/2-map', 'areas/Test/meters/2-map-b', 'areas/Test/meters/1-other'),
        strict=True,
    ):
        write_map_with_icon(tmp_path / map_info.file_path, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=str(tmp_path),
        metadata_name='Test',
        metadata_version=None,
        collab_id='Test',
        maps=maps,
    )
    report = ModScanReport(mods_dir=str(tmp_path), disabled_filenames=[], mods=[mod])
    first_slot = SaveSlot(
        0,
        {
            ('Test/Map', 0): MapStats(Duration.from_milliseconds(1_000), 1, completed=True),
            ('Test/Map', 1): MapStats(Duration.from_milliseconds(1_000), 1, completed=True),
        },
    )
    second_slot = SaveSlot(
        1,
        {
            ('Test/Map', 0): MapStats(Duration.from_milliseconds(2_000), 2),
            ('Test/Map', 1): MapStats(Duration.from_milliseconds(4_000), 4),
            (
                'Test/Other',
                0,
            ): MapStats(Duration.from_milliseconds(1_000), 1, single_run_completed=True),
        },
    )

    async def check() -> None:
        app = MapBrowserApp(report, save_slot=first_slot)
        async with app.run_test(size=(100, 40)) as pilot:
            collab_list = app.query_one(CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            map_list = collab_list.query_one(MapList)
            side_item = next(item for item in map_list.map_items if isinstance(item, SideMapItem))
            map_list.index = map_list.map_items.index(side_item)
            side_item.switch_side(MapSideButton.Clicked(1))
            assert side_item.loaded_map.map_info is b_side
            app._save_slot = second_slot
            await app._refresh_save_stats()
            refreshed_list = collab_list.query_one(MapList)
            refreshed_side_item = next(
                item for item in refreshed_list.map_items if isinstance(item, SideMapItem)
            )
            assert refreshed_side_item.loaded_map.map_info is b_side
            b_side_text = str(refreshed_side_item.query_one('.map-item-content', Static).render())
            refreshed_side_item.switch_side(MapSideButton.Clicked(-1))
            a_side_text = str(refreshed_side_item.query_one('.map-item-content', Static).render())
            assert '0:00:04' in b_side_text
            assert '0:00:02' in a_side_text

    asyncio.run(check())


def test_campaign_switch_replaces_the_detail_map_list() -> None:
    maps = [MapInfo(file_path=f'Maps/Test/{name}/Map.bin') for name in ('Prologue', 'Beginner')]
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
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
        app = MapBrowserApp(report)
        async with app.run_test(size=(100, 40)) as pilot:
            campaign_list = app.query_one(CampaignList)
            campaign_list.index = 1
            await pilot.pause()
            assert [item.map_info for item in app.query(MapItem)] == [maps[0]]

    asyncio.run(check())


def test_campaign_switch_loads_the_new_collab_map_list(tmp_path: Path) -> None:
    maps = [
        MapInfo(file_path='Maps/Normal/Map.bin'),
        MapInfo(file_path='Maps/Test/Map.bin'),
    ]
    write_map_with_icon(tmp_path / maps[1].file_path, 'areas/Test/meters/1-easy')
    report = ModScanReport(
        mods_dir='C:/Celeste/Mods',
        disabled_filenames=[],
        mods=[
            make_installed_mod(
                source='directory',
                filename='Test.zip',
                path=str(tmp_path),
                metadata_name='Test',
                metadata_version='1.0.0',
                collab_id='Test',
                maps=maps,
            )
        ],
    )

    async def check() -> None:
        app = MapBrowserApp(report)
        async with app.run_test() as pilot:
            campaign_list = app.query_one(CampaignList)
            campaign_list.index = next(
                index
                for index, item in enumerate(campaign_list.children)
                if isinstance(item, CampaignItem)
                and item.campaign.directory.as_posix() == 'Maps/Test'
            )
            await pilot.pause()
            collab_list = app.query_one(CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            assert not collab_list.has_class('is-loading')
            assert [item.map_info for item in collab_list.query_one(MapList).map_items] == [maps[1]]

    asyncio.run(check())
