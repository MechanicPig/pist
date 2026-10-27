import asyncio
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from struct import pack
from threading import Event
from typing import Literal
from zipfile import ZipFile

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from catalog_factory import make_campaign_catalog
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Button, Checkbox, Input, Select, SelectionList, Static, TextArea
from textual.widgets.text_area import Selection

from berries import map_layout
from berries.containers import CaseFoldDict
from berries.entities.classification import CollectedEntityRuleIssue, CollectedEntityRuleIssueStatus
from berries.entities.config import (
    load_default_entity_rules,
)
from berries.entities.rules import EntityRules
from berries.game import collab as game_collab
from berries.game.binmap import BinElement, BinMap, MapStringDecodeWarning
from berries.game.campaigns import CampaignCatalog
from berries.game.content import ContentPath
from berries.game.duration import Duration
from berries.game.everest import Dependency, Version
from berries.game.levels import (
    Level,
    LevelSide,
    assemble_mod_levels,
    local_sid_for_map_file,
)
from berries.game.maps import Map, ModMap
from berries.game.mods import InstalledMod
from berries.game.saves.files.main import MainSave
from berries.gamebanana import GameBananaClient, GameBananaSubmission
from berries.map_entity_id import MapEntityID
from berries.map_entrances import MapEntranceRules
from berries.map_layout import MapEntrance, MapLayout, MapRoom
from pist import map_names
from pist.campaign_catalog import CatalogLoadResult
from pist.data_stores import DataStores
from pist.entities.audit import EntityAuditStore, ObservationQuestion, ObservationStatus
from pist.entities.audit.models import AuditGroup, AuditOccurrence, AuditReport, AuditSource
from pist.entities.collected import (
    CollectedEntityReviewIssue,
    UnreviewedCollectedEntity,
    VariantReviewLoader,
)
from pist.lobby_overrides import CollabLobbyOverride, CollabLobbyOverrides, CollabLobbyOverrideStore
from pist.map_preview import MapPreviewMode
from pist.map_preview.server import MapPreview
from pist.mod_scan import ModScanner
from pist.records.entities import MapEntityRecordSource
from pist.records.fields import RECORD_ATTRIBUTES
from pist.records.lock import record_writer_lock
from pist.records.models import MapRecord, MapRecordProgress
from pist.routes.models import MapRoute
from pist.saves import MapStats, SaveProgress, SaveProgressReader
from pist.settings import PistSettings, SettingsStore
from pist.smartsheet.report import ManualRecordField
from pist.types import RecordValues
from pist.ui.maps import browser
from pist.ui.maps.browser.app import browse_maps
from pist.ui.maps.browser.campaign_list import CampaignListToggle
from pist.ui.maps.browser.catalog import map_save_stats
from pist.ui.maps.browser.collab_list import LobbyMapItem, SideLobbyMapItem
from pist.ui.maps.browser.collab_order import maps_by_campaign_icon_order, maps_by_progress
from pist.ui.maps.browser.map_list import MapSideButton, SideMapItem
from pist.ui.messages import MessageCard, MessagesScreen
from pist.ui.records.controller import EditorFactory, RecordEditorController
from pist.ui.records.editor import RECORD_FIELD_ORDER, RecordRefSummary
from test_support.mod_factory import make_installed_mod
from test_support.save_docs import normal_save_xml
from test_support.saves import MemorySaveReader, make_save_progress


@pytest.mark.parametrize(
    ('milliseconds', 'display'),
    ((83_456, '01:23'), (3_599_999, '59:59'), (3_600_000, '1:00:00')),
)
def test_map_save_stats_uses_game_timer_format(milliseconds: int, display: str) -> None:
    slot = make_save_progress(
        2, {('Example/Map', 0): MapStats(Duration.from_dotnet_ticks(milliseconds * 10_000), 12)}
    )
    assert map_save_stats('Example/Map', LevelSide.A, slot).plain == (
        f'  存档 2: 用时 {display}，死亡 12'
    )


type LevelSelection = tuple[Level, LevelSide]


