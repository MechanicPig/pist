from pathlib import Path
from struct import pack

from berries.entities.rules import EntityRules
from berries.game.binmap import BinElement, BinMap
from berries.game.content import ContentPath
from berries.game.levels import Map
from berries.game.maps import MapInfo
from berries.map_entrances import MapEntranceSource, load_map_entrance_rule_layers
from berries.map_layout import (
    MapEntrance,
    MapPreviewEntity,
    load_map_layout,
    map_layout,
)
from test_support.mod_factory import make_installed_mod


def _varlen(value: int) -> bytes:
    parts = bytearray()
    while value > 0x7F:
        parts.append(value % 0x80 + 0x80)
        value //= 0x80
    parts.append(value)
    return bytes(parts)


def _string(value: str) -> bytes:
    encoded = value.encode()
    return _varlen(len(encoded)) + encoded


def test_map_layout_keeps_rooms_without_complete_preview_bounds() -> None:
    map_data = BinMap(
        package='Author/Pack/Map',
        root=BinElement(
            'Map',
            {},
            (
                BinElement(
                    'levels',
                    {},
                    (
                        BinElement(
                            'level',
                            {'name': 'start', 'x': 8, 'y': 16, 'width': 320, 'height': 184},
                            (),
                        ),
                        BinElement('level', {'name': 'secret'}, ()),
                    ),
                ),
            ),
        ),
    )

    layout = map_layout(map_data)

    assert layout.room_names == {'start', 'secret'}
    assert layout.rooms[0].has_bounds
    assert (
        layout.rooms[0].x,
        layout.rooms[0].y,
        layout.rooms[0].width,
        layout.rooms[0].height,
    ) == (
        8,
        16,
        320,
        184,
    )
    assert not layout.rooms[1].has_bounds


