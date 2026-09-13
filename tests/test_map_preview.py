import asyncio
import json
from typing import cast
from urllib.parse import urlsplit

from aiohttp import ClientSession, web

from pist import map_preview
from pist.game.entities import EntityStat
from pist.game.routes import (
    EndersBlenderSave,
    MapLayout,
    MapLink,
    MapMarker,
    MapRoom,
    MapRoute,
)
from pist.local_data import LocalDataStore
from pist.map_preview import MAP_PREVIEW_HTML, MapPreview, _web_file
from pist.models import InstalledMod, LocalMap
from pist.time import Time


class _Request:
    def __init__(self, data: object, *, match_info: dict[str, str] | None = None) -> None:
        self._data = data
        self.match_info = {} if match_info is None else match_info

    async def json(self) -> object:
        return self._data


def test_map_preview_state_includes_tiles_markers_and_initial_selection() -> None:
    map_info = LocalMap(
        file_path='Maps/Author/Pack/Map.bin',
        dialog_key='Author_Pack_Map',
        names={'en': 'Map'},
    )
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
                    markers=(MapMarker(8, 16, 'strawberry'),),
                    respawns=(),
                ),
            ),
            (MapLink('start', 'Author/Pack/Target', 16, 24, 32, 24),),
        ),
        first_clear_rooms=('start',),
        saved_route=MapRoute(map_file=map_info.file_path, rooms=('start',)),
        enders_blender_save=EndersBlenderSave(
            0,
            {},
            {'Author/Pack/Map': {'start': 2}},
            {'Author/Pack/Map': {'start': Time(2_550)}},
        ),
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))
    assert response.text is not None
    state = json.loads(response.text)

    assert state['title'] == 'Map'
    assert state['selected'] == ['start']
    assert state['firstClearRooms'] == ['start']
    assert state['rooms'][0]['firstClearDeath'] == 2
    assert state['rooms'][0]['firstClearTime'] == 2550
    assert state['rooms'][0]['respawnCount'] == 0
    assert state['rooms'][0]['roomCount'] == 1
    assert state['rooms'][0]['background'] == ['10']
    assert state['rooms'][0]['markers'] == [{'x': 8, 'y': 16, 'kind': 'strawberry'}]
    assert state['rooms'][0]['respawns'] == []
    assert state['links'][0]['x'] == 16
    assert state['links'][0]['y'] == 24
    assert state['links'][0]['width'] == 32
    assert state['links'][0]['height'] == 24


def test_map_preview_state_exposes_configured_marker_sprite() -> None:
    map_info = LocalMap(file_path='Maps/Test.bin', dialog_key='Test', names={'en': 'Test'})
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
                    markers=(
                        MapMarker(
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

    assert json.loads(response.text)['rooms'][0]['markers'] == [
        {
            'x': 4,
            'y': 4,
            'kind': 'seed',
            'sprite': 'seed.png',
            'entityId': 'Example/Seed',
            'attrs': {'x': 4, 'y': 4, 'color': 'blue'},
        }
    ]


def test_map_preview_persists_marker_exclusions_with_the_route(tmp_path) -> None:
    map_info = LocalMap(file_path='Maps/Test.bin', dialog_key='Test', names={'en': 'Test'})
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('room', 0, 0, 8, 8, markers=(MapMarker(4, 4, 'seed', key='seed'),)),)),
        saved_route=MapRoute(
            map_file=map_info.file_path, rooms=(), excluded_markers=frozenset({'seed'})
        ),
        local_data=local_data,
    )

    async def check() -> object:
        state = await preview._state(cast(web.Request, _Request({})))
        await preview._save(
            cast(web.Request, _Request({'rooms': ['room'], 'excludedMarkers': ['seed']}))
        )
        assert state.text is not None
        data = cast(dict[str, list[dict[str, object]]], json.loads(state.text))
        return data['rooms'][0]['markers']

    markers = asyncio.run(check())
    route = local_data.load_route(map_info.file_path)

    assert markers == [{'x': 4, 'y': 4, 'kind': 'seed', 'key': 'seed', 'excluded': True}]
    assert route is not None
    assert route.excluded_markers == frozenset({'seed'})


def test_map_preview_state_exposes_collectible_summary_inputs() -> None:
    map_info = LocalMap(file_path='Maps/Test.bin', dialog_key='Test', names={'en': 'Test'})
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
                    markers=(
                        MapMarker(
                            2,
                            2,
                            'end_level_heart',
                            key='end',
                            summary_kind='heart',
                            summary_stat=EntityStat.SELECT,
                            summary_label='水晶之心',
                            summary_value='通关收集',
                        ),
                        MapMarker(
                            6,
                            6,
                            'keep_going_heart',
                            key='extra',
                            summary_kind='heart',
                            summary_stat=EntityStat.SELECT,
                            summary_label='水晶之心',
                            summary_value='额外收集',
                        ),
                    ),
                ),
            )
        ),
        saved_route=MapRoute(
            map_file=map_info.file_path, rooms=(), excluded_markers=frozenset({'extra'})
        ),
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))

    assert response.text is not None
    markers = json.loads(response.text)['rooms'][0]['markers']
    assert markers[0]['summary'] == {
        'kind': 'heart',
        'stat': 'select',
        'label': '水晶之心',
        'value': '通关收集',
    }
    assert markers[1]['excluded'] is True


