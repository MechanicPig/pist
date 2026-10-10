import asyncio
import json
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from aiohttp import ClientSession, web

from berries.entities.rules import SHARED_ENTITIES_PATH, load_entity_rules
from berries.game.content import ContentPath
from berries.game.duration import Duration
from berries.game.enders_blender import EndersBlenderSave
from berries.game.levels import Level, LevelSide, Map
from berries.game.maps import MapInfo
from berries.map_entrances import MapEntranceSource
from berries.map_layout import MapEntrance, MapLayout, MapPreviewEntity, MapRoom
from berries.map_preview.session import PreviewAssets
from pist.entity_stats import load_entity_stat_rules
from pist.map_preview import MapPreview, MapPreviewMode
from pist.routes.models import MapRoute
from pist.routes.store import RouteStore
from test_support.browser import load_browser_modules
from test_support.mod_factory import make_installed_mod
from test_support.preview import preview_url


class _Request:
    def __init__(self, data: object, *, match_info: dict[str, str] | None = None) -> None:
        self._data = data
        self.match_info = {} if match_info is None else match_info

    async def json(self) -> object:
        return self._data


def test_failed_route_save_returns_json_and_preserves_cancel_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Test.bin'))
    store = RouteStore(tmp_path / 'routes.sqlite3')
    original = MapRoute(
        map_file=map_info.file_path.as_posix(), rooms=('start',), room_counts={'start': 2}
    )
    store.save(original)
    layout = MapLayout((MapRoom('start', 0, 0, 100, 100), MapRoom('goal', 100, 0, 100, 100)))
    preview = MapPreview(map_info, layout, route_store=store, saved_route=original)
    urls: list[str] = []
    monkeypatch.setattr(
        'berries.map_preview.session.webbrowser.open', lambda url: urls.append(url) or True
    )
    with store.connect() as conn:
        conn.execute("""CREATE TRIGGER reject_route BEFORE INSERT ON map_routes BEGIN
            SELECT RAISE(ABORT, 'simulated route write failure'); END""")

    async def run() -> None:
        task = asyncio.create_task(preview.preview())
        async with preview_url(task, urls) as url, ClientSession() as session:
            payload = {'rooms': ['goal'], 'roomCounts': {'goal': 3}}
            async with session.post(f'{url}/save', json=payload) as response:
                assert response.status == 500
                assert 'simulated route write failure' in (await response.json())['error']
            async with session.post(f'{url}/cancel') as response:
                state = await response.json()
                assert state['selected'] == ['start']
                assert state['rooms'][0]['roomCount'] == 2
            assert store.load(original.map_file) == original
            reopened = MapPreview(
                map_info, layout, route_store=store, saved_route=store.load(original.map_file)
            )
            state_response = await reopened._state(cast(web.Request, _Request({})))
            assert state_response.text is not None
            assert json.loads(state_response.text)['selected'] == ['start']
            with store.connect() as conn:
                conn.execute('DROP TRIGGER reject_route')
            async with session.post(f'{url}/save', json=payload) as response:
                assert response.status == 200
            async with session.post(f'{url}/cancel') as response:
                state = await response.json()
                assert state['selected'] == ['goal']
                assert state['rooms'][1]['roomCount'] == 3
            assert store.load(original.map_file) == MapRoute(
                map_file=original.map_file, rooms=('goal',), room_counts={'goal': 3}
            )

    asyncio.run(run())


def test_map_preview_rejects_invalid_browser_requests() -> None:
    preview = MapPreview(
        MapInfo(file_path=ContentPath('Maps/Test.bin')),
        MapLayout((MapRoom('room', 0, 0, 8, 8),)),
    )

    async def check() -> tuple[web.Response, web.Response]:
        return (
            await preview._open(cast(web.Request, _Request({'targetSid': 1}))),
            await preview._save(
                cast(web.Request, _Request({'rooms': ['room'], 'roomCounts': {'room': True}}))
            ),
        )

    open_response, save_response = asyncio.run(check())

    assert open_response.status == 400
    assert save_response.status == 400


