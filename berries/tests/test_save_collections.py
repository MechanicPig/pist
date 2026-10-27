"""Reference and identity contracts of session and room-statistics collections."""

from copy import copy, deepcopy
from operator import attrgetter

import pytest

from berries.containers import KeyedDict
from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.progress import Session
from berries.game.saves.rooms import RoomStats, room_key
from test_support.save_docs import parsed_session


def test_room_protocol_keys_are_not_decoded() -> None:
    stats = RoomStats(room_deaths={'room': 0})
    records = {'Map_B': stats}
    assert records['Map_B'] is stats
    assert records[room_key('Map', LevelSide.B)] is stats
    assert records.get(room_key('Map_B', LevelSide.A)) is stats
    assert records.get(room_key('Map', LevelSide.A)) is None
    assert room_key('Map', LevelSide.B) in records
    assert 'Map_B' in records
    assert 'map_B' not in records
    assert records.get('Map-B') is None


def test_room_records_preserve_references_and_order() -> None:
    first, second, replacement = RoomStats(), RoomStats(), RoomStats()
    records = {'First': first, 'Second': second}
    records['First'] = replacement
    assert list(records.keys()) == ['First', 'Second']
    assert list(records.items()) == [('First', replacement), ('Second', second)]
    assert list(records) == ['First', 'Second']
    del records['First']
    records['First'] = first
    assert list(records.keys()) == ['Second', 'First']
    first.room_deaths['room'] = 9
    assert records['First'].room_deaths == {'room': 9}
    records.clear()
    assert len(records) == 0
    assert first.room_deaths == {'room': 9}


def test_room_sid_overloads_share_the_same_namespace() -> None:
    records: dict[str, RoomStats] = {}
    first, second = RoomStats(), RoomStats()
    records[room_key('Pack/Map', LevelSide.C)] = first
    records['Pack/Map_C'] = second
    assert records[room_key('Pack/Map', LevelSide.C)] is second
    del records[room_key('Pack/Map', LevelSide.C)]
    assert len(records) == 0
    with pytest.raises(KeyError):
        del records['Missing']
    with pytest.raises(KeyError):
        records['Missing']
    assert len(records) == 0


def test_room_stats_partial_fields_copy_and_clear() -> None:
    stats = RoomStats(
        room_order=['first'],
        room_deaths={'deaths_only': 0},
        room_times={'timer_only': Duration.from_dotnet_ticks(-1)},
        room_rta_times={'rta_only': Duration.from_seconds(2)},
        room_strawberries={'berries_only': 1},
        pause_flags={'Level_Invalid': True},
    )
    copied = deepcopy(stats)
    deaths = stats.room_deaths
    stats.reset()
    assert stats == RoomStats()
    assert deaths is stats.room_deaths and deaths == {}
    assert copied.room_deaths == {'deaths_only': 0}
    assert copied.room_times == {'timer_only': Duration.from_dotnet_ticks(-1)}
    assert copied.pause_flags == {'Level_Invalid': True}
    assert copied.room_order == ['first']
    assert RoomStats().room_order is not RoomStats().room_order


def test_room_stats_shallow_copy_shares_lists_and_mappings() -> None:
    original = RoomStats(room_order=['start'], room_deaths={'start': 2})
    copied = copy(original)
    assert copied is not original
    assert copied.room_order is original.room_order
    assert copied.room_deaths is original.room_deaths
    copied.room_order.append('end')
    assert original.room_order == ['start', 'end']
    copied.room_order = ['separate']
    assert original.room_order == ['start', 'end']


def test_room_records_deepcopy_retains_internal_aliases_only() -> None:
    stats = RoomStats(room_order=['start'])
    records = {'One': stats, 'Two': stats}
    copied = deepcopy(records)
    assert copied['One'] is copied['Two']
    assert copied['One'] is not stats
    copied['One'].room_order.append('end')
    assert stats.room_order == ['start']


def test_session_mapping_is_sid_unique_and_side_matching_is_explicit() -> None:
    session = parsed_session('Pack/Map', LevelSide.B)
    sessions: KeyedDict[str, Session] = KeyedDict([session], key=attrgetter('sid'))
    assert sessions['Pack/Map'] is session
    assert sessions['Pack/Map'].side is LevelSide.B
    assert 'Pack/Map' in sessions and sessions.get('Missing') is None
    replacement = parsed_session('Pack/Map', LevelSide.A)
    sessions.add(replacement)
    assert sessions['Pack/Map'] is replacement and len(sessions) == 1
    assert list(sessions) == ['Pack/Map']
    assert list(sessions.values()) == [replacement]


def test_session_replacement_removal_and_clear_do_not_invalidate_objects() -> None:
    first = parsed_session('One', LevelSide.A)
    second = parsed_session('Two', LevelSide.C)
    sessions: KeyedDict[str, Session] = KeyedDict([first, second], key=attrgetter('sid'))
    replacement = copy(first)
    replacement.room = 'end'
    sessions.replace(replacement)
    assert list(sessions.values()) == [replacement, second]
    assert first.room == ''
    del sessions['One']
    with pytest.raises(KeyError):
        sessions['One']
    sessions.clear()
    assert len(sessions) == 0
    assert replacement.room == 'end'
