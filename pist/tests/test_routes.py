from pathlib import Path

import pytest

from berries.entities.rules import SHARED_ENTITIES_PATH, load_entity_rules
from berries.game.binmap import BinElement, BinMap
from pist.entity_stats import load_entity_stat_rules, map_entity_stats
from pist.route_store import RouteStore
from pist.routes import MapRoute


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

    entity_rules = load_entity_rules(SHARED_ENTITIES_PATH).with_rule(
        'heartGem', 'end_level_heart', {}
    )
    values = map_entity_stats(
        map_data,
        excluded_entities=frozenset({'room:1'}),
        entity_rules=entity_rules,
        stat_rules=load_entity_stat_rules(entity_rules),
    ).record_values

    assert values == {'主表': {'红草莓数': 0, '月莓数': 0, '磁带': True, '水晶之心': '通关收集'}}


def test_route_store_round_trips_one_confirmed_map_route(tmp_path: Path) -> None:
    store = RouteStore(tmp_path / 'local-data.sqlite3')
    route = MapRoute(map_file='Maps/Author/Pack/Map.bin', rooms=('start', 'goal'))

    store.save(route)

    assert store.load(route.map_file) == route
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