def test_map_preview_state_includes_tiles_markers_and_initial_selection() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    preview = MapPreview(
        map_info,
        MapLayout(
            (
                MapRoom(
                    'start',
                    0,
                    0,
                    320,
                    184,
                    background=('10',),
                    solids=('01',),
                    entities=(MapPreviewEntity(8, 16, 'strawberry'),),
                    respawns=(),
                ),
                MapRoom('goal', 400, 0, 320, 184),
            ),
            (MapEntrance('start', 'Author/Pack/Target', 16, 24, 32, 24),),
        ),
        first_clear_rooms=('start', 'goal'),
        saved_route=MapRoute(map_file=map_info.file_path.as_posix(), rooms=('start',)),
        enders_blender_save=EndersBlenderSave(
            0,
            {},
            {'Author/Pack/Map': {'start': 2}},
            {
                'Author/Pack/Map': {
                    'start': Duration.from_milliseconds(2_550),
                    'goal': Duration.from_milliseconds(1_450),
                }
            },
        ),
        title='Map',
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))
    assert response.text is not None
    state = json.loads(response.text)

    assert state['title'] == 'Map'
    assert state['initialMode'] == 'edit_route'
    assert state['selected'] == ['start']
    assert state['firstClearRooms'] == ['start', 'goal']
    assert state['rooms'][0]['firstClearDeath'] == 2
    assert state['rooms'][0]['firstClearTime'] == 2550
    assert state['rooms'][0]['firstClearCumulativeTime'] == 2550
    assert state['rooms'][1]['firstClearTime'] == 1450
    assert state['rooms'][1]['firstClearCumulativeTime'] == 4000
    assert state['rooms'][0]['respawnCount'] == 0
    assert state['rooms'][0]['roomCount'] == 1
    assert state['rooms'][0]['background'] == ['10']
    assert state['rooms'][0]['entities'] == [{'x': 8, 'y': 16, 'kind': 'strawberry'}]
    assert state['rooms'][0]['respawns'] == []
    assert state['entrances'][0]['x'] == 16
    assert state['entrances'][0]['y'] == 24
    assert state['entrances'][0]['width'] == 32
    assert state['entrances'][0]['height'] == 24


def test_map_preview_cumulative_time_stops_after_a_missing_room_time() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    preview = MapPreview(
        map_info,
        MapLayout(
            (
                MapRoom('start', 0, 0, 320, 184),
                MapRoom('middle', 400, 0, 320, 184),
                MapRoom('goal', 800, 0, 320, 184),
            )
        ),
        first_clear_rooms=('start', 'middle', 'goal'),
        enders_blender_save=EndersBlenderSave(
            0,
            {},
            {},
            {
                'Author/Pack/Map': {
                    'start': Duration.from_milliseconds(1_000),
                    'goal': Duration.from_milliseconds(3_000),
                }
            },
        ),
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))
    assert response.text is not None
    rooms = json.loads(response.text)['rooms']

    assert rooms[0]['firstClearCumulativeTime'] == 1000
    assert 'firstClearCumulativeTime' not in rooms[1]
    assert 'firstClearCumulativeTime' not in rooms[2]


def test_map_preview_state_exposes_configured_marker_sprite() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Test.bin'))
    preview = MapPreview(
        map_info,
        MapLayout(
            (
                MapRoom(
                    'room',
                    0,
                    0,
                    8,
                    8,
                    entities=(
                        MapPreviewEntity(
                            4,
                            4,
                            'seed',
                            'seed.png',
                            entity_name='Example/Seed',
                            attrs={'x': 4, 'y': 4, 'color': 'blue'},
                        ),
                    ),
                ),
            )
        ),
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))
    assert response.text is not None

    assert json.loads(response.text)['rooms'][0]['entities'] == [
        {
            'x': 4,
            'y': 4,
            'kind': 'seed',
            'sprite': 'seed.png',
            'entityId': 'Example/Seed',
            'attrs': {'x': 4, 'y': 4, 'color': 'blue'},
        }
    ]


def test_map_preview_persists_entity_exclusions_with_the_route(tmp_path: Path) -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Test.bin'))
    local_data = RouteStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout(
            (MapRoom('room', 0, 0, 8, 8, entities=(MapPreviewEntity(4, 4, 'seed', key='seed'),)),)
        ),
        saved_route=MapRoute(
            map_file=map_info.file_path.as_posix(),
            rooms=(),
            excluded_entities=frozenset({'seed'}),
        ),
        route_store=local_data,
    )

    async def check() -> object:
        state = await preview._state(cast(web.Request, _Request({})))
        await preview._save(
            cast(web.Request, _Request({'rooms': ['room'], 'excludedEntities': ['seed']}))
        )
        assert state.text is not None
        data = cast(dict[str, list[dict[str, object]]], json.loads(state.text))
        return data['rooms'][0]['entities']

    entities = asyncio.run(check())
    route = local_data.load(map_info.file_path.as_posix())

    assert entities == [{'x': 4, 'y': 4, 'kind': 'seed', 'key': 'seed', 'excluded': True}]
    assert route is not None
    assert route.excluded_entities == frozenset({'seed'})


