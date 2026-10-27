"""Exercise explicit interpretation rules through file reads and multi-map HTTP navigation."""

import asyncio
from pathlib import Path
from struct import pack

import pytest
from aiohttp import ClientSession

from berries.entities.rules import EntityRules
from berries.game.binmap import ValueTag
from berries.game.content import ContentPath
from berries.game.levels import Level, LevelSide
from berries.game.maps import VanillaMap
from berries.map_entrances import MapEntranceRules
from berries.map_layout import MapLayout
from berries.map_preview import MapPreview
from test_support.preview import preview_url


def _map_bytes(target: str) -> bytes:
    lookup: dict[str, int] = {}

    def string(value: str) -> bytes:
        encoded = value.encode('utf-8')
        assert len(encoded) < 128
        return bytes([len(encoded)]) + encoded

    def key(value: str) -> bytes:
        return pack('<H', lookup.setdefault(value, len(lookup)))

    def element(name: str, attrs: dict[str, str | int], children: tuple[bytes, ...]) -> bytes:
        header = key(name) + bytes([len(attrs)])
        for attr_name, value in attrs.items():
            header += key(attr_name)
            header += (
                bytes([ValueTag.INT]) + pack('<i', value)
                if isinstance(value, int)
                else bytes([ValueTag.STRING]) + string(value)
            )
        return header + pack('<H', len(children)) + b''.join(children)

    berry = element('Example/Berry', {'id': 1, 'x': 8, 'y': 16}, ())
    exit_entity = element('Example/Exit', {'map': target, 'x': 24, 'y': 16}, ())
    entities = element('entities', {}, (berry, exit_entity))
    room = element(
        'level', {'name': 'start', 'x': 0, 'y': 0, 'width': 64, 'height': 64}, (entities,)
    )
    root = element('Map', {}, (element('levels', {}, (room,)),))
    return (
        string('CELESTE MAP')
        + string('Example')
        + pack('<H', len(lookup))
        + b''.join(string(value) for value in lookup)
        + root
    )


def test_explicit_rules_survive_file_loading_and_two_navigation_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = {}
    for name, target in (('A', 'Example/B'), ('B', 'Example/C'), ('C', 'Example/A')):
        path = ContentPath(f'Maps/Example/{name}.bin')
        physical = tmp_path / path
        physical.parent.mkdir(parents=True, exist_ok=True)
        physical.write_bytes(_map_bytes(target))
        maps[f'Example/{name}'] = VanillaMap(path, tmp_path)
    entity_rules = EntityRules.model_validate(
        {
            'kinds': {'custom': {'label': 'Custom'}},
            'entities': {'Example/Berry': {'rules': [{'kind': 'custom'}]}},
        }
    )
    entrances = MapEntranceRules.model_validate(
        {
            'rules': [
                {
                    'source': 'entity',
                    'name': 'Example/Exit',
                    'region': {'x': {'attr': 'x'}, 'y': {'attr': 'y'}},
                }
            ]
        }
    )

    def reject_defaults() -> None:
        pytest.fail('Explicit interpretation must not read default local rules')

    monkeypatch.setattr('berries.map_layout.config.load_default_entity_rules', reject_defaults)
    monkeypatch.setattr('berries.map_layout.load_default_map_entrance_rules', reject_defaults)
    first = maps['Example/A']
    layout = MapLayout.from_map(first, entity_rules=entity_rules, entrance_rules=entrances)
    assert layout == MapLayout.from_path(
        tmp_path, first.file_path, entity_rules=entity_rules, entrance_rules=entrances
    )
    assert layout.rooms[0].entities[0].kind == 'custom'
    assert layout.entrances[0].target_sid == 'Example/B'
    preview = MapPreview(
        first,
        layout,
        entity_rules=entity_rules,
        entrance_rules=entrances,
        routable_maps={
            sid: (Level(sid, (loaded_map,)), LevelSide.A) for sid, loaded_map in maps.items()
        },
    )
    urls: list[str] = []
    monkeypatch.setattr(
        'berries.map_preview.session.webbrowser.open', lambda url: urls.append(url) or True
    )

    async def check() -> None:
        task = asyncio.create_task(preview.preview())
        async with preview_url(task, urls) as url, ClientSession() as client:
            for sid, target in (('Example/B', 'Example/C'), ('Example/C', 'Example/A')):
                async with client.post(f'{url}/open', json={'targetSid': sid}) as response:
                    assert response.status == 200
                    state = await response.json()
                    assert state['rooms'][0]['entities'][0]['kind'] == 'custom'
                    assert state['entrances'][0]['targetSid'] == target
            async with client.post(f'{url}/close') as response:
                assert response.status == 200
            await task

    asyncio.run(check())