def test_map_layout_extracts_tiles_and_configured_entity_markers() -> None:
    map_data = BinMap(
        package='Author/Pack/Map',
        root=BinElement(
            'Map',
            {},
            (
                BinElement(
                    'levels',
                    {},
                    (
                        BinElement(
                            'level',
                            {'name': 'start', 'x': 0, 'y': 0, 'width': 24, 'height': 16},
                            (
                                BinElement('bg', {'innerText': '010\n111'}, ()),
                                BinElement('solids', {'innerText': '100\n001'}, ()),
                                BinElement(
                                    'entities',
                                    {},
                                    (BinElement('strawberry', {'x': 8, 'y': 8}, ()),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    room = map_layout(map_data).rooms[0]

    assert room.background == ('010', '111')
    assert room.solids == ('100', '001')
    assert room.entities == (
        MapPreviewEntity(
            8,
            8,
            'strawberry',
            'strawberry.png',
            None,
            'strawberry',
            {'x': 8, 'y': 8},
        ),
    )


def test_map_layout_uses_rules_supplied_when_the_map_is_opened() -> None:
    map_data = BinMap(
        package='Author/Pack/Map',
        root=BinElement(
            'Map',
            {},
            (
                BinElement(
                    'levels',
                    {},
                    (
                        BinElement(
                            'level',
                            {'name': 'start', 'x': 0, 'y': 0, 'width': 8, 'height': 8},
                            (
                                BinElement(
                                    'entities',
                                    {},
                                    (BinElement('Example/Seed', {'x': 4, 'y': 4}, ()),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    rules = EntityRules.model_validate(
        {
            'kinds': {'seed': {'label': 'Seed', 'sprite': 'seed.png'}},
            'entities': {'Example/Seed': {'rules': [{'kind': 'seed'}]}},
        }
    )

    room = map_layout(map_data, entity_rules=rules).rooms[0]

    assert room.entities == (
        MapPreviewEntity(
            4,
            4,
            'seed',
            'seed.png',
            None,
            'Example/Seed',
            {'x': 4, 'y': 4},
        ),
    )


def test_map_layout_extracts_collab_route_links() -> None:
    map_data = BinMap(
        package='Author/Pack/Map',
        root=BinElement(
            'Map',
            {},
            (
                BinElement(
                    'levels',
                    {},
                    (
                        BinElement(
                            'level',
                            {'name': 'lobby'},
                            (
                                BinElement(
                                    'entities',
                                    {},
                                    (
                                        BinElement('strawberry', {'x': 8, 'y': 8}, ()),
                                        BinElement(
                                            'SJ2021/StrawberryJamJar',
                                            {'map': 'Author/Pack/SmallMap', 'x': 160, 'y': 40},
                                            (),
                                        ),
                                    ),
                                ),
                                BinElement(
                                    'triggers',
                                    {},
                                    (
                                        BinElement(
                                            'CollabUtils2/ChapterPanelTrigger',
                                            {
                                                'map': 'Author/Pack/Target',
                                                'x': 80,
                                                'y': 40,
                                                'width': 32,
                                                'height': 24,
                                            },
                                            (),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    assert map_layout(map_data).entrances == (
        MapEntrance(
            'lobby',
            'Author/Pack/SmallMap',
            136,
            8,
            48,
            32,
            MapEntranceSource.ENTITY,
            'SJ2021/StrawberryJamJar',
            {'map': 'Author/Pack/SmallMap', 'x': 160, 'y': 40},
        ),
        MapEntrance(
            'lobby',
            'Author/Pack/Target',
            80,
            40,
            32,
            24,
            MapEntranceSource.TRIGGER,
            'CollabUtils2/ChapterPanelTrigger',
            {
                'map': 'Author/Pack/Target',
                'x': 80,
                'y': 40,
                'width': 32,
                'height': 24,
            },
        ),
    )


def test_map_entrance_rules_allow_local_rule_overrides(tmp_path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[[rules]]
source = 'entity'
name = 'Test/Entrance'

[rules.region]
x = { attr = 'x' }
y = { attr = 'y' }
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[[rules]]
source = 'entity'
name = 'Test/Entrance'

[rules.region]
x = { attr = 'x', offset = -8 }
y = { attr = 'y' }
width = { value = 16 }
height = { value = 16 }
""",
        encoding='utf-8',
    )

    rules = load_map_entrance_rule_layers(shared_path, local_path)

    assert len(rules.rules) == 1
    rule = rules.rules[0]
    assert rule.region.x.resolve({'x': 24}) == 16
    assert rule.region.width is not None
    assert rule.region.width.resolve({}) == 16


def test_load_map_layout_reads_an_active_mod_map(tmp_path: Path) -> None:
    lookup = ('Map', 'levels', 'level', 'name')

    def element(name: str, attrs: list[bytes], children: list[bytes]) -> bytes:
        return b''.join(
            (
                pack('<H', lookup.index(name)),
                pack('<B', len(attrs)),
                *attrs,
                pack('<H', len(children)),
                *children,
            )
        )

    room_name = pack('<H', lookup.index('name')) + b'\x06' + _string('start')
    root = element('Map', [], [element('levels', [], [element('level', [room_name], [])])])
    data = b''.join((_string('CELESTE MAP'), _string('Author/Pack/Map'), pack('<H', len(lookup))))
    data += b''.join(map(_string, lookup)) + root + b'trailing payload'
    mod_dir = tmp_path / 'Mod'
    map_path = mod_dir / 'Maps' / 'Author' / 'Pack' / 'Map.bin'
    map_path.parent.mkdir(parents=True)
    map_path.write_bytes(data)
    mod = make_installed_mod(
        source='directory',
        filename='Mod',
        path=str(mod_dir),
        metadata_name='Mod',
        metadata_version=None,
    )

    layout = load_map_layout(Map(MapInfo(file_path=ContentPath('Maps/Author/Pack/Map.bin')), mod))

    assert [room.name for room in layout.rooms] == ['start']