def test_map_preview_state_exposes_collectible_summary_inputs() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Test.bin'))
    classification_rules = load_entity_rules(SHARED_ENTITIES_PATH)
    preview = MapPreview(
        map_info,
        MapLayout(
            (
                MapRoom(
                    'room',
                    0,
                    0,
                    8,
                    8,
                    entities=(
                        MapPreviewEntity(
                            2,
                            2,
                            'end_level_heart',
                            key='end',
                        ),
                        MapPreviewEntity(
                            6,
                            6,
                            'keep_going_heart',
                            key='extra',
                        ),
                    ),
                ),
            )
        ),
        saved_route=MapRoute(
            map_file=map_info.file_path.as_posix(),
            rooms=(),
            excluded_entities=frozenset({'extra'}),
        ),
        classification_rules=classification_rules,
        statistic_rules=load_entity_stat_rules(classification_rules),
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))

    assert response.text is not None
    entities = json.loads(response.text)['rooms'][0]['entities']
    assert entities[0]['summary'] == {
        'kind': 'heart',
        'stat': 'select',
        'label': '水晶之心',
        'value': '通关收集',
    }
    assert entities[1]['excluded'] is True


def test_read_only_map_preview_marks_audited_entities_without_exposing_routes() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    preview = MapPreview(
        map_info,
        MapLayout(
            (MapRoom('start', 0, 0, 320, 184, entities=(MapPreviewEntity(8, 16, 'strawberry'),)),),
            (MapEntrance('start', 'Author/Pack/Target', 16, 24, 32, 24),),
        ),
        audit_entities={'start': (MapPreviewEntity(32, 48, 'audit'),)},
        read_only=True,
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))
    assert response.text is not None
    state = json.loads(response.text)

    assert state['readOnly'] is True
    assert state['initialMode'] == 'preview'
    assert state['entrances'] == []
    assert state['rooms'][0]['entities'] == [
        {'x': 8, 'y': 16, 'kind': 'strawberry'},
        {'x': 32, 'y': 48, 'kind': 'audit'},
    ]

    async def check() -> int:
        response = await preview._save(cast(web.Request, _Request({'rooms': ['start']})))
        return response.status

    assert asyncio.run(check()) == 400


def test_preview_mode_exposes_routes_and_can_save_edits() -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    preview = MapPreview(
        map_info,
        MapLayout(
            (MapRoom('start', 0, 0, 320, 184),),
            (
                MapEntrance(
                    'start',
                    'Author/Pack/Target',
                    x=16,
                    y=24,
                    width=32,
                    height=24,
                    source=MapEntranceSource.TRIGGER,
                    element_name='CollabUtils2/ChapterPanelTrigger',
                    attrs={'map': 'Author/Pack/Target'},
                ),
            ),
        ),
        initial_mode=MapPreviewMode.PREVIEW,
    )

    async def check() -> tuple[dict[str, object], int]:
        state_response = await preview._state(cast(web.Request, _Request({})))
        assert state_response.text is not None
        save_response = await preview._save(cast(web.Request, _Request({'rooms': ['start']})))
        return json.loads(state_response.text), save_response.status

    state, save_status = asyncio.run(check())

    assert state['readOnly'] is False
    assert state['initialMode'] == 'preview'
    assert state['entrances'] == [
        {
            'room': 'start',
            'targetSid': 'Author/Pack/Target',
            'x': 16,
            'y': 24,
            'width': 32,
            'height': 24,
            'targetTitle': 'Author/Pack/Target',
            'available': False,
            'source': 'trigger',
            'entityId': 'CollabUtils2/ChapterPanelTrigger',
            'attrs': {'map': 'Author/Pack/Target'},
        }
    ]
    assert save_status == 200


def test_map_preview_save_validates_rooms_and_persists_layout_order(tmp_path: Path) -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    local_data = RouteStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184))),
        route_store=local_data,
    )

    async def check() -> int:
        invalid = await preview._save(cast(web.Request, _Request({'rooms': ['missing']})))
        await preview._save(cast(web.Request, _Request({'rooms': ['goal', 'start']})))
        return invalid.status

    status = asyncio.run(check())
    route = local_data.load(map_info.file_path.as_posix())

    assert status == 400
    assert route is not None
    assert route == MapRoute(map_file=map_info.file_path.as_posix(), rooms=('start', 'goal'))