def test_read_only_map_preview_marks_audited_entities_without_exposing_routes() -> None:
    map_info = LocalMap(
        file_path='Maps/Author/Pack/Map.bin',
        dialog_key='Author_Pack_Map',
        names={'en': 'Map'},
    )
    preview = MapPreview(
        map_info,
        MapLayout(
            (MapRoom('start', 0, 0, 320, 184, markers=(MapMarker(8, 16, 'strawberry'),)),),
            (MapLink('start', 'Author/Pack/Target', 16, 24, 32, 24),),
        ),
        audit_markers={'start': (MapMarker(32, 48, 'audit'),)},
        read_only=True,
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))
    assert response.text is not None
    state = json.loads(response.text)

    assert state['readOnly'] is True
    assert state['links'] == []
    assert state['rooms'][0]['markers'] == [
        {'x': 8, 'y': 16, 'kind': 'strawberry'},
        {'x': 32, 'y': 48, 'kind': 'audit'},
    ]

    async def check() -> int:
        response = await preview._save(cast(web.Request, _Request({'rooms': ['start']})))
        return response.status

    assert asyncio.run(check()) == 400


def test_map_preview_save_validates_rooms_and_persists_layout_order(tmp_path) -> None:
    map_info = LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Author_Pack_Map')
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184))),
        local_data=local_data,
    )

    async def check() -> int:
        invalid = await preview._save(cast(web.Request, _Request({'rooms': ['missing']})))
        await preview._save(cast(web.Request, _Request({'rooms': ['goal', 'start']})))
        return invalid.status

    status = asyncio.run(check())
    route = local_data.load_route(map_info.file_path)

    assert status == 400
    assert route is not None
    assert route == MapRoute(map_file=map_info.file_path, rooms=('start', 'goal'))


def test_map_preview_saves_explicit_in_game_room_counts(tmp_path) -> None:
    map_info = LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Author_Pack_Map')
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184))),
        local_data=local_data,
    )

    async def check() -> None:
        await preview._save(
            cast(
                web.Request,
                _Request({'rooms': ['goal', 'start'], 'roomCounts': {'start': 2, 'goal': 1}}),
            )
        )

    asyncio.run(check())
    route = local_data.load_route(map_info.file_path)

    assert route is not None
    assert route == MapRoute(
        map_file=map_info.file_path,
        rooms=('start', 'goal'),
        room_counts={'start': 2},
    )
    assert route.room_count == 3


def test_map_preview_abandon_cancels_an_open_preview() -> None:
    map_info = LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Author_Pack_Map')
    preview = MapPreview(map_info, MapLayout(()))

    async def check() -> None:
        preview._result = asyncio.get_running_loop().create_future()
        await preview._abandon(cast(web.Request, _Request({})))
        assert preview._result.result() is None

    asyncio.run(check())


def test_map_preview_serves_packaged_game_assets() -> None:
    preview = MapPreview(
        LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Author_Pack_Map'), MapLayout(())
    )
    response = asyncio.run(
        preview._game_asset(cast(web.Request, _Request({}, match_info={'name': 'strawberry.png'})))
    )

    assert response.content_type == 'image/png'


def test_map_preview_serves_any_existing_safe_packaged_sprite() -> None:
    assert map_preview._sprite_data('silverberry.png') is not None


def test_map_preview_serves_only_safe_local_configured_sprites(tmp_path, monkeypatch) -> None:
    sprites = tmp_path / 'sprites'
    sprites.mkdir()
    (sprites / 'seed.png').write_bytes(b'png')
    monkeypatch.setattr(map_preview, 'LOCAL_SPRITES_DIR', sprites)

    assert map_preview._sprite_data('seed.png') == b'png'
    assert map_preview._sprite_data('../seed.png') is None


