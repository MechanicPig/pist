import asyncio
import json
from typing import cast
from urllib.parse import urlsplit

from aiohttp import ClientSession, web
from berries.game.maps import MapInfo
from berries.map_layout import MapEntrance, MapLayout, MapPreviewEntity, MapRoom
from berries.map_preview import MapPreview


class _Request:
    def __init__(self, data: object, *, match_info: dict[str, str] | None = None) -> None:
        self._data = data
        self.match_info = {} if match_info is None else match_info

    async def json(self) -> object:
        return self._data


def test_public_preview_protocol_contains_only_objective_map_state() -> None:
    preview = MapPreview(
        MapInfo(file_path='Maps/Author/Pack/Map.bin'),
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
                    entities=(
                        MapPreviewEntity(
                            8,
                            16,
                            'strawberry',
                            'strawberry.png',
                            key='start:1',
                            entity_name='strawberry',
                            attrs={'id': 1},
                        ),
                    ),
                ),
            ),
            (MapEntrance('start', 'Author/Pack/Target', 16, 24, 32, 24),),
        ),
        extra_entities={'start': (MapPreviewEntity(32, 48, 'audit'),)},
        title='Map',
    )

    response = asyncio.run(preview._state(cast(web.Request, _Request({}))))

    assert response.text is not None
    state = json.loads(response.text)
    assert state.keys() == {'title', 'rooms', 'entrances', 'canBack', 'canForward', 'canHome'}
    assert state['rooms'][0].keys() == {
        'name',
        'x',
        'y',
        'width',
        'height',
        'background',
        'solids',
        'entities',
        'respawns',
    }
    assert state['rooms'][0]['entities'] == [
        {
            'x': 8,
            'y': 16,
            'kind': 'strawberry',
            'key': 'start:1',
            'sprite': 'strawberry.png',
            'entityId': 'strawberry',
            'attrs': {'id': 1},
        },
        {'x': 32, 'y': 48, 'kind': 'audit'},
    ]


def test_public_preview_rejects_unavailable_map_entry() -> None:
    preview = MapPreview(
        MapInfo(file_path='Maps/Author/Pack/Map.bin'),
        MapLayout(
            (MapRoom('start', 0, 0, 320, 184),),
            (MapEntrance('start', 'Author/Pack/Target'),),
        ),
    )

    response = asyncio.run(
        preview._open(cast(web.Request, _Request({'targetSid': 'Author/Pack/Target'})))
    )

    assert response.status == 400


def test_public_preview_closes_without_persistence() -> None:
    preview = MapPreview(MapInfo(file_path='Maps/Test.bin'), MapLayout(()))

    async def check() -> None:
        preview._result = asyncio.get_running_loop().create_future()
        response = await preview._close(cast(web.Request, _Request({})))
        assert response.status == 200
        assert preview._result.result() is None

    asyncio.run(check())


def test_public_preview_serves_loopback_state_and_cleans_up(monkeypatch) -> None:
    preview = MapPreview(
        MapInfo(file_path='Maps/Test.bin'),
        MapLayout((MapRoom('room', 0, 0, 8, 8),)),
    )
    urls: list[str] = []
    monkeypatch.setattr(
        'berries.map_preview.server.webbrowser.open', lambda url: urls.append(url) or True
    )

    async def check() -> None:
        task = asyncio.create_task(preview.preview())
        while not urls:
            await asyncio.sleep(0)
        async with ClientSession() as session:
            async with session.get(urls[0]) as response:
                page = await response.text()
                asset_dir = f'{urlsplit(urls[0]).path}/assets'
                assert f'{asset_dir}/map_preview.js' in page
            async with session.get(f'{urls[0]}/state') as response:
                assert (await response.json())['rooms'][0]['name'] == 'room'
            async with session.post(f'{urls[0]}/close') as response:
                assert response.status == 200
        await task

    asyncio.run(check())
