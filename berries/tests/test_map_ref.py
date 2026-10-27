"""Stored map references resolve to SID/side keys without changing protocol fields."""

import pytest
from lxml import etree

from berries.game.levels import LevelSide, MapKey
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.progress.map_ref import MapRef
from berries.game.vanilla import VANILLA_SIDS


@pytest.mark.parametrize('side', list(LevelSide))
def test_explicit_sid_is_preferred_and_xml_names_are_unchanged(side: LevelSide) -> None:
    ref = MapRef(level_id=99, side=side, level_sid='Pack/Map')
    key: MapKey = ('Pack/Map', side)
    assert ref.resolve_key() == key
    assert ref.resolve_key(vanilla=True) == key
    node = ref.to_xml('LastArea_Safe')
    assert dict(node.attrib) == {
        'ID': '99',
        'Mode': ('Normal', 'BSide', 'CSide')[side],
        'SID': 'Pack/Map',
    }
    restored = MapRef.from_xml(node)
    assert restored == ref
    assert restored.level_id == 99 and restored.level_sid == 'Pack/Map'


@pytest.mark.parametrize('level_id', (-1, len(VANILLA_SIDS), 999))
def test_unknown_numeric_id_does_not_invent_a_key(level_id: int) -> None:
    ref = MapRef(level_id=level_id)
    assert ref.resolve_key() is None
    assert ref.resolve_key(vanilla=True) is None


def test_numeric_resolution_requires_vanilla_context_and_keeps_sid_absent() -> None:
    ref = MapRef(level_id=7, side=LevelSide.C)
    assert ref.resolve_key() is None
    assert ref.resolve_key(vanilla=True) == (VANILLA_SIDS[7], LevelSide.C)
    assert 'SID' not in ref.to_xml().attrib
    assert MapRef.from_xml(etree.fromstring(b'<Area ID="7" Mode="CSide"/>')) == ref


def test_main_last_map_resolves_original_game_reference() -> None:
    data = MainSaveData()
    data.last_map_ref = MapRef(level_id=7, side=LevelSide.C)
    assert data.last_map == (VANILLA_SIDS[7], LevelSide.C)
