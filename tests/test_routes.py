from pathlib import Path
from struct import pack

import pytest
from pydantic import ValidationError

from pist.entities.classification import map_entity_stats
from pist.entities.rules import EntityRules, EntityStat, load_entity_rule_layers
from pist.game.binmap import BinElement, BinMap
from pist.game.duration import Duration
from pist.game.levels import LoadedModMap
from pist.game.maps import MapInfo
from pist.game.routes import (
    EndersBlenderReader,
    MapEntrance,
    MapPreviewEntity,
    MapRoute,
    load_loaded_map_layout,
    map_layout,
)
from pist.local_data import LocalDataStore
from pist.map_entrances import MapEntranceSource, load_map_entrance_rule_layers
from tests.map_factory import make_level_side
from tests.mod_factory import make_installed_mod


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
            summary_kind='strawberry',
            summary_stat=EntityStat.COUNT,
            summary_label='草莓',
        ),
    )


def test_map_entity_record_values_apply_saved_marker_exclusions() -> None:
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

    values = map_entity_stats(
        map_data,
        excluded_entities=frozenset({'room:1'}),
        rule_set=load_entity_rule_layers().with_rule('heartGem', 'end_level_heart', {}),
    ).record_values

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
        *make_level_side(file_path='Maps/Author/Pack/Map-B.bin')
    ) == ('start', 'middle', 'goal')
    assert save.first_clear_room_order(*make_level_side(file_path='Maps/Author/Pack/Map.bin')) == ()
    assert save.first_clear_room_order(
        *make_level_side(file_path='Maps/Author/Pack/Numbers.bin')
    ) == ('0', '1')
    level, side = make_level_side(file_path='Maps/Author/Pack/Map-B.bin')
    assert save.first_clear_room_death(level, side, 'start') == 4
    assert save.first_clear_room_time(level, side, 'start') == Duration.from_milliseconds(2550)


def test_enders_blender_reader_rejects_invalid_room_orders(tmp_path: Path) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0-modsave-EndersBlender.celeste').write_text(
        'mapDict_roomStat_firstClear_roomOrder: invalid\n', encoding='utf-8'
    )

    with pytest.raises(ValueError, match='room orders'):
        EndersBlenderReader(tmp_path / 'Celeste').load(0)


@pytest.mark.parametrize(
    ('field', 'value', 'label', 'reason'),
    (
        ('mapDict_roomStat_firstClear_death', '1.0', 'room deaths', 'decimal integer'),
        ('mapDict_roomStat_firstClear_timer', '1', 'room timers', 'whole milliseconds'),
    ),
)
def test_enders_blender_reader_preserves_invalid_value_locations(
    tmp_path: Path,
    field: str,
    value: str,
    label: str,
    reason: str,
) -> None:
    saves_dir = tmp_path / 'Celeste' / 'Saves'
    saves_dir.mkdir(parents=True)
    (saves_dir / '0-modsave-EndersBlender.celeste').write_text(
        f"""{field}:
  Author/Pack/Map_B:
    start: {value}
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError) as info:
        EndersBlenderReader(tmp_path / 'Celeste').load(0)

    message = str(info.value)
    assert label in message
    assert 'Author/Pack/Map_B' in message
    assert 'start' in message
    assert reason in message
    assert isinstance(info.value.__cause__, ValidationError)


def test_load_loaded_map_layout_reads_an_active_mod_map(tmp_path: Path) -> None:
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

    layout = load_loaded_map_layout(
        LoadedModMap(MapInfo(file_path='Maps/Author/Pack/Map.bin'), mod)
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


def test_map_route_rejects_non_positive_explicit_room_count() -> None:
    with pytest.raises(ValueError):
        MapRoute(
            map_file='Maps/Author/Pack/Map.bin',
            rooms=('start',),
            room_counts={'start': 0},
        )
