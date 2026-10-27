"""Main-save equality follows stored values, not file coordination state."""

from copy import copy, deepcopy

import pytest
from lxml import etree

from berries.game.levels import LevelSide
from berries.game.saves.files.main import MainSave
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from test_support.save_docs import normal_save_xml, parsed_session, require_map


@pytest.mark.parametrize('save_type', (MainSaveData, MainSave))
def test_main_data_compares_independent_values_and_is_unhashable(
    save_type: type[MainSaveData],
) -> None:
    value = save_type()
    assert value == save_type() == deepcopy(value) == copy(value)
    assert value != object()
    with pytest.raises(TypeError):
        hash(value)


@pytest.mark.parametrize(
    ('name', 'replacement'),
    [('name', 'Other'), ('deaths', 1), ('flags', {'flag'}), ('summit_gems', [True] * 6)],
)
def test_field_edits_change_equality_and_can_be_reverted(name: str, replacement: object) -> None:
    value = MainSaveData()
    other = deepcopy(value)
    original = getattr(other, name)
    setattr(other, name, replacement)
    assert value != other
    setattr(other, name, original)
    assert value == other


def test_nested_progress_and_assist_values_participate() -> None:
    value = MainSaveData()
    value.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    other = deepcopy(value)
    assert value == other
    vanilla_map = require_map(other.vanilla.levels['Celeste/0-Intro'].maps[0])
    vanilla_map.deaths = 1
    assert value != other
    vanilla_map.deaths = 0
    assert value == other
    mod_map = require_map(other.mods.campaigns['Pack'].levels['Pack/Map'].maps[0])
    mod_map.deaths = 1
    assert value != other
    mod_map.deaths = 0
    assert value == other
    other.assists.game_speed = 8
    assert value != other


def test_safe_session_values_compare_without_edit_baseline() -> None:
    value = MainSaveData()
    other = MainSaveData()
    for data in (value, other):
        data.session = parsed_session('Pack/Map', LevelSide.A)
        assert data.session is not None
        data.session.dark_room_alpha = 0.5
    assert value == other
    assert other.session is not None
    other.session.deaths += 1
    assert value != other
    other.session.deaths -= 1
    assert value == other


def test_legacy_session_is_compared_even_when_safe_session_is_selected() -> None:
    legacy = parsed_session('Celeste/1-ForsakenCity', LevelSide.A)
    root = etree.fromstring(normal_save_xml('<SaveData/>'))
    node = etree.fromstring(legacy.tostring())
    node.tag = 'CurrentSession'
    root.append(node)
    safe = etree.fromstring(parsed_session('Pack/Map', LevelSide.A).tostring())
    safe.tag = 'CurrentSession_Safe'
    root.append(safe)
    value = MainSave.fromstring(etree.tostring(root))
    other = deepcopy(value)
    assert value == other
    # Different legacy data must remain visible even behind identical safe data.
    node.set('Deaths', '1')
    changed = MainSave.fromstring(etree.tostring(root))
    assert changed != other


def test_recovery_marker_participates_but_read_policy_does_not() -> None:
    source = normal_save_xml('<SaveData/>')
    value = MainSave.fromstring(source, strict=True)
    other = MainSave.fromstring(source, strict=False, unknown='warn')
    assert value == other
    other.install_mod_progress(deepcopy(value).mods)
    assert value != other


def test_file_edit_baseline_is_not_owned_by_data_or_used_for_equality() -> None:
    assert not hasattr(MainSaveData(), '_mods_baseline')
    value = MainSave.fromstring(
        normal_save_xml(
            '<SaveData><LevelSets><LevelSetStats Name="Pack"><Areas/><Poem/>'
            '</LevelSetStats></LevelSets></SaveData>'
        )
    )
    other = MainSave.fromstring(normal_save_xml('<SaveData/>'))
    other.mods.campaigns.add(CampaignProgress('Pack'))
    assert value == other
    assert etree.fromstring(value.tostring()).findtext('HasModdedSaveData') == 'false'
    for clone in (other, copy(other), deepcopy(other)):
        assert clone == value
        assert etree.fromstring(clone.tostring()).findtext('HasModdedSaveData') == 'true'