@pytest.fixture(autouse=True)
def isolated_browser_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep presentation tests on real SQLite stores without touching personal data."""

    def create_stores() -> DataStores:
        return DataStores(tmp_path / 'browser.sqlite3')

    monkeypatch.setattr('pist.ui.maps.browser.app.DataStores', create_stores)


async def _wait_for_save_switch(app: browser.MapBrowserApp) -> None:
    async with asyncio.timeout(2):
        while app.screen_stack[0].has_class('save-loading'):
            await asyncio.sleep(0.01)


def test_browser_uses_injected_catalog_without_loading_game_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_catalog = make_campaign_catalog()

    def unexpected_load(*args: object, **kwargs: object) -> None:
        pytest.fail('The browser must not load an installation when a catalog is injected.')

    monkeypatch.setattr(CampaignCatalog, 'from_game_dir', unexpected_load)
    app = browser.MapBrowserApp(
        campaign_catalog,
        diagnostics=('独立传入的加载消息',),
        data_stores=DataStores(tmp_path / 'local.sqlite3'),
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            assert not app.query(browser.CampaignItem)
            assert app.diagnostic_count == 1
            await app.action_show_messages()
            await pilot.pause()
            assert [card.text for card in app.screen.query(MessageCard)] == ['独立传入的加载消息']

    asyncio.run(check())


@pytest.mark.parametrize(
    'rooms,counts,expected', ((['start', 'goal'], {'start': 3}, 4), ([], {}, None))
)
def test_route_save_refreshes_open_record_room_count_without_losing_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rooms: list[str],
    counts: dict[str, int],
    expected: int | None,
) -> None:
    info = ContentPath('Maps/Example/Map.bin')
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=tmp_path / 'Example',
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[info],
    )
    level, side = _map_ref(mod, info)
    stores = DataStores(tmp_path / 'local.sqlite3')
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod], game_dir=tmp_path),
        data_stores=stores,
        save_slot=make_save_progress(0, {(level.sid, side): MapStats(Duration.from_seconds(1), 1)}),
    )
    monkeypatch.setattr(
        MapEntityRecordSource,
        'from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({'主表': {'主房间数': 2}}))
        ),
    )
    layout = MapLayout((MapRoom('start', 0, 0, 8, 8), MapRoom('goal', 8, 0, 8, 8)))
    monkeypatch.setattr(map_layout.MapLayout, 'from_map', lambda _, **kwargs: layout)

    async def preview(self: MapPreview) -> None:
        server = web.Application()
        server.router.add_post('/save', self._save)
        async with TestClient(TestServer(server)) as client:
            response = await client.post('/save', json={'rooms': rooms, 'roomCounts': counts})
            assert response.status == 200

    monkeypatch.setattr(MapPreview, 'preview', preview)

    async def run() -> None:
        async with app.run_test(size=(160, 55)) as pilot:
            await pilot.double_click(app.query_one(browser.MapItem), offset=(3, 0))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, browser.RecordEditorScreen)
            notes = screen.query_one(f'#record-manual-{RECORD_FIELD_ORDER.index("备注")}', Input)
            await pilot.click(notes)
            await pilot.press(*'Unsaved note')
            route_field = screen.query_one(browser.RecordRouteField)
            route_field.scroll_visible(animate=False, force=True)
            await pilot.pause()
            await pilot.click(route_field)
            control = screen.query_one(
                f'#record-manual-{RECORD_FIELD_ORDER.index("主房间数")}', Input
            )
            async with asyncio.timeout(5):
                while control.value != (str(expected) if expected is not None else ''):
                    await pilot.pause(0.01)
            assert notes.value == 'Unsaved note'
            saved_route = stores.routes.load(info.as_posix())
            assert saved_route is not None
            assert saved_route.room_count == sum(counts.get(room, 1) for room in rooms)
            await pilot.click('#record-confirm-save')
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    saved = stores.records.load(1)
    assert saved.n_main_rooms == expected and saved.notes == 'Unsaved note'


@pytest.mark.parametrize('valid', [False, True])
def test_first_clear_records_are_loaded_or_warn_without_blocking_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    MainSave().write(tmp_path / '0.celeste')
    (tmp_path / '0-modsave-EndersBlender.celeste').write_text(
        'mapDict_roomStat_firstClear_roomOrder:\n  Example/Map: '
        + ('[room]' if valid else 'invalid')
        + '\nmapDict_roomStat_firstClear_death: {Example/Map: {room: 3}}\n'
        + 'mapDict_roomStat_firstClear_timer: {Example/Map: {room: 0}}\n',
        encoding='utf-8',
    )
    reader = SaveProgressReader(tmp_path)
    info = ContentPath('Maps/Example/Map.bin')
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=tmp_path / 'Example',
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[info],
    )
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod], game_dir=tmp_path),
        data_stores=DataStores(tmp_path / 'local.sqlite3'),
        save_reader=reader,
        save_slot=reader.load(0),
    )
    layout = MapLayout((MapRoom('room', 0, 0, 8, 8),))
    monkeypatch.setattr(map_layout.MapLayout, 'from_map', lambda _, **kwargs: layout)
    opened = False

    async def preview(self: MapPreview) -> None:
        nonlocal opened
        opened = True
        if valid:
            assert self._first_clear_lookup is not None
            stats = self._first_clear_lookup('Example/Map', LevelSide.A)
            assert stats is not None and stats.room_deaths == {'room': 3}
        else:
            assert self._first_clear_lookup is None

    monkeypatch.setattr(MapPreview, 'preview', preview)

    async def run() -> None:
        async with app.run_test() as pilot:
            app.query_one(browser.MapList).focus()
            await pilot.press('p')
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert opened
            assert app.diagnostic_count == (0 if valid else 1)

    asyncio.run(run())


def test_navigation_retains_real_target_decode_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod_path = tmp_path / 'Example'
    target_dir = mod_path / 'Maps' / 'Example'
    target_dir.mkdir(parents=True)
    (target_dir / 'Target.bin').write_bytes(
        b'\x0bCELESTE MAP\x01\xff' + pack('<H', 1) + b'\x03Map' + pack('<HBH', 0, 0, 0)
    )
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=mod_path,
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[ContentPath(f'Maps/Example/{name}.bin') for name in ('Source', 'Target')],
    )
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod], game_dir=tmp_path),
        data_stores=DataStores(tmp_path / 'local.sqlite3'),
    )
    original = map_layout.MapLayout.from_map

    def load(
        map_file: Map,
        *,
        entity_rules: EntityRules | None = None,
        entrance_rules: MapEntranceRules | None = None,
    ) -> MapLayout:
        if map_file.file_path.name == 'Source.bin':
            return MapLayout(
                (MapRoom('room', 0, 0, 8, 8),), (MapEntrance('room', 'Example/Target'),)
            )
        return original(map_file, entity_rules=entity_rules, entrance_rules=entrance_rules)

    async def preview(self: MapPreview) -> None:
        server = web.Application()
        server.router.add_post('/open', self._open)
        async with TestClient(TestServer(server)) as client:
            response = await client.post('/open', json={'targetSid': 'Example/Target'})
            assert response.status == 200

    monkeypatch.setattr(map_layout.MapLayout, 'from_map', load)
    monkeypatch.setattr(MapPreview, 'preview', preview)

    async def run() -> None:
        async with app.run_test() as pilot:
            maps = app.query_one(browser.MapList)
            maps.index = next(
                i for i, item in enumerate(maps.map_items) if item.level.sid == 'Example/Source'
            )
            maps.focus()
            await pilot.press('p')
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.diagnostic_count == 1
            await app.action_show_messages()
            await pilot.pause()
            card = app.screen.query_one(MessageCard)
            text = str(card.query_one(Static).content)
            assert 'Maps/Example/Target.bin' in text
            assert 'byte 13' in text and 'ff' in text

    asyncio.run(run())


def test_map_decode_warnings_are_retained_without_swallowing_other_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=str(tmp_path / 'Example'),
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[ContentPath('Maps/Example/Map.bin')],
    )
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod], game_dir=tmp_path),
        data_stores=DataStores(tmp_path / 'local.sqlite3'),
    )

    async def check() -> None:
        def read_layout(*_args: object, **_kwargs: object) -> MapLayout:
            warnings.warn(MapStringDecodeWarning(13, b'M\xfcn'), stacklevel=1)
            warnings.warn('unrelated', UserWarning, stacklevel=1)
            return MapLayout((MapRoom('room', 0, 0, 8, 8),))

        async def preview(_self: MapPreview) -> None:
            return

        monkeypatch.setattr(map_layout.MapLayout, 'from_map', read_layout)
        monkeypatch.setattr(MapPreview, 'preview', preview)

        async with app.run_test() as pilot:
            await pilot.click(app.query_one(browser.MapItem), offset=(3, 0))

            async def preview_map() -> None:
                await pilot.press('p')
                await app.workers.wait_for_complete()

            with pytest.warns(UserWarning, match='unrelated'):
                await preview_map()
            await pilot.pause()
            assert app.diagnostic_count == 1
            await app.action_show_messages()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, MessagesScreen)
            card = screen.query_one(MessageCard)
            text = str(card.query_one(Static).content)
            assert 'Maps/Example/Map.bin' in text
            assert 'byte 13' in text
            assert '4dfc6e' in text

    asyncio.run(check())


@pytest.mark.parametrize('collection_fails', (False, True))
def test_new_collected_variant_is_available_for_audit_before_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collection_fails: bool
) -> None:
    audit = EntityAuditStore(tmp_path / 'audit.sqlite3')
    audit_source = AuditSource(scope='mod', map_file='Maps/Example/Map.bin', map_name='Map')
    audit.collect_entities(
        AuditReport(
            entities=(
                AuditGroup(
                    entity_name='strawberry',
                    occurrences=(
                        AuditOccurrence(
                            source=audit_source, room='room', entity_id=1, attrs={'moon': False}
                        ),
                    ),
                ),
            )
        ).entities,
    )
    audit.confirm_entity_kind('strawberry', 'strawberry')
    if collection_fails:

        def fail_collection(groups: Iterable[AuditGroup]) -> int:
            raise OSError('Cannot write audit data')

        monkeypatch.setattr(audit, 'collect_entities', fail_collection)
    entity = BinElement('strawberry', {'id': 509, 'moon': True, 'delay': 0.29999998211860657}, ())
    room = BinElement('level', {'name': 'room'}, (BinElement('entities', {}, (entity,)),))
    map_data = BinMap(
        'Example/Map',
        BinElement(
            'Map',
            {},
            (
                BinElement('levels', {}, (room,)),
                BinElement('meta', {}, (BinElement('mode', {'HeartIsEnd': True}, ()),)),
            ),
        ),
    )
    source = MapEntityRecordSource(map_data, frozenset({MapEntityID('room', 509)}))
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map',
        lambda *_args, **_kwargs: source,
    )
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path=str(tmp_path / 'Example.zip'),
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[ContentPath('Maps/Example/Map.bin')],
            ),
        ],
        game_dir=tmp_path,
    )
    save_slot = make_save_progress(
        0,
        {
            ('Example/Map', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000),
                1,
                collected_strawberries=frozenset({MapEntityID('room', 509)}),
            )
        },
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            data_stores=DataStores(tmp_path / 'local.sqlite3'),
            save_slot=save_slot,
            entity_audit_store=audit,
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem))
            async with asyncio.timeout(5):
                while (
                    not isinstance(app.screen, browser.CollectedEntityRulesScreen)
                    or not app.screen.is_mounted
                ) and not app.diagnostic_count:
                    await asyncio.sleep(0.01)
            if collection_fails:
                assert not isinstance(app.screen, browser.CollectedEntityRulesScreen)
                assert app.diagnostic_count == 1
                assert audit.raw_variant_count('strawberry') == 1
                return
            assert isinstance(app.screen, browser.CollectedEntityRulesScreen)
            await pilot.pause()
            detail = audit.entity_detail('strawberry')
            assert len(detail.variants) == 2
            variant = next(item for item in detail.variants if item.attrs.get('moon') is True)
            assert variant.meta == {'HeartIsEnd': True}
            assert variant.attrs['delay'] == 0.29999998211860657
            occurrences = audit.occurrences('strawberry')
            assert any(
                item.entity_id == 509 and item.source.mod_file == str(tmp_path / 'Example.zip')
                for item in occurrences
            )
            occurrences_before = audit.occurrences('strawberry')
            previous_screen = app.screen
            assert await pilot.click('#collected-entity-rules-refresh')
            async with asyncio.timeout(5):
                while (
                    app.screen is previous_screen
                    or not isinstance(app.screen, browser.CollectedEntityRulesScreen)
                    or not app.screen.is_mounted
                ):
                    await asyncio.sleep(0.01)
            await pilot.pause()
            assert audit.occurrences('strawberry') == occurrences_before
            audit.save_observation(
                'strawberry',
                variant.attrs,
                ObservationQuestion.ENTITY_CLASSIFICATION,
                ObservationStatus.CONFIRMED,
                kind='moonberry',
                meta=variant.meta,
            )
            assert await pilot.click('#collected-entity-rules-refresh')
            async with asyncio.timeout(5):
                while (
                    not isinstance(app.screen, browser.RecordEditorScreen)
                    or not app.screen.is_mounted
                ):
                    await asyncio.sleep(0.01)
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)

    asyncio.run(check())


@pytest.mark.parametrize('other_id', (1, 2))
def test_record_editor_retains_warning_for_duplicate_map_entity_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, other_id: int
) -> None:
    entities = BinElement(
        'entities',
        {},
        (
            BinElement('strawberry', {'id': 1}, ()),
            BinElement('strawberry', {'id': other_id, 'moon': True}, ()),
        ),
    )
    room = BinElement('level', {'name': 'room'}, (entities,))
    map_data = BinMap('Test/Map', BinElement('Map', {}, (BinElement('levels', {}, (room,)),)))
    rules = load_default_entity_rules()
    monkeypatch.setattr(
        'pist.records.entities.config.load_entity_rule_layers', lambda **kwargs: rules
    )
    source = MapEntityRecordSource(map_data, frozenset())
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=tmp_path / 'Test',
        metadata_name='Test',
        metadata_version='1.0.0',
        maps=[ContentPath('Maps/Test/Map.bin')],
    )
    campaign_catalog = make_campaign_catalog(mods=[mod], game_dir=tmp_path)
    monkeypatch.setattr(MapEntityRecordSource, 'from_map', lambda *_args, **_kwargs: source)
    app = browser.MapBrowserApp(
        campaign_catalog,
        data_stores=DataStores(tmp_path / '.pist/local-data.sqlite3'),
        save_slot=make_save_progress(0, {('Test/Map', 0): MapStats(Duration.from_seconds(1), 1)}),
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem), offset=(3, 0))
            assert isinstance(app.screen, browser.RecordEditorScreen)
            review = source.eval_rules()
            assert review.stats.count('strawberry') == review.stats.count('moonberry') == 1
            await pilot.pause()
            if other_id != 1:
                assert app.diagnostic_count == 0
                return
            assert app.diagnostic_count == 1
            await pilot.press('escape')
            await app.action_show_messages()
            await pilot.pause()
            card = app.screen.query_one('#runtime-message-list MessageCard', MessageCard)
            assert 'Test/Map' in card.text
            assert 'room:1' in card.text

    asyncio.run(check())


def _map_ref(mod: InstalledMod, map_info: ContentPath) -> tuple[Level, LevelSide]:
    loaded_map = ModMap(map_info, mod)
    level = Level(
        sid=local_sid_for_map_file(map_info),
        maps=(loaded_map,),
    )
    return level, LevelSide.A


def _map_infos(maps: Iterable[tuple[Level, LevelSide]]) -> tuple[ContentPath, ...]:
    return tuple(level[side].file_path for level, side in maps)


@dataclass(frozen=True, slots=True)
class StubMapEntityStats:
    record_values: RecordValues
    select_conflicts: tuple[object, ...] = ()
    duplicate_instance_ids: frozenset[MapEntityID] = frozenset()

    @property
    def record_fields(self) -> dict[str, int | bool | str | tuple[str, ...]]:
        return {
            RECORD_ATTRIBUTES[title]: value
            for title, value in self.record_values.get('主表', {}).items()
        }

    def count(self, _kind: str) -> int:
        return 0

    def exists(self, _kind: str) -> bool:
        return False

    def has_stat(self, _kind: str) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class StubMapEntityRecordReview:
    stats: StubMapEntityStats
    collected_issues: tuple[CollectedEntityReviewIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class StubMapEntityRecordSource:
    load_review: Callable[[], StubMapEntityRecordReview]
    variant_review_loaders: list[VariantReviewLoader] = field(default_factory=list)
    map_data: BinMap = field(
        default_factory=lambda: BinMap('Example/Map', BinElement('Map', {}, ()))
    )

    def eval_rules(
        self, *, variant_review_loader: VariantReviewLoader
    ) -> StubMapEntityRecordReview:
        self.variant_review_loaders.append(variant_review_loader)
        return self.load_review()


def write_map_with_icon(path: Path, icon: str) -> None:
    """Write the smallest binary map carrying one top-level ``meta.Icon`` value."""
    lookup = ('Map', 'meta', 'Icon', icon)
    indices = {value: i for i, value in enumerate(lookup)}

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


def test_browser_consolidates_startup_warnings_behind_a_summary_button() -> None:
    visible = make_installed_mod(
        source='zip',
        filename='Visible.zip',
        path='C:/Celeste/Mods/Visible.zip',
        metadata_name='Visible',
        metadata_version='1.0.0',
        maps=[ContentPath('Maps/Visible/Map.bin')],
    )
    unavailable = make_installed_mod(
        source='zip',
        filename='Unavailable.zip',
        path='C:/Celeste/Mods/Unavailable.zip',
        metadata_name='Unavailable',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Missing', version=Version.parse('1.0.0'))],
    )
    loaded = CatalogLoadResult.from_catalog(
        make_campaign_catalog(
            mods=[visible, unavailable],
        ),
        diagnostics=(
            "Mod 扫描 Broken.zip/Dialog/English.txt：无法解码文本 [type=string_type, input_value='1', input_type=str]",
        ),
    )
    app = browser.MapBrowserApp(loaded.catalog, diagnostics=loaded.diagnostics)

    async def check() -> None:
        async with app.run_test() as pilot:
            button = app.query_one('#browser-warnings-button', Button)
            assert str(button.label) == '消息（2）'

            await pilot.click(button)

            assert isinstance(app.screen, MessagesScreen)
            cards = list(app.screen.query('#loading-message-list MessageCard').results(MessageCard))
            assert len(cards) == 2
            assert 'Broken.zip/Dialog/English.txt' in cards[0].text
            assert 'Unavailable.zip' in cards[1].text
            app.notify('运行时错误', severity='error', timeout=0.01)
            await pilot.pause()
            assert str(button.label) == '消息（3）'
            await pilot.click('#--content-tab-runtime-messages')
            card = app.screen.query_one('#runtime-message-list MessageCard', MessageCard)
            await pilot.click(card.query_one('.message-clear', Button))
            assert str(button.label) == '消息（2）'

    asyncio.run(check())


def test_browser_displays_info_and_warning_path_notices(tmp_path: Path) -> None:
    mods_dir = tmp_path / 'Mods'
    mods_dir.mkdir()
    with ZipFile(mods_dir / 'Example.zip', 'w') as archive:
        archive.writestr('everest.yaml', '- Name: Example\n  Version: 1.0.0\n')
        archive.writestr('Maps/Example/A.bin', b'map')
        archive.writestr('Maps/./Ignored.bin', b'ignored')
        archive.writestr('Maps/Example//A.bin', b'ignored')
    loaded = ModScanner(tmp_path).scan().load_catalog(tmp_path)
    app = browser.MapBrowserApp(loaded.catalog, diagnostics=loaded.diagnostics)

    async def check() -> None:
        async with app.run_test() as pilot:
            assert str(app.query_one('#browser-warnings-button', Button).label) == '消息（2）'
            await pilot.press('f2')
            await pilot.pause()
            texts = [card.text for card in app.screen.query(MessageCard)]
            assert any(text.startswith('[info]') and 'Maps/./Ignored.bin' in text for text in texts)
            assert any(
                text.startswith('[warning]') and 'Maps/Example//A.bin' in text for text in texts
            )

    asyncio.run(check())


@pytest.mark.parametrize('action', ('preview', 'edit_route', 'record'))
def test_browser_retains_warning_when_accessing_uninstalled_mod(
    tmp_path: Path, action: str
) -> None:
    mod_path = tmp_path / 'Removed.zip'
    map_info = ContentPath('Maps/Removed/Map.bin')
    mod = make_installed_mod(
        source='zip',
        filename=mod_path.name,
        path=mod_path,
        metadata_name='Removed',
        metadata_version=None,
        maps=[map_info],
    )
    campaign_catalog = make_campaign_catalog(mods=[mod], game_dir=tmp_path)
    app = browser.MapBrowserApp(
        campaign_catalog,
        data_stores=DataStores(tmp_path / '.pist/local-data.sqlite3'),
        save_slot=make_save_progress(0, {}),
    )

    async def check() -> None:
        async with app.run_test(size=(160, 50)) as pilot:
            item = app.query_one(browser.MapItem)
            item.scroll_visible(immediate=True)
            await pilot.pause()
            if action == 'record':
                await pilot.click(item, offset=(3, 1), times=2)
            elif action == 'preview':
                app.query_one(browser.MapList).focus()
                await pilot.press('p')
            else:
                await pilot.click(item, offset=(3, 1), button=3)
                await pilot.pause()
                assert await pilot.click('#map-action-edit-route')
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.diagnostic_count == 1
            await app.action_show_messages()
            await pilot.pause()
            card = app.screen.query_one('#runtime-message-list MessageCard', MessageCard)
            assert mod_path.name in card.text

    asyncio.run(check())


def test_ref_summary_normalizes_all_whitespace() -> None:
    field = RecordRefSummary('简介', '  第一段\n\t第二段   第三段\r\n')
    assert field.content == '[b]简介[/b]\n第一段 第二段 第三段'


@pytest.mark.parametrize(
    ('path', 'use_dialog'),
    [
        ('Maps/Expert/Example.bin', True),
        ('Maps/0-Lobbies/Prologue.bin', False),
        ('Maps/Beginner Gym/Example.bin', False),
    ],
)
def test_dialog_author_path_heuristic_excludes_lobbies_and_gyms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, use_dialog: bool
) -> None:
    map_file = ContentPath(path)
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=tmp_path / 'Example',
        metadata_name='Example',
        metadata_version='1.0.0',
        collab_id='ExampleCollab',
        maps=[map_file],
    )
    level, side = _map_ref(mod, map_file)
    mod.dialogs['en'] = CaseFoldDict({f'{level.dialog_key}_author': 'Dialog Author'})
    submission = GameBananaSubmission.model_validate(
        {
            'name': 'Example',
            'submitter': 'Submitter',
            'latestUpdateAddedTime': '2026-09-04T12:00:00Z',
        }
    )

    class Client(GameBananaClient):
        async def lookup(self, metadata_name: str) -> GameBananaSubmission:
            assert metadata_name == 'Example'
            return submission

    monkeypatch.setattr(
        MapEntityRecordSource,
        'from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod], game_dir=tmp_path),
        save_slot=make_save_progress(0, {(level.sid, side): MapStats(Duration.from_seconds(1), 1)}),
        gamebanana_client=Client(),
    )

    async def check() -> None:
        async with app.run_test(size=(160, 55)) as pilot:
            await pilot.double_click(app.query_one(browser.MapItem), offset=(3, 0))
            assert isinstance(app.screen, browser.RecordEditorScreen)
            await pilot.click(app.screen.query_one(browser.RecordAuthorField))
            expected = (
                browser.DialogAuthorSelectionScreen if use_dialog else browser.AuthorSelectionScreen
            )
            assert isinstance(app.screen, expected)

    asyncio.run(check())


def test_record_ref_and_collab_tags_are_shown_in_their_own_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
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
            'description': '<p>适合初学者。<br>有进阶&nbsp;路线。</p>',
        }
    )

    class Client(GameBananaClient):
        async def lookup(self, metadata_name: str) -> GameBananaSubmission:
            assert metadata_name == 'Example'
            return gamebanana

    monkeypatch.setattr(
        MapEntityRecordSource,
        'from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[collab_mod], game_dir=tmp_path),
        gamebanana_client=Client(),
        save_slot=make_save_progress(
            0, {('Example/Map', 0): MapStats(Duration.from_seconds(1), 1)}
        ),
    )

    async def check() -> None:
        async with app.run_test(size=(160, 55)) as pilot:
            await pilot.double_click(app.query_one(browser.MapItem), offset=(3, 0))
            assert isinstance(app.screen, browser.RecordEditorScreen)
            assert app.screen.query_one('#record-ref-summary', RecordRefSummary).content == (
                '[b]简介[/b]\n适合初学者。 有进阶 路线。'
            )
            assert any(widget.content == 'Beginner' for widget in app.screen.query(Static))
            await pilot.double_click('#record-ref-summary')
            assert isinstance(app.screen, browser.RecordRefScreen)
            assert app.screen.query_one('#record-ref-content Static', Static).content == (
                '适合初学者。\n有进阶 路线。'
            )

    asyncio.run(check())


def test_record_ref_opens_full_text_on_double_click() -> None:
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
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[]))

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(browser.RecordEditorScreen(record, reference=('简介', '完整介绍')))
            await pilot.pause()
            await pilot.double_click('#record-ref-summary')
            assert isinstance(app.screen, browser.RecordRefScreen)

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
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[]))

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(browser.RecordEditorScreen(record, author_source=submission))
            await pilot.pause()
            await pilot.click(app.screen.query_one(browser.RecordAuthorField))
            assert isinstance(app.screen, browser.AuthorSelectionScreen)
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
    campaign_catalog = make_campaign_catalog(mods=[])
    opened: list[None] = []

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test() as pilot:
            app.push_screen(
                browser.RecordEditorScreen(record, edit_route=lambda: opened.append(None))
            )
            await pilot.pause()
            route_field = app.screen.query_one(browser.RecordRouteField)
            assert route_field.render() == '单击编辑路线'
            route_field.scroll_visible(animate=False, force=True)
            await pilot.pause()
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
            'rated_difficulty': '专家',
            'started_at': datetime.fromisoformat('2026-09-01').replace(tzinfo=UTC).date(),
            'rating': 8,
            'notes': '好图',
        }
    )
    manual_fields = (
        ManualRecordField('标注难度', 17, ('高级', '专家')),
        ManualRecordField('起始日期', 4),
        ManualRecordField('评分', 2),
        ManualRecordField('备注', 1),
    )
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[]))

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(browser.RecordEditorScreen(record, manual_fields=manual_fields))
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            ids = [
                f'#record-manual-{RECORD_FIELD_ORDER.index(field.title)}' for field in manual_fields
            ]
            assert app.screen.query_one(ids[0], Select).value == '专家'
            assert app.screen.query_one(ids[1], Input).value == '2026-09-01'
            assert app.screen.query_one(ids[2], Input).value == '8'
            assert app.screen.query_one(ids[3], Input).value == '好图'

    asyncio.run(check())


def test_campaign_list_contains_one_item_per_campaign() -> None:
    campaign_catalog = make_campaign_catalog(mods=[])

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test():
            assert list(app.query_one(browser.CampaignList).query(browser.CampaignItem)) == []

    asyncio.run(check())


def test_campaign_list_merges_maps_from_multiple_mod_sources() -> None:
    first_map = ContentPath('Maps/Pack/First.bin')
    second_map = ContentPath('Maps/Pack/Second.bin')
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
    campaign_catalog = make_campaign_catalog(mods=[first, second])

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test():
            assert len(app.query_one(browser.CampaignList).query(browser.CampaignItem)) == 1
            active_maps = [item.map for item in app.query(browser.MapItem)]
            assert all(isinstance(active_map, ModMap) for active_map in active_maps)
            assert [
                active_map.mod for active_map in active_maps if isinstance(active_map, ModMap)
            ] == [
                first,
                second,
            ]

    asyncio.run(check())


def test_browser_groups_helper_hidden_campaigns_in_a_collapsible() -> None:
    helper_map = ContentPath('Maps/Helper/Test.bin')
    visible_map = ContentPath('Maps/Visible/Map.bin')
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
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[helper, visible, hider]))

    async def check() -> None:
        async with app.run_test() as pilot:
            campaign_list = app.query_one(browser.CampaignList)
            assert [
                item.campaign.directory.as_posix()
                for item in campaign_list.query(browser.CampaignItem)
            ] == ['Maps/Visible']
            buttons = {button.direction: button for button in app.query(CampaignListToggle)}
            assert buttons[-1].has_class('-hidden')
            assert not buttons[1].has_class('-hidden')
            hidden_button = buttons[1]
            await pilot.click(hidden_button)
            for _ in range(10):
                await pilot.pause()
                if (
                    [
                        item.campaign.directory.as_posix()
                        for item in campaign_list.query(browser.CampaignItem)
                    ]
                    == ['Maps/Helper']
                    and _map_infos(app.query_one(browser.MapList).maps) == (helper_map,)
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
                if [
                    item.campaign.directory.as_posix()
                    for item in campaign_list.query(browser.CampaignItem)
                ] == ['Maps/Visible'] and _map_infos(app.query_one(browser.MapList).maps) == (
                    visible_map,
                ):
                    break
            else:
                pytest.fail('返回地图集后未显示对应地图')

    asyncio.run(check())


def test_browser_keeps_an_only_hidden_campaign_browsable() -> None:
    helper_map = ContentPath('Maps/Helper/Test.bin')
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
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[helper, hider]))

    async def check() -> None:
        async with app.run_test():
            assert [
                item.campaign.directory.as_posix() for item in app.query(browser.CampaignItem)
            ] == ['Maps/Helper']
            assert _map_infos(app.query_one(browser.MapList).maps) == (helper_map,)

    asyncio.run(check())


def test_browser_renders_hidden_collab_group_as_its_direct_map_list() -> None:
    lobby = ContentPath('Maps/Example/0-Lobbies/1-Maps.bin')
    visible_map = ContentPath('Maps/Example/1-Maps/Visible.bin')
    hidden_map = ContentPath('Maps/Example/1-Submissions/Hidden.bin')
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
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[collab, collab_utils]))

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(
                next(button for button in app.query(CampaignListToggle) if button.direction > 0)
            )
            for _ in range(20):
                await pilot.pause(0.05)
                if any(item.highlighted for item in app.query(browser.CampaignItem)):
                    break
            else:
                pytest.fail('未切换到隐藏的 Collab 地图组')
            hidden_item = next(
                item
                for item in app.query_one(browser.CampaignList).query(browser.CampaignItem)
                if item.campaign.directory.as_posix() == 'Maps/Example/1-Submissions'
            )
            await pilot.click(hidden_item)
            for _ in range(20):
                await pilot.pause(0.05)
                if hidden_item.highlighted:
                    break
            else:
                pytest.fail('未选中指定的隐藏 Collab 地图组')
            collab_list = app.query_one(browser.CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            else:
                pytest.fail('隐藏的 Collab 地图组未完成日志图标读取')
            assert _map_infos(collab_list.query_one(browser.MapList).maps) == (hidden_map,)

    asyncio.run(check())


def test_browser_selects_a_visible_campaign_before_hidden_campaigns() -> None:
    helper_map = ContentPath('Maps/Helper/Test.bin')
    visible_map = ContentPath('Maps/Visible/Map.bin')
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

    app = browser.MapBrowserApp(make_campaign_catalog(mods=[helper, visible, hider]))

    async def check() -> None:
        async with app.run_test():
            assert _map_infos(app.query_one(browser.MapList).maps) == (visible_map,)

    asyncio.run(check())


def test_browser_separates_vanilla_and_ungrouped_mod_maps(tmp_path: Path) -> None:
    content_maps = tmp_path / 'Content' / 'Maps'
    content_maps.mkdir(parents=True)
    (content_maps / '0-Intro.bin').touch()
    loose_map = ContentPath('Maps/Loose.bin')
    loose_mod = make_installed_mod(
        source='zip',
        filename='Loose.zip',
        path='C:/Celeste/Mods/Loose.zip',
        metadata_name='Loose',
        metadata_version='1.0.0',
        maps=[loose_map],
    )
    campaign_catalog = make_campaign_catalog(mods=[loose_mod], game_dir=tmp_path)

    app = browser.MapBrowserApp(campaign_catalog)

    async def check() -> None:
        async with app.run_test():
            items = list(app.query(browser.CampaignItem))
            assert [map_names.campaign_fallback(item.campaign) for item in items] == [
                '官图',
                '未分类',
            ]
            assert [
                map_names.source_name(map_file) for map_file in items[0].campaign.iter_maps()
            ] == ['原版']

    asyncio.run(check())


def test_collab_campaign_expands_lobby_from_journal_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prologue = ContentPath('Maps/Example/0-Lobbies/0-Prologue.bin')
    lobby = ContentPath('Maps/Example/0-Lobbies/1-Easy.bin')
    gym = ContentPath('Maps/Example/0-Gyms/1-Easy.bin')
    easy_map = ContentPath('Maps/Example/1-Easy/Map.bin')
    hard_map = ContentPath('Maps/Example/2-Hard/Map.bin')
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
    campaign_catalog = make_campaign_catalog(mods=[mod, collab_utils])
    sid = local_sid_for_map_file(lobby)
    stats = {(sid, 0): MapStats(Duration.from_seconds(1), 1)}
    slots = (make_save_progress(0, stats), make_save_progress(1, stats))
    app = browser.MapBrowserApp(
        campaign_catalog,
        data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
        collab_lobby_overrides=lobby_store.load(),
        collab_lobby_store=lobby_store,
        save_slot=slots[0],
        save_reader=MemorySaveReader(*slots),
    )
    monkeypatch.setattr(
        'pist.catalog_cache.catalog_fingerprint.journal_fingerprint', lambda _: 'first'
    )
    monkeypatch.setattr(
        'pist.catalog_cache.JournalRefs.from_map',
        lambda _: game_collab.JournalRefs(('Example/1-Easy',)),
    )

    record_previewed: list[MapRecord] = []

    async def edit_record(
        _self: RecordEditorController, record: MapRecord, _factory: EditorFactory
    ) -> None:
        record_previewed.append(record)

    monkeypatch.setattr(RecordEditorController, 'edit', edit_record)
    monkeypatch.setattr(
        MapEntityRecordSource,
        'from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        async with app.run_test(size=(160, 60)) as pilot:
            lobby_item = app.query_one(LobbyMapItem)
            assert _map_infos(
                next(
                    campaign
                    for campaign in campaign_catalog.hidden_campaigns
                    if campaign.directory.as_posix() == 'Maps/Example/2-Hard'
                ).iter_sides()
            ) == (hard_map,)
            assert _map_infos(
                next(
                    campaign
                    for campaign in campaign_catalog.hidden_campaigns
                    if campaign.directory.as_posix() == 'Maps/Example/0-Gyms'
                ).iter_sides()
            ) == (gym,)
            assert record_previewed == []
            map_lists = list(app.query(browser.MapList))
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
                if child_lists := list(lobby_item.query(browser.MapList)):
                    break
            else:
                pytest.fail('Lobby submission map list did not finish loading.')
            await pilot.press(']')
            await _wait_for_save_switch(app)
            assert list(lobby_item.query(browser.MapList)) == child_lists
            child_lists = list(lobby_item.query(browser.MapList))
            assert all(
                not map_list.has_class('is-loading')
                for map_list in app.query(browser.CollabMapList)
            )
            assert [map_list.maps for map_list in app.query(browser.CollabMapList)] == [
                tuple(
                    next(
                        campaign
                        for campaign in campaign_catalog.hidden_campaigns
                        if campaign.directory.as_posix() == 'Maps/Example/1-Easy'
                    ).iter_sides()
                ),
            ]
            map_lists[0].index = 0
            await pilot.click(child_lists[0].query_one(browser.MapItem), offset=(3, 0))
            assert isinstance(app.focused, browser.MapList)
            assert app.focused is not map_lists[0]
            assert map_lists[0].index == 0
            await pilot.double_click(lobby_item.query_one('.map-item-title'), offset=(3, 0))
            await pilot.pause()
            assert [(record.sid, record.side) for record in record_previewed] == [
                (sid, LevelSide.A)
            ]
            menu_x, menu_y = lobby_item.region.offset + (10, 2)
            await pilot.click(lobby_item, offset=(10, 2), button=3)
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
            assert [(record.sid, record.side) for record in record_previewed] == [
                (sid, LevelSide.A)
            ]
            assert lobby_item.collapsed
            await pilot.click(lobby_item.query_one('.map-item-title'), offset=(3, 0), button=3)
            await pilot.pause()
            await pilot.click('#map-action-edit-lobby')
            await pilot.pause()
            selection = app.screen.query_one(SelectionList)
            assert not list(app.screen.query('#lobby-campaign-save-shared'))
            assert not list(app.screen.query('#lobby-campaign-remove-shared'))
            assert [selection.get_option_at_index(i).value for i in range(selection.option_count)][
                :3
            ] == ['Example/0-Gyms', 'Example/1-Easy', 'Example/2-Hard']
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
                    for loaded_list in app.query(browser.CollabMapList)
                    for loaded_map in loaded_list.maps
                ),
            )
            assert gym not in _map_infos(visible_maps)

    asyncio.run(check())


def test_collab_lobby_override_replaces_journal_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lobby = ContentPath('Maps/Example/0-Lobbies/1-Easy.bin')
    automatic_map = ContentPath('Maps/Example/1-Easy/Map.bin')
    configured_map = ContentPath('Maps/Example/2-Hard/Map.bin')
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
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod, collab_utils], game_dir=tmp_path),
        data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
        collab_lobby_overrides=overrides,
    )

    def unexpected_journal_lookup(_loaded_map: Map) -> game_collab.JournalRefs:
        pytest.fail('Configured lobby side should not inspect JournalTrigger values.')

    monkeypatch.setattr('pist.catalog_cache.JournalRefs.from_map', unexpected_journal_lookup)

    async def check() -> None:
        async with app.run_test() as pilot:
            lobby_item = app.query_one(LobbyMapItem)
            await pilot.click(lobby_item, offset=(0, 0))
            for _ in range(20):
                await pilot.pause(0.05)
                if child_lists := list(lobby_item.query(browser.MapList)):
                    break
            else:
                pytest.fail('Configured Lobby map list did not finish loading.')
            assert _map_infos(child_lists[0].maps) == (configured_map,)

    asyncio.run(check())


def test_collab_lobby_orders_journal_maps_from_multiple_source_mods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lobby = ContentPath('Maps/Example/0-Lobbies/1-Easy.bin')
    base_map = ContentPath('Maps/Example/1-Easy/Base.bin')
    addon_map = ContentPath('Maps/Example/1-Easy/Addon.bin')
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
    campaign_catalog = make_campaign_catalog(
        mods=[base_mod, addon_mod, collab_utils], game_dir=tmp_path
    )
    local_data = DataStores(tmp_path / 'local-data.sqlite3')
    monkeypatch.setattr(
        'pist.catalog_cache.JournalRefs.from_map',
        lambda _loaded_map: game_collab.JournalRefs(('Example/1-Easy',)),
    )
    monkeypatch.setattr(
        'pist.catalog_cache.catalog_fingerprint.journal_fingerprint',
        lambda _loaded_map: 'first',
    )
    icon_calls: list[tuple[str, tuple[str, ...]]] = []

    def fingerprint(mod: InstalledMod, maps: tuple[ContentPath, ...]) -> str:
        return f'{mod.path}:{",".join(map_info.as_posix() for map_info in maps)}'

    def iter_icons(
        mod: InstalledMod,
        maps: tuple[ContentPath, ...],
        *,
        on_warning: Callable[[ContentPath, MapStringDecodeWarning], None] | None = None,
    ) -> Iterable[tuple[ContentPath, str]]:
        map_files = tuple(map_info for map_info in maps)
        if on_warning is not None:
            for map_info in maps:
                on_warning(map_info, MapStringDecodeWarning(13, b'\xff'))
        icon_calls.append((str(mod.path), tuple(path.as_posix() for path in map_files)))
        for map_file in map_files:
            order = '1-addon' if map_file.name == 'Addon.bin' else '2-base'
            yield map_file, f'areas/Example/meters/{order}'

    monkeypatch.setattr(
        'pist.ui.maps.browser.collab_order.catalog_fingerprint.collab_journal_icon_fingerprint',
        fingerprint,
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.collab_order.collab.iter_collab_journal_map_icons',
        iter_icons,
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, data_stores=local_data)
        async with app.run_test(size=(100, 40)) as pilot:
            lobby_item = app.query_one(LobbyMapItem)
            await pilot.click(lobby_item, offset=(0, 0))
            for _ in range(20):
                await pilot.pause(0.05)
                child_lists = list(lobby_item.query(browser.MapList))
                if child_lists:
                    break
            else:
                pytest.fail('Lobby journal maps from multiple Mods did not finish loading.')
            assert [item.file_path for item in child_lists[0].query(browser.MapItem)] == [
                addon_map,
                base_map,
            ]
            assert {path for path, _map_files in icon_calls} == {
                str(base_mod.path),
                str(addon_mod.path),
            }
            assert app.diagnostic_count == 2
            await app.action_show_messages()
            await pilot.pause()
            messages = '\n'.join(
                str(card.query_one(Static).content) for card in app.screen.query(MessageCard)
            )
            assert 'Base.bin' in messages and 'Addon.bin' in messages

    asyncio.run(check())


def test_collab_lobby_sides_resolve_their_own_journal_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lobby_a = ContentPath('Maps/Example/0-Lobbies/1-Easy.bin')
    lobby_b = ContentPath('Maps/Example/0-Lobbies/1-Easy-B.bin')
    easy_map = ContentPath('Maps/Example/1-Easy/Map.bin')
    hard_map = ContentPath('Maps/Example/2-Hard/Map.bin')
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

    def journal_refs(loaded_map: Map) -> game_collab.JournalRefs:
        map_file = str(loaded_map.file_path)
        calls.append(map_file)
        levelset = 'Example/2-Hard' if map_file.endswith('-B.bin') else 'Example/1-Easy'
        return game_collab.JournalRefs((levelset,))

    monkeypatch.setattr('pist.catalog_cache.JournalRefs.from_map', journal_refs)
    monkeypatch.setattr(
        'pist.catalog_cache.catalog_fingerprint.journal_fingerprint',
        lambda loaded_map: loaded_map.file_path.as_posix(),
    )
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[mod, collab_utils]),
        data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            lobby_item = app.query_one(SideLobbyMapItem)
            await pilot.click(lobby_item, offset=(0, 0))

            for _ in range(20):
                await pilot.pause(0.05)
                child_lists = list(lobby_item.query(browser.MapList))
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
                child_lists = list(lobby_item.query(browser.MapList))
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
                child_lists = list(lobby_item.query(browser.MapList))
                if child_lists and _map_infos(child_lists[0].maps) == (easy_map,):
                    break
            else:
                pytest.fail('切回 A 面后未恢复已解析的日志地图。')

            assert calls == [str(lobby_a), str(lobby_b)]

    asyncio.run(check())


def test_campaign_detail_exposes_only_overrides_of_its_active_maps() -> None:
    first = make_installed_mod(
        source='zip',
        filename='First.zip',
        path='C:/Celeste/Mods/First.zip',
        metadata_name='First',
        metadata_version='1.0.0',
        maps=[ContentPath('Maps/Pack/Map.bin')],
    )
    second = make_installed_mod(
        source='zip',
        filename='Second.zip',
        path='C:/Celeste/Mods/Second.zip',
        metadata_name='Second',
        metadata_version='1.0.0',
        maps=[ContentPath('Maps/Pack/Map.bin')],
    )
    app = browser.MapBrowserApp(make_campaign_catalog(mods=[first, second]))

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query('.map-override-warning')) == 1

    asyncio.run(check())


def test_detail_pane_scrolls_with_shortcut() -> None:
    maps = [ContentPath(f'Maps/Test/{i}.bin') for i in range(100)]
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=maps,
            )
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test(size=(80, 24)) as pilot:
            detail_scroll = app.query_one(f'#{browser.DETAIL_SCROLL_ID}', VerticalScroll)
            await pilot.press('j')
            assert detail_scroll.scroll_y > 0

    asyncio.run(check())


def test_save_slot_shortcuts_redraw_selected_slot(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for number in (0, 2):
        MainSave().write(saves_dir / f'{number}.celeste')
    reader = SaveProgressReader.from_game_dir(tmp_path / 'Celeste')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[ContentPath('Maps/Test/Map.bin')],
            )
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, save_reader=reader, save_slot=reader.load(0))
        async with app.run_test() as pilot:
            map_list = app.query_one(browser.MapList)
            await pilot.press(']')
            await _wait_for_save_switch(app)
            assert app.sub_title == '存档 2'
            assert app.query_one(browser.MapList) is map_list
            await pilot.press('[')
            await _wait_for_save_switch(app)
            assert app.sub_title == '存档 0'

    asyncio.run(check())


def test_save_slot_switch_keeps_selected_campaign_list(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    for number in (0, 1):
        MainSave().write(saves_dir / f'{number}.celeste')
    reader = SaveProgressReader.from_game_dir(tmp_path / 'Celeste')
    map_info = ContentPath('Maps/Test/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, save_reader=reader, save_slot=reader.load(0))
        async with app.run_test() as pilot:
            map_list = app.query_one(browser.MapList)
            await pilot.press(']')
            await _wait_for_save_switch(app)
            assert app.sub_title == '存档 1'
            assert app.query_one(browser.MapList) is map_list

    asyncio.run(check())


def test_save_slot_burst_shows_target_and_loading_without_old_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saves_dir = tmp_path / 'Saves'
    saves_dir.mkdir()
    for number in range(4):
        (saves_dir / f'{number}.celeste').write_text(
            normal_save_xml(
                '<SaveData><LevelSets><LevelSetStats Name="Test"><Areas>'
                '<AreaStats SID="Test/Map"><Modes>'
                f'<AreaModeStats TimePlayed="10030000" Deaths="{number}" />'
                '<AreaModeStats TimePlayed="0" Deaths="0"/>'
                '<AreaModeStats TimePlayed="0" Deaths="0"/>'
                '</Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>'
            ),
            encoding='utf-8',
        )
    reader = SaveProgressReader(saves_dir)
    initial = reader.load(0)
    load = reader.load
    reads: list[int] = []
    started, release = Event(), Event()

    def read(number: int) -> SaveProgress:
        reads.append(number)
        if number == 1:
            started.set()
            if not release.wait(3):
                raise AssertionError('Read was not released.')
        return load(number)

    monkeypatch.setattr(reader, 'load', read)
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[ContentPath('Maps/Test/Map.bin'), ContentPath('Maps/Test/Map-B.bin')],
            )
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            save_reader=reader,
            save_slot=initial,
            data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
        )
        async with app.run_test() as pilot:
            item = app.query_one(SideMapItem)
            await pilot.press(']')
            assert await asyncio.to_thread(started.wait, 2)
            await pilot.press(']', ']')
            assert app.sub_title == '存档 3 · 加载中…'
            assert not item.query_one('.map-item-save-stats').display
            assert item.query_one('.map-item-save-loading').display
            assert '存档 3：加载中…' in str(
                item.query_one('.map-item-save-loading', Static).render()
            )
            assert item.query_one('.map-item-content').display
            assert await pilot.click(app.query_one(browser.CampaignItem))
            await pilot.pause(0.01)
            item = app.query_one(SideMapItem)
            assert '存档 3：加载中…' in str(
                item.query_one('.map-item-save-loading', Static).render()
            )
            assert await pilot.click(list(item.query(MapSideButton))[1])
            assert not item.query_one('.map-item-save-stats').display
            await pilot.double_click(item, offset=(5, 1))
            assert not isinstance(app.screen, browser.RecordEditorScreen)
            release.set()
            await _wait_for_save_switch(app)
            assert reads == [1, 3]
            assert app.sub_title == '存档 3'
            assert item.query_one('.map-item-save-stats').display
            assert not item.query_one('.map-item-save-loading').display
            assert await pilot.click(item.query_one(MapSideButton))
            assert '死亡 3' in str(item.query_one('.map-item-save-stats', Static).render())

    asyncio.run(check())


def test_ordinary_campaign_maps_keep_path_order_when_switching_save_slots() -> None:
    first = ContentPath('Maps/Test/First.bin')
    second = ContentPath('Maps/Test/Second.bin')
    third = ContentPath('Maps/Test/Third.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[third, first, second],
            )
        ]
    )
    first_slot = make_save_progress(
        0,
        {
            ('Test/First', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1),
            ('Test/Second', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            ),
        },
    )
    second_slot = make_save_progress(
        1,
        {
            ('Test/Second', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1),
            ('Test/Third', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            ),
        },
    )
    third_slot = make_save_progress(
        2, {('Test/Third', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1)}
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            save_reader=MemorySaveReader(first_slot, second_slot, third_slot),
            save_slot=first_slot,
        )
        async with app.run_test():
            map_list = app.query_one(browser.MapList)
            assert [item.file_path for item in map_list.query(browser.MapItem)] == [
                first,
                second,
                third,
            ]
            await app.action_next_save_slot()
            await _wait_for_save_switch(app)
            assert [item.file_path for item in map_list.query(browser.MapItem)] == [
                first,
                second,
                third,
            ]
            await app.action_next_save_slot()
            await _wait_for_save_switch(app)
            assert [item.file_path for item in map_list.query(browser.MapItem)] == [
                first,
                second,
                third,
            ]

    asyncio.run(check())


def test_save_slot_switch_restores_highlight_when_selected_map_keeps_its_index() -> None:
    first = ContentPath('Maps/Test/First.bin')
    second = ContentPath('Maps/Test/Second.bin')
    third = ContentPath('Maps/Test/Third.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[first, second, third],
            )
        ]
    )
    first_slot = make_save_progress(
        5,
        {
            ('Test/First', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            ),
            ('Test/Second', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1),
        },
    )
    second_slot = make_save_progress(
        6,
        {
            ('Test/First', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            ),
            ('Test/Third', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1),
        },
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            save_reader=MemorySaveReader(first_slot, second_slot),
            save_slot=first_slot,
        )
        async with app.run_test() as pilot:
            map_list = app.query_one(browser.MapList)
            first_item = map_list.query_one(browser.MapItem)
            assert map_list.highlighted_child is first_item
            assert first_item.highlighted

            await app.action_next_save_slot()
            await _wait_for_save_switch(app)

            selected_item = map_list.query_one(browser.MapItem)
            assert selected_item.file_path is first
            assert map_list.highlighted_child is selected_item
            assert selected_item.highlighted
            await pilot.click(selected_item)
            assert selected_item.highlighted

    asyncio.run(check())


def test_save_slot_switch_preserves_selected_b_side_and_its_save_slot() -> None:
    a_side = ContentPath('Maps/Test/Map.bin')
    b_side = ContentPath('Maps/Test/Map-B.bin')
    other = ContentPath('Maps/Test/Other.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=[a_side, b_side, other],
            )
        ]
    )
    first_slot = make_save_progress(
        0,
        {
            ('Test/Other', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            )
        },
    )
    second_slot = make_save_progress(
        1,
        {('Test/Map', 1): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True)},
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            save_reader=MemorySaveReader(first_slot, second_slot),
            save_slot=first_slot,
        )
        async with app.run_test():
            map_list = app.query_one(browser.MapList)
            side_item = next(item for item in map_list.map_items if isinstance(item, SideMapItem))
            map_list.index = map_list.map_items.index(side_item)
            side_item.switch_side(MapSideButton.Clicked(1))
            assert side_item.map.file_path is b_side

            await app.action_next_save_slot()
            await _wait_for_save_switch(app)

            selected = map_list.highlighted_child
            assert isinstance(selected, SideMapItem)
            assert selected.map.file_path is b_side
            content = str(selected.query_one('.map-item-save-stats', Static).render())
            assert '00:01' in content

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
        ModMap(
            ContentPath(f'Maps/Test/Map{("-" + side) if side else ""}.bin'),
            mod,
        )
        for side in sides
    )
    maps = tuple(
        (level, side) for level in assemble_mod_levels(loaded_maps) for side in level.sides
    )

    app = App[None](css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')

    async def check() -> None:
        async with app.run_test():
            rows = browser.MapList(maps, 'en', {}, None, map_names.source_name)
            await app.screen.mount(rows)
            assert [(item.level, item.side) for item in rows.map_items] == list(maps)
            assert not rows.query(SideMapItem)

    asyncio.run(check())


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
            ModMap(ContentPath('Maps/Test/Map.bin'), mod),
            ModMap(ContentPath('Maps/Test/Map-B.bin'), mod),
        )
    )
    level = levels[0]

    app = App[None](css_path=Path(__file__).parents[1] / 'src/pist/ui/styles/maps_browser.tcss')

    async def check() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            rows = browser.MapList(
                ((level, side) for side in level.sides), 'en', {}, None, map_names.source_name
            )
            await app.screen.mount(rows)
            await pilot.pause()
            assert len(rows.map_items) == 1
            item = rows.query_one(SideMapItem)
            assert item.level is level and item.side is LevelSide.A
            assert await pilot.click(list(item.query(MapSideButton))[1])
            assert item.side is LevelSide.B

    asyncio.run(check())


@pytest.mark.parametrize('invalid_session', (False, True))
def test_map_selection_requires_right_click_to_write_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_session: bool
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
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
        ]
    )
    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')

    save_slot = make_save_progress(
        0, {('Example/Map', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1)}
    )
    if invalid_session:
        (tmp_path / '0.celeste').write_text(
            normal_save_xml(
                '<SaveData><LevelSets><LevelSetStats Name="Example"><Areas>'
                '<AreaStats SID="Example/Map"><Modes>'
                '<AreaModeStats TimePlayed="10030000" Deaths="1" />'
                '<AreaModeStats TimePlayed="0" Deaths="0"/>'
                '<AreaModeStats TimePlayed="0" Deaths="0"/>'
                '</Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>'
            ),
            encoding='utf-8',
        )
        (tmp_path / '0-modsave-CollabUtils2.celeste').write_text(
            'SessionsPerLevel:\n  Example/Map: <broken\n', encoding='utf-8'
        )
        save_slot = SaveProgressReader(tmp_path).load(0)
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({'主表': {'红草莓数': 2}}))
        ),
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, data_stores=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            map_list = app.query_one(browser.MapList)
            assert app.diagnostic_count == 0
            map_list.focus()
            await pilot.press('enter')
            with pytest.raises(ValueError, match='No saved local record'):
                local_data.records.load(1)
            await pilot.double_click(map_list.query_one(browser.MapItem))
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            assert any(
                str(control.content) == MapRecordProgress.RAN_BEFORE.label
                for control in app.screen.query(Static)
            )
            if invalid_session:
                assert app.diagnostic_count == 1
                await app.action_show_messages()
                assert isinstance(app.screen, MessagesScreen)
                text = '\n'.join(card.text for card in app.screen.query(MessageCard))
                assert 'Example/Map' in text and 'CollabUtils2' in text
                await pilot.click('#messages-close')
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.records.load(1)
    assert record.map_name == 'Example Map'
    assert record.mod_metadata_name == 'Example'
    assert record.model_dump(include={'n_strawberries'}) == {'n_strawberries': 2}


@pytest.mark.parametrize(
    ('issue', 'blocks_record'),
    (
        (
            CollectedEntityRuleIssue(
                MapEntityID('room', 3),
                CollectedEntityRuleIssueStatus.UNMATCHED,
                'Example/UnknownBerry',
            ),
            True,
        ),
        (
            CollectedEntityRuleIssue(
                MapEntityID('room', 3),
                CollectedEntityRuleIssueStatus.EXCLUDED,
                'Example/UnknownBerry',
            ),
            True,
        ),
        (UnreviewedCollectedEntity(MapEntityID('room', 3), 'Example/UnknownBerry', {}), True),
        (
            CollectedEntityRuleIssue(
                MapEntityID('room', 3), CollectedEntityRuleIssueStatus.NOT_FOUND
            ),
            False,
        ),
    ),
)
def test_collected_entity_rule_review_controls_record_editing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue: CollectedEntityReviewIssue,
    blocks_record: bool,
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
    )
    source_load_count = 0
    review_count = 0
    sources: list[StubMapEntityRecordSource] = []

    def load_source(*_args: object, **_kwargs: object) -> StubMapEntityRecordSource:
        nonlocal source_load_count
        source_load_count += 1

        def load_review() -> StubMapEntityRecordReview:
            nonlocal review_count
            review_count += 1
            return StubMapEntityRecordReview(
                StubMapEntityStats({}),
                (issue,),
            )

        source = StubMapEntityRecordSource(load_review)
        sources.append(source)
        return source

    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map', load_source
    )
    save_slot = make_save_progress(
        0,
        {
            ('Example/Map', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000),
                1,
                collected_strawberries=frozenset({MapEntityID('room', 3)}),
            )
        },
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            data_stores=DataStores(tmp_path / '.pist/local-data.sqlite3'),
            save_slot=save_slot,
            entity_audit_store=EntityAuditStore(tmp_path / 'audit.sqlite3'),
        )
        async with app.run_test() as pilot:
            map_item = app.query_one(browser.MapItem)
            expected_screen = (
                browser.CollectedEntityRulesScreen if blocks_record else browser.RecordEditorScreen
            )

            async def wait_for_screen(expected_reviews: int) -> None:
                async with asyncio.timeout(5):
                    # push_screen makes the screen current before its children are mounted.
                    # A refresh also replaces it with another screen of the same class.
                    while not app.diagnostic_count and (
                        not isinstance(app.screen, expected_screen)
                        or not app.screen.is_mounted
                        or review_count < expected_reviews
                    ):
                        await asyncio.sleep(0.01)

            await pilot.double_click(map_item)
            await wait_for_screen(1)
            assert isinstance(app.screen, browser.CollectedEntityRulesScreen) is blocks_record
            if blocks_record:
                await pilot.click('#collected-entity-rules-refresh')
                await wait_for_screen(2)
                assert isinstance(app.screen, browser.CollectedEntityRulesScreen)
                assert source_load_count == 1
                assert review_count == 2
            else:
                assert isinstance(app.screen, browser.RecordEditorScreen)
                assert source_load_count == 1
                assert review_count == 1
            assert len(sources[0].variant_review_loaders) == review_count
            assert all(callable(loader) for loader in sources[0].variant_review_loaders)

    asyncio.run(check())


def test_refreshing_collected_entity_rules_resumes_record_editing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
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
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map', load_source
    )
    save_slot = make_save_progress(
        0,
        {
            ('Example/Map', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000),
                1,
                collected_strawberries=frozenset({MapEntityID('room', 3)}),
            )
        },
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            data_stores=DataStores(tmp_path / '.pist/local-data.sqlite3'),
            save_slot=save_slot,
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem))
            assert isinstance(app.screen, browser.CollectedEntityRulesScreen)
            await pilot.click('#collected-entity-rules-refresh')
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            assert source_load_count == 1
            assert review_count == 2

    asyncio.run(check())


def test_record_includes_saved_main_room_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
    )
    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')
    local_data.routes.save(
        MapRoute(map_file=map_info.as_posix(), rooms=('start', 'middle', 'goal'))
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({'主表': {'红草莓数': 2}}))
        ),
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            data_stores=local_data,
            save_slot=make_save_progress(
                0, {('Example/Map', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1)}
            ),
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem))
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.records.load(1)
    assert record.model_dump(include={'n_strawberries', 'n_main_rooms'}) == {
        'n_strawberries': 2,
        'n_main_rooms': 3,
    }


@pytest.mark.parametrize('trigger', ('shortcut', 'context_menu'))
def test_map_preview_starts_in_preview_mode_without_implicit_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
    )
    layout = MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184)))
    rules = load_default_entity_rules()
    entrances = MapEntranceRules()

    def load_layout(
        source: Map, *, entity_rules: EntityRules, entrance_rules: MapEntranceRules
    ) -> MapLayout:
        assert source.file_path == map_info
        assert entity_rules is rules
        assert entrance_rules is entrances
        return layout

    monkeypatch.setattr('pist.ui.maps.browser.app.load_entity_rule_layers', lambda: rules)
    monkeypatch.setattr('pist.ui.maps.browser.app.load_map_entrance_rule_layers', lambda: entrances)
    monkeypatch.setattr('pist.ui.maps.browser.app.map_layout.MapLayout.from_map', load_layout)
    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')
    started = asyncio.Event()
    finish = asyncio.Event()
    preview_options: list[dict[str, object]] = []

    class Preview:
        def __init__(self, *_: object, **options: object) -> None:
            preview_options.append(options)

        async def preview(self) -> None:
            started.set()
            await finish.wait()

    monkeypatch.setattr('pist.ui.maps.browser.app.MapPreview', Preview)

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, data_stores=local_data)
        async with app.run_test() as pilot:
            map_item = app.query_one(browser.MapItem)
            if trigger == 'shortcut':
                await pilot.click(map_item)
                await pilot.press('p')
            else:
                await pilot.click(map_item, button=3)
                await pilot.click('#map-action-preview')
            await asyncio.wait_for(started.wait(), timeout=10)
            previous_theme = app.theme
            await pilot.press('ctrl+p')
            await pilot.press('t', 'h', 'e', 'm', 'e', 'enter')
            await pilot.pause()
            await pilot.press('n', 'o', 'r', 'd', 'enter')
            await pilot.pause()
            assert app.theme != previous_theme
            finish.set()
            await pilot.pause()

    asyncio.run(check())
    assert local_data.routes.load(map_info.as_posix()) is None
    assert preview_options[0]['initial_mode'] is MapPreviewMode.PREVIEW
    assert preview_options[0]['classification_rules'] is rules
    assert preview_options[0]['entrance_rules'] is entrances
    assert 'read_only' not in preview_options[0]
    routable_maps = preview_options[0]['routable_maps']
    assert isinstance(routable_maps, dict)
    routable_level, routable_side = routable_maps['Example/Map']
    assert isinstance(routable_level[routable_side], Map)


def test_vanilla_route_editor_starts_in_review_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_maps = tmp_path / 'Content' / 'Maps'
    content_maps.mkdir(parents=True)
    (content_maps / '1-ForsakenCity.bin').touch()
    campaign_catalog = make_campaign_catalog(mods=[], game_dir=tmp_path)
    layout = MapLayout((MapRoom('start', 0, 0, 320, 184),))
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.map_layout.MapLayout.from_map', lambda *_, **kwargs: layout
    )
    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')
    started = asyncio.Event()
    preview_sources: list[object] = []

    class Preview:
        def __init__(self, source: object, *_: object, **options: object) -> None:
            preview_sources.append(source)
            assert options['initial_mode'] is MapPreviewMode.EDIT_ROUTE
            assert 'read_only' not in options

        async def preview(self) -> None:
            started.set()

    monkeypatch.setattr('pist.ui.maps.browser.app.MapPreview', Preview)

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, data_stores=local_data)
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(browser.MapItem), button=3)
            await pilot.click('#map-action-edit-route')
            await started.wait()
            await pilot.pause()

    asyncio.run(check())
    assert len(preview_sources) == 1
    assert isinstance(preview_sources[0], Map)
    assert local_data.routes.load('Maps/1-ForsakenCity.bin') is None


def test_map_selected_in_detail_edits_credit_authors_from_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
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

    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')
    save_slot = make_save_progress(
        0, {('Example/Map', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1)}
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            data_stores=local_data,
            save_slot=save_slot,
            gamebanana_client=StubGameBananaClient(),
        )
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem))
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            await pilot.click(app.screen.query_one(browser.RecordAuthorField))
            await pilot.pause()
            assert isinstance(app.screen, browser.AuthorSelectionScreen)
            await pilot.click('#record-author-0')
            await pilot.pause()
            await pilot.click('#author-select-save')
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.records.load(1)
    assert record.authors == ('Alice',)
    assert record.credits[1].group_name == 'Special Thanks'


def test_collab_map_edits_multiple_dialog_authors_from_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = ContentPath('Maps/Expert/Example.bin')
    campaign_catalog = make_campaign_catalog(
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
        ]
    )
    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')
    save_slot = make_save_progress(
        0, {('Expert/Example', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1)}
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, data_stores=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem))
            assert isinstance(app.screen, browser.RecordEditorScreen)
            await pilot.click(app.screen.query_one(browser.RecordAuthorField))
            assert isinstance(app.screen, browser.DialogAuthorSelectionScreen)
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
            assert isinstance(app.screen, browser.RecordEditorScreen)
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    record = local_data.records.load(1)
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
            'n_strawberries': 2,
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
    campaign_catalog = make_campaign_catalog(mods=[])
    saved: list[MapRecord] = []

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test() as pilot:
            app.push_screen(
                browser.RecordEditorScreen(record, manual_fields=manual_fields),
                lambda result: saved.append(result) if result is not None else None,
            )
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            ids = [
                f'#record-manual-{RECORD_FIELD_ORDER.index(field.title)}' for field in manual_fields
            ]
            app.screen.query_one(ids[0], Select).value = '高级'
            app.screen.query_one(ids[1], Select).value = '低'
            app.screen.query_one(ids[2], Select).value = '专家'
            app.screen.query_one(ids[3], Select).value = '高'
            await pilot.pause()
            app.screen.query_one(f'{ids[4]}-picker', Button).press()
            await pilot.pause()
            assert isinstance(app.screen, browser.DatePickerScreen)
            app.screen.query_one('#date-picker-day-1', Button).press()
            await pilot.pause()
            assert isinstance(app.screen, browser.RecordEditorScreen)
            app.screen.query_one(ids[4], Input).value = '2026-09-12'
            app.screen.query_one(ids[5], Select).value = '通关'
            app.screen.query_one(ids[6], Input).value = '8'
            app.screen.query_one(ids[7], Input).value = '好图'
            await pilot.click('#record-confirm-save')

    asyncio.run(check())
    assert saved == [
        record.model_copy(
            update={
                'n_strawberries': 2,
                'perceived_difficulty': '高级',
                'perceived_difficulty_tier': '低',
                'rated_difficulty': '专家',
                'rated_difficulty_tier': '高',
                'started_at': datetime.fromisoformat('2026-09-12').replace(tzinfo=UTC).date(),
                'status': '通关',
                'rating': 8,
                'notes': '好图',
            }
        )
    ]


def test_map_without_record_does_not_open_record_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = ContentPath('Maps/Example/Map.bin')
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Example.zip',
                path='C:/Celeste/Mods/Example.zip',
                metadata_name='Example',
                metadata_version='1.0.0',
                maps=[map_info],
            )
        ]
    )
    local_data = DataStores(tmp_path / '.pist/local-data.sqlite3')
    save_slot = make_save_progress(
        0, {('Example/Map', 0): MapStats(Duration.from_dotnet_ticks(0), 1)}
    )
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_entities.MapEntityRecordSource.from_map',
        lambda *_args, **_kwargs: StubMapEntityRecordSource(
            lambda: StubMapEntityRecordReview(StubMapEntityStats({}))
        ),
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, data_stores=local_data, save_slot=save_slot)
        async with app.run_test() as pilot:
            await pilot.double_click(app.query_one(browser.MapItem))
            assert not isinstance(app.screen, browser.RecordEditorScreen)

    asyncio.run(check())
    with pytest.raises(ValueError, match='No saved local record'):
        local_data.records.load(1)


@pytest.mark.parametrize('entry', ['app', 'browse_maps'])
@pytest.mark.parametrize('persist', [False, True])
def test_browser_entries_share_settings_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, persist: bool
) -> None:
    campaign_catalog = make_campaign_catalog(mods=[], game_dir=tmp_path)
    settings_path = tmp_path / 'settings.json'
    store = SettingsStore(settings_path) if persist else None
    if store is not None:
        store.save(PistSettings(theme='textual-light', dialog_lang='en'))
    data_stores = DataStores(tmp_path / 'local-data.sqlite3')
    monkeypatch.setattr('pist.ui.maps.browser.app.DataStores', lambda: data_stores)
    monkeypatch.setattr(
        'pist.ui.maps.browser.app.record_writer_lock', lambda: record_writer_lock(tmp_path)
    )
    monkeypatch.setattr(CollabLobbyOverrideStore, 'load', lambda _: CollabLobbyOverrides())

    def unexpected_default_store() -> SettingsStore:
        pytest.fail('Browser entries must not create a settings store implicitly')

    monkeypatch.setattr('pist.ui.maps.browser.app.SettingsStore', unexpected_default_store)
    ran = False

    async def check(app: browser.MapBrowserApp) -> None:
        nonlocal ran
        ran = True
        async with app.run_test() as pilot:
            assert app.theme == ('textual-light' if persist else 'textual-dark')
            app.theme = 'nord'
            await pilot.pause()
            assert app.theme == 'nord'

    monkeypatch.setattr(browser.MapBrowserApp, 'run_async', check)
    if entry == 'app':
        asyncio.run(check(browser.MapBrowserApp(campaign_catalog, settings_store=store)))
    else:
        asyncio.run(browse_maps(campaign_catalog, settings_store=store))
    assert ran
    if store is not None:
        saved = store.load()
        assert saved.theme == 'nord'
        assert saved.dialog_lang == 'en'
    else:
        assert not settings_path.exists()


def test_explicit_settings_override_stored_presentation(tmp_path: Path) -> None:
    campaign_catalog = make_campaign_catalog(mods=[], game_dir=tmp_path)
    store = SettingsStore(tmp_path / 'settings.json')
    store.save(PistSettings(theme='textual-light'))
    app = browser.MapBrowserApp(
        campaign_catalog,
        settings=PistSettings(theme='textual-dark'),
        settings_store=store,
        data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
    )
    assert app.theme == 'textual-dark'
    assert store.load().theme == 'textual-light'


def test_builtin_theme_picker_persists_selection(tmp_path: Path) -> None:
    campaign_catalog = make_campaign_catalog(mods=[])
    settings_store = SettingsStore(tmp_path / 'settings.json')

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, settings_store=settings_store)
        async with app.run_test() as pilot:
            assert await pilot.click('HeaderIcon')
            await pilot.press('t', 'h', 'e', 'm', 'e', 'enter')
            await pilot.pause()
            await pilot.press('n', 'o', 'r', 'd', 'enter')
            await pilot.pause()
            assert app.theme == 'nord'
            await pilot.press('t')
            assert app.theme == 'nord'

    asyncio.run(check())
    assert settings_store.load().theme == 'nord'


def test_theme_changes_preserve_game_settings(tmp_path: Path) -> None:
    campaign_catalog = make_campaign_catalog(mods=[])
    settings_store = SettingsStore(tmp_path / 'settings.json')
    settings_store.save(
        PistSettings(
            game_dir=Path('C:/Celeste'),
            smartsheet_url='https://docs.qq.com/smartsheet/example',
        )
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog, settings_store=settings_store)
        async with app.run_test() as pilot:
            app.theme = 'nord'
            await pilot.pause()

    asyncio.run(check())
    settings = settings_store.load()
    assert settings.game_dir == Path('C:/Celeste')
    assert settings.smartsheet_url == 'https://docs.qq.com/smartsheet/example'


def test_escape_quits_the_browser(tmp_path: Path) -> None:
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[], game_dir=tmp_path),
        data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.press('escape')
            await pilot.pause()
            assert not app.is_running

    asyncio.run(check())


def test_page_keys_do_not_switch_save_slots(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Saves'
    saves_dir.mkdir()
    for number in (0, 1):
        MainSave().write(saves_dir / f'{number}.celeste')
    save_slot = make_save_progress(0, {})
    app = browser.MapBrowserApp(
        make_campaign_catalog(mods=[], game_dir=tmp_path),
        data_stores=DataStores(tmp_path / 'local-data.sqlite3'),
        save_reader=SaveProgressReader.from_game_dir(tmp_path),
        save_slot=save_slot,
    )

    async def check() -> None:
        async with app.run_test() as pilot:
            app.query_one(f'#{browser.DETAIL_SCROLL_ID}').focus()
            await pilot.press('pageup', 'pagedown')
            assert app.sub_title == '存档 0'
            await pilot.press(']')
            await _wait_for_save_switch(app)
            assert app.sub_title == '存档 1'

    asyncio.run(check())


def test_campaigns_are_top_level_list_entries() -> None:
    maps = [ContentPath('Maps/TestCollab/1-Beginner/Map.bin')]
    campaign_catalog = make_campaign_catalog(
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
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            campaign_list = app.query_one(browser.CampaignList)
            items = list(campaign_list.query(browser.CampaignItem))
            assert len(items) == 1
            assert items[0].campaign is campaign_catalog.campaigns[0]
            assert isinstance(app.query_one(browser.CollabMapList), browser.CollabMapList)

    asyncio.run(check())


def test_collab_map_list_orders_maps_and_reuses_cached_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = [
        ContentPath('Maps/Test/Hard.bin'),
        ContentPath('Maps/Test/Easy.bin'),
        ContentPath('Maps/Test/Normal.bin'),
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
        write_map_with_icon(tmp_path / map_info, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=str(tmp_path),
        metadata_name='Test',
        metadata_version=None,
        collab_id='Test',
        maps=maps,
    )
    campaign_catalog = make_campaign_catalog(mods=[mod], game_dir=tmp_path)
    local_data = DataStores(tmp_path / 'local-data.sqlite3')
    first_slot = make_save_progress(
        0,
        {
            ('Test/Easy', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1),
            ('Test/Normal', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1),
            (
                'Test/Hard',
                0,
            ): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1, single_run_completed=True),
        },
    )
    second_slot = make_save_progress(
        1,
        {('Test/Easy', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True)},
    )

    async def map_order(app: browser.MapBrowserApp) -> list[str]:
        async with app.run_test(size=(100, 40)) as pilot:
            collab_list = app.query_one(browser.CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            assert not collab_list.has_class('is-loading')
            map_list = collab_list.query_one(browser.MapList)
            assert collab_list.size.height > 1
            return [item.file_path.as_posix() for item in map_list.query(browser.MapItem)]

    assert asyncio.run(
        map_order(
            browser.MapBrowserApp(
                campaign_catalog,
                data_stores=local_data,
                save_reader=MemorySaveReader(first_slot, second_slot),
                save_slot=first_slot,
            )
        )
    ) == [
        'Maps/Test/Hard.bin',
        'Maps/Test/Easy.bin',
        'Maps/Test/Normal.bin',
    ]

    def unexpected_map_read(*_: object) -> object:
        raise AssertionError('A cached Collab map list must not read map files again.')

    monkeypatch.setattr(
        'pist.ui.maps.browser.collab_order.collab.iter_collab_journal_map_icons',
        unexpected_map_read,
    )

    assert asyncio.run(
        map_order(
            browser.MapBrowserApp(
                campaign_catalog,
                data_stores=local_data,
                save_reader=MemorySaveReader(first_slot, second_slot),
                save_slot=first_slot,
            )
        )
    ) == [
        'Maps/Test/Hard.bin',
        'Maps/Test/Easy.bin',
        'Maps/Test/Normal.bin',
    ]

    async def switched_map_order() -> list[str]:
        app = browser.MapBrowserApp(
            campaign_catalog,
            data_stores=local_data,
            save_reader=MemorySaveReader(first_slot, second_slot),
            save_slot=first_slot,
        )
        async with app.run_test(size=(100, 40)) as pilot:
            collab_list = app.query_one(browser.CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            await app.action_next_save_slot()
            await _wait_for_save_switch(app)
            return [
                item.file_path.as_posix()
                for item in collab_list.query_one(browser.MapList).query(browser.MapItem)
            ]

    assert asyncio.run(switched_map_order()) == [
        'Maps/Test/Easy.bin',
        'Maps/Test/Hard.bin',
        'Maps/Test/Normal.bin',
    ]


def test_collab_map_order_sorts_icons_per_campaign_before_progress() -> None:
    first_maps = (
        ContentPath('Maps/First/Hard.bin'),
        ContentPath('Maps/First/Easy.bin'),
    )
    second_maps = (
        ContentPath('Maps/Second/WithoutIcon.bin'),
        ContentPath('Maps/Second/WithIcon.bin'),
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
            first_maps[0].as_posix(): 'areas/First/2-hard',
            first_maps[1].as_posix(): 'areas/First/1-easy',
            second_maps[0].as_posix(): None,
            second_maps[1].as_posix(): 'areas/Second/1-map',
        },
    )

    assert _map_infos(icon_order) == (
        first_maps[1],
        first_maps[0],
        second_maps[0],
        second_maps[1],
    )

    save_slot = make_save_progress(
        0,
        {
            ('Second/WithIcon', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            )
        },
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
        ContentPath('Maps/Test/Hard.bin'),
        ContentPath('Maps/Test/Easy.bin'),
    ]
    for map_info, icon in zip(
        maps,
        ('areas/Test/meters/2-hard', 'areas/Test/meters/1-easy'),
        strict=True,
    ):
        write_map_with_icon(tmp_path / map_info, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=str(tmp_path),
        metadata_name='Test',
        metadata_version=None,
        collab_id='Test',
        maps=maps,
    )
    campaign_catalog = make_campaign_catalog(mods=[mod], game_dir=tmp_path)
    first_slot = make_save_progress(
        0,
        {('Test/Hard', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True)},
    )
    second_slot = make_save_progress(
        1,
        {('Test/Easy', 0): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True)},
    )
    remove_started = asyncio.Event()
    resume_remove = asyncio.Event()
    original_remove_children = browser.CollabMapList.remove_children

    async def pause_first_remove(self: browser.CollabMapList) -> None:
        if not remove_started.is_set():
            remove_started.set()
            await resume_remove.wait()
        await original_remove_children(self)

    monkeypatch.setattr(browser.CollabMapList, 'remove_children', pause_first_remove)

    async def check() -> list[str]:
        app = browser.MapBrowserApp(
            campaign_catalog,
            save_slot=first_slot,
            save_reader=MemorySaveReader(first_slot, second_slot),
        )
        async with app.run_test(size=(100, 40)) as pilot:
            await asyncio.wait_for(remove_started.wait(), timeout=1)
            await pilot.press(']')
            await _wait_for_save_switch(app)
            resume_remove.set()
            collab_list = app.query_one(browser.CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            return [
                item.file_path.as_posix()
                for item in collab_list.query_one(browser.MapList).query(browser.MapItem)
            ]

    assert asyncio.run(check()) == ['Maps/Test/Easy.bin', 'Maps/Test/Hard.bin']


def test_collab_map_list_keeps_selected_side_and_new_slot_stats_when_reordered(
    tmp_path: Path,
) -> None:
    a_side = ContentPath('Maps/Test/Map.bin')
    b_side = ContentPath('Maps/Test/Map-B.bin')
    other = ContentPath('Maps/Test/Other.bin')
    maps = [a_side, b_side, other]
    for map_info, icon in zip(
        maps,
        ('areas/Test/meters/2-map', 'areas/Test/meters/2-map-b', 'areas/Test/meters/1-other'),
        strict=True,
    ):
        write_map_with_icon(tmp_path / map_info, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Test',
        path=str(tmp_path),
        metadata_name='Test',
        metadata_version=None,
        collab_id='Test',
        maps=maps,
    )
    campaign_catalog = make_campaign_catalog(mods=[mod], game_dir=tmp_path)
    first_slot = make_save_progress(
        0,
        {
            ('Test/Map', 0): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            ),
            ('Test/Map', 1): MapStats(
                Duration.from_dotnet_ticks(1_000 * 10_000), 1, completed=True
            ),
        },
    )
    second_slot = make_save_progress(
        1,
        {
            ('Test/Map', 0): MapStats(Duration.from_dotnet_ticks(2_000 * 10_000), 2),
            ('Test/Map', 1): MapStats(Duration.from_dotnet_ticks(4_000 * 10_000), 4),
            (
                'Test/Other',
                0,
            ): MapStats(Duration.from_dotnet_ticks(1_000 * 10_000), 1, single_run_completed=True),
        },
    )

    async def check() -> None:
        app = browser.MapBrowserApp(
            campaign_catalog,
            save_reader=MemorySaveReader(first_slot, second_slot),
            save_slot=first_slot,
        )
        async with app.run_test(size=(100, 40)) as pilot:
            collab_list = app.query_one(browser.CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            map_list = collab_list.query_one(browser.MapList)
            side_item = next(item for item in map_list.map_items if isinstance(item, SideMapItem))
            map_list.index = map_list.map_items.index(side_item)
            side_item.switch_side(MapSideButton.Clicked(1))
            assert side_item.map.file_path is b_side
            await app.action_next_save_slot()
            await _wait_for_save_switch(app)
            refreshed_list = collab_list.query_one(browser.MapList)
            refreshed_side_item = next(
                item for item in refreshed_list.map_items if isinstance(item, SideMapItem)
            )
            assert refreshed_side_item.map.file_path is b_side
            b_side_text = str(
                refreshed_side_item.query_one('.map-item-save-stats', Static).render()
            )
            refreshed_side_item.switch_side(MapSideButton.Clicked(-1))
            a_side_text = str(
                refreshed_side_item.query_one('.map-item-save-stats', Static).render()
            )
            assert '00:04' in b_side_text
            assert '00:02' in a_side_text

    asyncio.run(check())


def test_campaign_switch_replaces_the_detail_map_list() -> None:
    maps = [ContentPath(f'Maps/Test/{name}/Map.bin') for name in ('Prologue', 'Beginner')]
    campaign_catalog = make_campaign_catalog(
        mods=[
            make_installed_mod(
                source='zip',
                filename='Test.zip',
                path='C:/Celeste/Mods/Test.zip',
                metadata_name='Test',
                metadata_version='1.0.0',
                maps=maps,
            )
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test(size=(100, 40)) as pilot:
            campaign_list = app.query_one(browser.CampaignList)
            campaign_list.index = 1
            await pilot.pause()
            assert [item.file_path for item in app.query(browser.MapItem)] == [maps[0]]

    asyncio.run(check())


def test_campaign_switch_loads_the_new_collab_map_list(tmp_path: Path) -> None:
    maps = [
        ContentPath('Maps/Normal/Map.bin'),
        ContentPath('Maps/Test/Map.bin'),
    ]
    write_map_with_icon(tmp_path / maps[1], 'areas/Test/meters/1-easy')
    campaign_catalog = make_campaign_catalog(
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
        ]
    )

    async def check() -> None:
        app = browser.MapBrowserApp(campaign_catalog)
        async with app.run_test() as pilot:
            campaign_list = app.query_one(browser.CampaignList)
            campaign_list.index = next(
                i
                for i, item in enumerate(campaign_list.children)
                if isinstance(item, browser.CampaignItem)
                and item.campaign.directory.as_posix() == 'Maps/Test'
            )
            await pilot.pause()
            collab_list = app.query_one(browser.CollabMapList)
            for _ in range(20):
                await pilot.pause(0.05)
                if not collab_list.has_class('is-loading'):
                    break
            assert not collab_list.has_class('is-loading')
            assert [
                item.file_path for item in collab_list.query_one(browser.MapList).map_items
            ] == [maps[1]]

    asyncio.run(check())