def test_map_preview_saves_explicit_in_game_room_counts(tmp_path: Path) -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    local_data = RouteStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184))),
        route_store=local_data,
    )

    async def check() -> None:
        await preview._save(
            cast(
                web.Request,
                _Request({'rooms': ['goal', 'start'], 'roomCounts': {'start': 2, 'goal': 1}}),
            )
        )

    asyncio.run(check())
    route = local_data.load(map_info.file_path.as_posix())

    assert route is not None
    assert route == MapRoute(
        map_file=map_info.file_path.as_posix(),
        rooms=('start', 'goal'),
        room_counts={'start': 2},
    )
    assert route.room_count == 3


def test_map_preview_rejects_non_positive_explicit_room_count(tmp_path: Path) -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184),)),
        route_store=RouteStore(tmp_path / 'local-data.sqlite3'),
    )

    async def check() -> int:
        response = await preview._save(
            cast(web.Request, _Request({'rooms': ['start'], 'roomCounts': {'start': 0}}))
        )
        return response.status

    assert asyncio.run(check()) == 400


def test_map_preview_serves_packaged_game_assets() -> None:
    preview = MapPreview(MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin')), MapLayout(()))
    response = asyncio.run(
        preview._session.assets.game_asset(
            cast(web.Request, _Request({}, match_info={'name': 'strawberry.png'}))
        )
    )

    assert response.content_type == 'image/png'


def test_map_preview_serves_any_existing_safe_packaged_sprite() -> None:
    assert PreviewAssets('pist.map_preview').sprite('silverberry.png') is not None


def test_map_preview_serves_only_safe_local_configured_sprites(tmp_path: Path) -> None:
    sprites = tmp_path / 'sprites'
    sprites.mkdir()
    (sprites / 'seed.png').write_bytes(b'png')
    assets = PreviewAssets('pist.map_preview', sprite_dir=sprites)

    assert assets.sprite('seed.png') == b'png'
    assert assets.sprite('../seed.png') is None


def test_map_preview_opens_only_a_linked_map_and_keeps_page_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = MapInfo(file_path=ContentPath('Maps/Author/Pack/Source.bin'))
    target = MapInfo(file_path=ContentPath('Maps/Author/Pack/Target.bin'))
    source_mod = make_installed_mod(
        source='directory',
        filename='SourceMod',
        path='SourceMod',
        metadata_name='SourceMod',
        metadata_version=None,
        maps=[source],
    )
    target_mod = make_installed_mod(
        source='directory',
        filename='TargetMod',
        path='TargetMod',
        metadata_name='TargetMod',
        metadata_version=None,
        maps=[target],
    )
    local_data = RouteStore(tmp_path / 'local-data.sqlite3')
    local_data.save(MapRoute(map_file=target.file_path.as_posix(), rooms=('saved',)))
    preview = MapPreview(
        Map(source, source_mod),
        MapLayout(
            (MapRoom('lobby', 0, 0, 320, 184),), (MapEntrance('lobby', 'Author/Pack/Target'),)
        ),
        route_store=local_data,
        enders_blender_save=EndersBlenderSave(0, {'Author/Pack/Target': ('target',)}),
        dialogs={
            'en': {
                'Author_Pack_Source': 'Source',
                'Author_Pack_Target': 'Target',
            }
        },
        routable_maps={
            'Author/Pack/Target': (
                Level(
                    sid='Author/Pack/Target',
                    dialog_key='Author_Pack_Target',
                    maps=(Map(target, target_mod),),
                ),
                LevelSide.A,
            )
        },
    )
    loaded_targets: list[Map] = []

    def load_target(loaded_map: Map) -> MapLayout:
        loaded_targets.append(loaded_map)
        return MapLayout((MapRoom('target', 0, 0, 320, 184), MapRoom('saved', 400, 0, 320, 184)))

    monkeypatch.setattr(
        'berries.map_preview.navigation.map_layout.load_map_layout',
        load_target,
    )

    async def check() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        first_open, second_open = await asyncio.gather(
            preview._open(cast(web.Request, _Request({'targetSid': 'Author/Pack/Target'}))),
            preview._open(cast(web.Request, _Request({'targetSid': 'Author/Pack/Target'}))),
        )
        backed = await preview._back(cast(web.Request, _Request({})))
        forwarded = await preview._forward(cast(web.Request, _Request({})))
        assert first_open.text is not None
        assert second_open.text is not None
        assert backed.text is not None
        assert forwarded.text is not None
        return json.loads(first_open.text), json.loads(backed.text), json.loads(forwarded.text)

    opened, backed, forwarded = asyncio.run(check())

    assert opened['title'] == 'Target'
    assert opened['selected'] == ['saved']
    assert opened['firstClearRooms'] == ['target']
    assert opened['canBack'] is True
    assert opened['canForward'] is False
    assert backed['title'] == 'Source'
    assert backed['canBack'] is False
    assert backed['canForward'] is True
    assert forwarded['title'] == 'Target'
    assert loaded_targets
    assert all(loaded_target.content is target_mod for loaded_target in loaded_targets)


def test_map_preview_uses_runtime_level_key_for_linked_isolated_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = MapInfo(file_path=ContentPath('Maps/Author/Pack/Source.bin'))
    target = MapInfo(file_path=ContentPath('Maps/Author/Pack/Target-B.bin'))
    source_mod = make_installed_mod(
        source='directory',
        filename='SourceMod',
        path='SourceMod',
        metadata_name='SourceMod',
        metadata_version=None,
        maps=[source],
    )
    target_mod = make_installed_mod(
        source='directory',
        filename='TargetMod',
        path='TargetMod',
        metadata_name='TargetMod',
        metadata_version=None,
        maps=[target],
    )
    target_level = Level(
        sid='Author/Pack/Target-B',
        dialog_key='Author_Pack_Target_B',
        maps=(Map(target, target_mod),),
    )
    preview = MapPreview(
        Map(source, source_mod),
        MapLayout(
            (MapRoom('lobby', 0, 0, 320, 184),),
            (MapEntrance('lobby', target_level.sid),),
        ),
        enders_blender_save=EndersBlenderSave(
            0,
            {target_level.sid: ('target',)},
            {target_level.sid: {'target': 4}},
            {target_level.sid: {'target': Duration.from_milliseconds(2500)}},
        ),
        dialogs={'en': {'Author_Pack_Target_B': 'Isolated Side'}},
        routable_maps={target_level.sid: (target_level, LevelSide.A)},
    )
    monkeypatch.setattr(
        'berries.map_preview.navigation.map_layout.load_map_layout',
        lambda _: MapLayout((MapRoom('target', 0, 0, 320, 184),)),
    )

    response = asyncio.run(
        preview._open(cast(web.Request, _Request({'targetSid': target_level.sid})))
    )

    assert response.text is not None
    opened = json.loads(response.text)
    assert opened['title'] == 'Isolated Side'
    assert opened['selected'] == ['target']
    assert opened['firstClearRooms'] == ['target']
    assert opened['rooms'][0]['firstClearDeath'] == 4
    assert opened['rooms'][0]['firstClearTime'] == 2500


def test_map_preview_serves_loopback_state_and_persists_saved_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_info = MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin'))
    local_data = RouteStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184))),
        route_store=local_data,
    )
    urls: list[str] = []
    monkeypatch.setattr(
        'berries.map_preview.session.webbrowser.open', lambda url: urls.append(url) or True
    )

    async def check() -> MapRoute | None:
        task = asyncio.create_task(preview.preview())
        async with preview_url(task, urls) as url, ClientSession() as session:
            async with session.get(url) as response:
                page = await response.text()
                asset_dir = f'{urlsplit(url).path}/assets'
                assert f'{asset_dir}/map_preview.css' in page
                assert f'{asset_dir}/map_preview.js' in page
                assert 'name="mode" value="edit_route"' in page
            async with session.get(f'{url}/assets/canvas.js') as response:
                assert response.status == 200
                assert response.content_type == 'text/javascript'
                assert 'export class MapCanvas' in await response.text()
            async with session.get(f'{url}/state') as response:
                state = await response.json()
                assert state['rooms'][1]['name'] == 'goal'
                assert state['initialMode'] == 'edit_route'
            async with session.get(f'{url}/assets/map_preview.js') as response:
                assert response.content_type == 'text/javascript'
                assert await response.text()
            modules = await load_browser_modules(session, url)
            assert {
                'viewport.js',
                'geometry.js',
                'client.js',
                'edit_state.js',
                'overlays.js',
                'object_info.js',
                'mouse.js',
                'room_list.js',
            } <= modules
            async with session.get(f'{url}/assets/unregistered.js') as response:
                assert response.status == 404
            async with session.get(f'{url}/assets/object_info.css') as response:
                assert response.status == 200
                assert response.content_type == 'text/css'
            async with session.post(f'{url}/save', json={'rooms': ['goal']}) as response:
                assert response.status == 200
            async with session.post(f'{url}/abandon') as response:
                assert response.status == 200
            return await task

    assert asyncio.run(check()) is None
    assert local_data.load(map_info.file_path.as_posix()) == MapRoute(
        map_file=map_info.file_path.as_posix(), rooms=('goal',)
    )