def test_map_preview_opens_only_a_linked_map_and_keeps_page_history(tmp_path, monkeypatch) -> None:
    source = LocalMap(
        file_path='Maps/Author/Pack/Source.bin',
        dialog_key='Author_Pack_Source',
        names={'en': 'Source'},
    )
    target = LocalMap(
        file_path='Maps/Author/Pack/Target.bin',
        dialog_key='Author_Pack_Target',
        names={'en': 'Target'},
    )
    mod = InstalledMod(
        source='directory',
        filename='Mod',
        path='Mod',
        metadata_name='Mod',
        metadata_version=None,
        maps=[source, target],
    )
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    local_data.save_route(MapRoute(map_file=target.file_path, rooms=('saved',)))
    preview = MapPreview(
        source,
        MapLayout((MapRoom('lobby', 0, 0, 320, 184),), (MapLink('lobby', 'Author/Pack/Target'),)),
        mod=mod,
        local_data=local_data,
        enders_blender_save=EndersBlenderSave(0, {'Author/Pack/Target': ('target',)}),
    )
    monkeypatch.setattr(
        'pist.map_preview.load_map_layout',
        lambda _mod, _map: MapLayout(
            (MapRoom('target', 0, 0, 320, 184), MapRoom('saved', 400, 0, 320, 184))
        ),
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


def test_map_preview_serves_loopback_state_and_persists_saved_route(
    tmp_path, monkeypatch
) -> None:
    map_info = LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Author_Pack_Map')
    local_data = LocalDataStore(tmp_path / 'local-data.sqlite3')
    preview = MapPreview(
        map_info,
        MapLayout((MapRoom('start', 0, 0, 320, 184), MapRoom('goal', 400, 0, 320, 184))),
        local_data=local_data,
    )
    urls: list[str] = []
    monkeypatch.setattr('pist.map_preview.webbrowser.open', lambda url: urls.append(url) or True)

    async def check() -> MapRoute | None:
        task = asyncio.create_task(preview.preview())
        while not urls:
            await asyncio.sleep(0)
        async with ClientSession() as session:
            async with session.get(urls[0]) as response:
                page = await response.text()
                asset_dir = f'{urlsplit(urls[0]).path}/assets'
                assert f'{asset_dir}/map_preview.css' in page
                assert f'{asset_dir}/map_preview.js' in page
            async with session.get(f'{urls[0]}/state') as response:
                assert (await response.json())['rooms'][1]['name'] == 'goal'
            async with session.get(f'{urls[0]}/assets/map_preview.js') as response:
                assert response.content_type == 'text/javascript'
                assert 'renderList' in (await response.text())
            async with session.post(f'{urls[0]}/save', json={'rooms': ['goal']}) as response:
                assert response.status == 200
            async with session.post(f'{urls[0]}/abandon') as response:
                assert response.status == 200
        return await task

    assert asyncio.run(check()) is None
    assert local_data.load_route(map_info.file_path) == MapRoute(
        map_file=map_info.file_path, rooms=('goal',)
    )


def test_map_preview_page_contains_map_interaction_modes() -> None:
    assert '<canvas id="map"></canvas>' in MAP_PREVIEW_HTML
    assert 'name="mode"' in MAP_PREVIEW_HTML
    assert 'value="collectibles"' in MAP_PREVIEW_HTML
    assert 'id="collectible-summary"' in MAP_PREVIEW_HTML
    assert 'id="show-first-clear-route"' in MAP_PREVIEW_HTML
    assert 'id="show-first-clear-time"' in MAP_PREVIEW_HTML
    assert 'map_preview.js' in MAP_PREVIEW_HTML
    script = _web_file('map_preview.js')
    assert 'renderList' in script
    assert 'nearestTrigger' in script
    assert 'nearestCollectibleMarker' in script
    assert "mode === 'review'" in script
    assert "if (mode === 'inspect')" in script
    assert 'const initial = state === undefined;' in script
    assert "else if (initial) mode = 'review';" in script
    assert 'highlightedRoom = room.name;' in script
    assert "row.classList.toggle('canvas-selected', room.name === canvasSelectedRoom)" in script
    assert 'roomsNode.scrollTop =' in script
    assert "if (mode === 'navigate' && isDoubleClick)" in script
    assert "mode === 'collectibles'" in script
    assert 'nearestInspectableMarker' in script
    assert 'showInspectable' in script
    assert 'let openingMap = false;' in script
    assert 'function revealRoom(name)' in script
    assert 'function applySelectionBox(event)' in script
    assert 'event.ctrlKey && event.shiftKey' in script
    assert 'event.button === 2' in script
    assert 'event.button === 1' in script
    assert "canvas.addEventListener('contextmenu'" in script
    assert "canvas.addEventListener('dblclick'" not in script
    assert 'canvasSelectedRoom = room.name;\n      selectedRoomToReveal = room.name;' in script
    assert 'drawOutlinedText(String(index)' in script
    assert 'highlightedRoom === room.name ? undefined : room.name' in script
    assert 'pagehide' in script
    assert '/abandon' in script
    assert 'firstClearData' in script
    assert 'showFirstClearRoute.checked' in script
    assert 'showFirstClearTime.checked' in script
    assert 'drawSprite' in script
    assert 'game-assets' in script
    assert 'ctx.imageSmoothingEnabled = false' in script
    assert "ctx.fillStyle = 'rgba(0, 0, 0, .48)'" in script
    assert 'ctx.lineTo(x + size, y)' not in script
    assert 'drawOutlinedText' in script
    assert 'ctx.fillRect(Math.floor(x / 8) * 8, Math.floor((y - 8) / 8) * 8, 8, 8)' in script
    assert "item.kind === 'audit'" in script
    assert 'state.readOnly' in script
    assert 'collectibleSummaryText' in script
    css = _web_file('map_preview.css')
    assert '#mode[hidden]' in css
    assert 'button[hidden]' in css
