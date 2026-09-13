from pathlib import Path
from struct import pack

import pytest

from pist.game.binmap import BinElement, BinMap
from pist.game.entities import EntityRules, EntityStat, load_entity_rule_layers
from pist.game.map_entrances import load_map_entrance_rule_layers
from pist.game.routes import (
    EndersBlenderReader,
    MapLink,
    MapMarker,
    MapRoute,
    load_map_layout,
    map_entity_table_values,
    map_layout,
)
from pist.local_data import LocalDataStore
from pist.models import InstalledMod, LocalMap
from pist.time import Time


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
    assert room.markers == (
        MapMarker(
            8,
            8,
            'strawberry',
            'strawberry.png',
            'start\x1fstrawberry\x1fNone\x1f8\x1f8',
            'strawberry',
            {'x': 8, 'y': 8},
            summary_kind='strawberry',
            summary_stat=EntityStat.COUNT,
            summary_label='草莓',
        ),
    )


def test_map_entity_table_values_apply_saved_marker_exclusions() -> None:
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
                            {'name': 'room'},
                            (
                                BinElement(
                                    'entities',
                                    {},
                                    (
                                        BinElement('strawberry', {'id': 1, 'x': 8, 'y': 8}, ()),
                                        BinElement('cassette', {'id': 2, 'x': 16, 'y': 8}, ()),
                                        BinElement('heartGem', {'id': 3, 'x': 24, 'y': 8}, ()),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    values = map_entity_table_values(
        map_data,
        excluded_markers=frozenset({'room\x1fstrawberry\x1f1\x1f8\x1f8'}),
        entity_rules=load_entity_rule_layers().with_rule('heartGem', 'end_level_heart', {}),
    )

    assert values == {'主表': {'红草莓数': 0, '月莓数': 0, '磁带': True, '水晶之心': '通关收集'}}


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

    assert room.markers == (
        MapMarker(
            4,
            4,
            'seed',
            'seed.png',
            'start\x1fExample/Seed\x1fNone\x1f4\x1f4',
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

    assert map_layout(map_data).links == (
        MapLink('lobby', 'Author/Pack/SmallMap', 136, 8, 48, 32),
        MapLink('lobby', 'Author/Pack/Target', 80, 40, 32, 24),
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


def test_enders_blender_reader_loads_first_clear_room_order(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '2-modsave-EndersBlender.celeste').write_text(
        """mapDict_roomStat_firstClear_roomOrder:
  Author/Pack/Map_B:
  - start
  - middle
  - goal
  Author/Pack/Numbers:
  - 0
  - 1
mapDict_roomStat_firstClear_death:
  Author/Pack/Map_B:
    start: 4
mapDict_roomStat_firstClear_timer:
  Author/Pack/Map_B:
    start: 25500000
""",
        encoding='utf-8',
    )

    reader = EndersBlenderReader(tmp_path / 'Celeste')
    save = reader.load(2)

    assert reader.available_numbers() == [2]
    assert save.first_clear_room_order(
        LocalMap(file_path='Maps/Author/Pack/Map-B.bin', dialog_key='Map', side='B')
    ) == ('start', 'middle', 'goal')
    assert (
        save.first_clear_room_order(
            LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Map')
        )
        == ()
    )
    assert save.first_clear_room_order(
        LocalMap(file_path='Maps/Author/Pack/Numbers.bin', dialog_key='Numbers')
    ) == ('0', '1')
    map_info = LocalMap(file_path='Maps/Author/Pack/Map-B.bin', dialog_key='Map', side='B')
    assert save.first_clear_room_death(map_info, 'start') == 4
    assert save.first_clear_room_time(map_info, 'start') == Time(2550)


def test_enders_blender_reader_rejects_invalid_room_orders(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0-modsave-EndersBlender.celeste').write_text(
        'mapDict_roomStat_firstClear_roomOrder: invalid\n', encoding='utf-8'
    )

    with pytest.raises(ValueError, match='room orders'):
        EndersBlenderReader(tmp_path / 'Celeste').load(0)


def test_load_map_layout_reads_a_map_from_an_installed_mod_directory(tmp_path: Path) -> None:
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
    mod = InstalledMod(
        source='directory',
        filename='Mod',
        path=str(mod_dir),
        metadata_name='Mod',
        metadata_version=None,
    )

    layout = load_map_layout(
        mod, LocalMap(file_path='Maps/Author/Pack/Map.bin', dialog_key='Author_Pack_Map')
    )

    assert [room.name for room in layout.rooms] == ['start']


def test_local_data_store_round_trips_one_confirmed_map_route(tmp_path: Path) -> None:
    store = LocalDataStore(tmp_path / 'local-data.sqlite3')
    route = MapRoute(map_file='Maps/Author/Pack/Map.bin', rooms=('start', 'goal'))

    store.save_route(route)

    assert store.load_route(route.map_file) == route
    assert route.room_count == 2


def test_map_route_counts_editor_rooms_by_explicit_in_game_room_count() -> None:
    route = MapRoute(
        map_file='Maps/Author/Pack/Map.bin',
        rooms=('start', 'middle', 'goal'),
        room_counts={'middle': 3},
    )

    assert route.room_count == 5
