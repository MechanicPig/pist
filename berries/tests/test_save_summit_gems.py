"""Persistent gem scope is explicit; current-session collections stay separate."""

from io import StringIO
from pathlib import Path

import pytest
import yaml
from lxml import etree

from berries.game.levels import LevelSide
from berries.game.saves import operations as ops
from berries.game.saves.codecs.xml import tree
from berries.game.saves.errors import BadProgressSave, ProgressConflictError
from berries.game.saves.files import CommunalHelperSave, MainSave
from berries.game.saves.progress import CampaignProgress, LevelProgress, Session
from berries.game.saves.slot import SaveSlot
from berries.game.saves.unknown import UnknownSaveFieldWarning
from test_support.save_docs import parsed_session

_SID = 'Celeste/7-Summit'
_GEM = 'Pack/Map/room/12'
_OTHER = 'Pack/Map/room/13'
_UNRELATED = 'Elsewhere/Map/room/7'
_IDS: ops.CustomSummitGemIDs = {('Pack/Map', LevelSide.A): {_GEM, _OTHER}}


def test_main_nullable_array_and_mutable_values_roundtrip() -> None:
    save = MainSave()
    assert save.summit_gems is None
    assert etree.fromstring(save.tostring()).find('SummitGems') is None
    save.summit_gems = [True, False, True, False, False, True]
    encoded = save.tostring()
    root = etree.fromstring(encoded)
    assert root.xpath('SummitGems/boolean/text()') == [
        'true',
        'false',
        'true',
        'false',
        'false',
        'true',
    ]
    assert MainSave.fromstring(encoded).summit_gems == save.summit_gems
    save.summit_gems = None
    assert etree.fromstring(save.tostring()).find('SummitGems') is None


@pytest.mark.parametrize('values', [[], [True], [False] * 7])
def test_malformed_lengths_allow_lenient_inspection_but_not_writing(values: list[bool]) -> None:
    root = etree.fromstring(MainSave().tostring())
    node = etree.SubElement(root, 'SummitGems')
    for value in values:
        etree.SubElement(node, 'boolean').text = str(value).lower()
    data = etree.tostring(root)
    with pytest.raises(BadProgressSave):
        MainSave.fromstring(data)
    save = MainSave.fromstring(data, strict=False)
    assert save.summit_gems == values
    with pytest.raises(ValueError):
        save.tostring()
    save.summit_gems = [False] * 6
    assert MainSave.fromstring(save.tostring()).summit_gems == [False] * 6


def test_session_gems_track_in_place_changes_without_changing_main_history() -> None:
    session = parsed_session(_SID, LevelSide.A)
    assert session.summit_gems == [False] * 6
    original = session.tostring()
    assert etree.fromstring(original).xpath('SummitGems/boolean/text()') == ['false'] * 6
    session.summit_gems[2] = True
    assert tuple(session.iter_changed_fields()) == ('summit_gems',)
    assert Session.from_xml(tree.parse(session.tostring()).getroot()).summit_gems == [
        False,
        False,
        True,
        False,
        False,
        False,
    ]
    save = MainSave()
    save.session = session
    assert save.summit_gems is None
    reread = MainSave.fromstring(save.tostring())
    assert reread.session is not None and reread.session.summit_gems == session.summit_gems
    assert reread.summit_gems is None
    session.summit_gems[2] = False
    assert not any(session.iter_changed_fields())
    assert session.tostring() == original


@pytest.mark.parametrize('kind', ['missing', 'nil', 'empty'])
def test_session_gem_defaults_distinguish_missing_nil_and_empty(kind: str) -> None:
    root = parsed_session(_SID, LevelSide.A).to_xml()
    node = root.find('SummitGems')
    assert node is not None
    if kind == 'missing':
        root.remove(node)
    elif kind == 'nil':
        node.set('{http://www.w3.org/2001/XMLSchema-instance}nil', 'true')
    else:
        node.clear()
    session = Session.from_xml(root)
    assert session.summit_gems == ([] if kind == 'empty' else [False] * 6)
    if kind == 'empty':
        with pytest.raises(BadProgressSave):
            Session.from_xml(root, strict=True)
    else:
        other = Session.from_xml(root, strict=True)
        session.summit_gems[0] = True
        assert other.summit_gems == [False] * 6


def test_custom_keys_preserve_scalars_and_null_and_warn_before_dropping_unknowns() -> None:
    with pytest.warns(UnknownSaveFieldWarning, match='CommunalHelperSave.Extra'):
        save = CommunalHelperSave.fromstring(
            'SummitGems: &gems ["001", "true", "Pack/Map/r/-1"]\nExtra: *gems\n',
            unknown='warn',
        )
    assert save.summit_gems == {'001', 'true', 'Pack/Map/r/-1'}
    assert save.summit_gems is not None
    save.summit_gems.add(_GEM)
    data = yaml.safe_load(save.tostring())
    assert set(data['SummitGems']) == save.summit_gems
    assert 'Extra' not in data
    assert CommunalHelperSave.fromstring('{}').summit_gems is None
    assert CommunalHelperSave.fromstring('SummitGems: null').summit_gems is None
    empty = CommunalHelperSave.fromstring('SummitGems: []')
    assert empty.summit_gems == set()
    assert CommunalHelperSave.fromstring(empty.tostring()).summit_gems == set()
    stream = StringIO()
    save.write(stream)
    assert CommunalHelperSave.fromstring(stream.getvalue()).summit_gems == save.summit_gems


def test_slot_loads_custom_save(tmp_path: Path) -> None:
    (tmp_path / '0-modsave-CommunalHelper.celeste').write_text('SummitGems: []', encoding='utf-8')
    assert SaveSlot(tmp_path, 0).mod(CommunalHelperSave).summit_gems == set()


@pytest.mark.parametrize('scope', ['map', 'level', 'campaign'])
@pytest.mark.parametrize('mode', ['merge', 'overwrite', 'exclusive'])
@pytest.mark.parametrize('move', [False, True])
def test_vanilla_transfers_link_only_the_summit_a_history(
    scope: str, mode: ops.TransferMode, move: bool
) -> None:
    source, target = MainSave(), MainSave()
    source.summit_gems = [True, False, False, False, False, False]
    target.summit_gems = [False, mode != 'exclusive', False, False, False, False]
    if scope == 'map':
        action = ops.move_map_progress if move else ops.copy_map_progress
        action(source, target, _SID, LevelSide.A, poem_id=None, mode=mode, goldenberry_ids={})
    elif scope == 'level':
        action = ops.move_level_progress if move else ops.copy_level_progress
        action(source, target, _SID, poem_ids={}, mode=mode, goldenberry_ids={})
    else:
        action = ops.move_campaign_progress if move else ops.copy_campaign_progress
        action(source, target, 'Celeste', mode=mode, goldenberry_ids={})
    assert target.summit_gems == [True, mode == 'merge', False, False, False, False]
    assert source.summit_gems == [not move, False, False, False, False, False]
    assert MainSave.fromstring(target.tostring()).summit_gems == target.summit_gems


@pytest.mark.parametrize('scope', ['map', 'level', 'campaign'])
@pytest.mark.parametrize('mode', ['merge', 'overwrite', 'exclusive'])
@pytest.mark.parametrize('move', [False, True])
def test_custom_transfers_use_selected_keys_and_preserve_unrelated_history(
    scope: str, mode: ops.TransferMode, move: bool
) -> None:
    source, target = MainSave(), MainSave()
    for save in (source, target):
        save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    source_mod, target_mod = CommunalHelperSave(), CommunalHelperSave()
    source_mod.summit_gems = {_GEM, _UNRELATED}
    target_mod.summit_gems = {_UNRELATED} if mode == 'exclusive' else {_OTHER, _UNRELATED}
    if scope == 'map':
        action = ops.move_map_progress if move else ops.copy_map_progress
        action(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            poem_id=None,
            mode=mode,
            goldenberry_ids={},
            custom_summit_gem_ids=_IDS,
            source_communal=source_mod,
            target_communal=target_mod,
        )
    elif scope == 'level':
        action = ops.move_level_progress if move else ops.copy_level_progress
        action(
            source,
            target,
            'Pack/Map',
            poem_ids={},
            mode=mode,
            goldenberry_ids={},
            custom_summit_gem_ids=_IDS,
            source_communal=source_mod,
            target_communal=target_mod,
        )
    else:
        action = ops.move_campaign_progress if move else ops.copy_campaign_progress
        action(
            source,
            target,
            'Pack',
            mode=mode,
            goldenberry_ids={},
            custom_summit_gem_ids=_IDS,
            source_communal=source_mod,
            target_communal=target_mod,
        )
    expected = {_GEM, _UNRELATED, _OTHER} if mode == 'merge' else {_GEM, _UNRELATED}
    assert target_mod.summit_gems == expected
    assert source_mod.summit_gems == ({_UNRELATED} if move else {_GEM, _UNRELATED})
    assert CommunalHelperSave.fromstring(target_mod.tostring()).summit_gems == expected


def test_exclusive_checks_persistent_gems_before_modifying_either_save() -> None:
    source, target = MainSave(), MainSave()
    target.summit_gems = [True] * 6
    before = source.tostring(), target.tostring()
    with pytest.raises(ProgressConflictError, match='not empty'):
        ops.move_map_progress(
            source, target, _SID, LevelSide.A, poem_id=None, mode='exclusive', goldenberry_ids={}
        )
    assert (source.tostring(), target.tostring()) == before


def test_other_side_does_not_clear_vanilla_gems_and_custom_clear_is_explicit() -> None:
    save = MainSave()
    save.summit_gems = [True] * 6
    ops.clear_map_progress(save, _SID, LevelSide.B, poem_id=None, goldenberry_ids={})
    assert save.summit_gems == [True] * 6
    save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    custom = CommunalHelperSave()
    custom.summit_gems = {_GEM, _UNRELATED}
    ops.clear_map_progress(
        save,
        'Pack/Map',
        LevelSide.A,
        poem_id=None,
        goldenberry_ids={},
        custom_summit_gem_ids=_IDS,
        communal=custom,
    )
    assert custom.summit_gems == {_UNRELATED}
    ops.clear_map_progress(save, _SID, LevelSide.A, poem_id=None, goldenberry_ids={})
    assert save.summit_gems == [False] * 6


def test_queries_include_persistent_gems_in_the_selected_scope() -> None:
    save = MainSave()
    save.summit_gems = [True] * 6
    assert not ops.is_empty_map_progress(save, _SID, LevelSide.A, poem_id=None)
    assert ops.is_empty_map_progress(save, _SID, LevelSide.B, poem_id=None)
    assert not ops.is_empty_level_progress(save, _SID, poem_ids={})
    assert not ops.is_empty_campaign_progress(save, 'Celeste')
    save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    custom = CommunalHelperSave()
    custom.summit_gems = {_GEM}
    assert ops.is_empty_level_progress(save, 'Pack/Map', poem_ids={})
    assert not ops.is_empty_level_progress(
        save,
        'Pack/Map',
        poem_ids={},
        custom_summit_gem_ids=_IDS,
        communal=custom,
    )


def test_whole_campaign_overwrite_removes_only_selected_target_only_gem_keys() -> None:
    source, target = MainSave(), MainSave()
    source.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    target.mods.campaigns.add(
        CampaignProgress('Pack', [LevelProgress('Pack/Map'), LevelProgress('Pack/Extra')])
    )
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    donor.summit_gems = {_GEM}
    extra = 'Pack/Extra/r/1'
    previous.summit_gems = {extra, _UNRELATED}
    ops.overwrite_campaign_progress(
        source,
        target,
        'Pack',
        goldenberry_ids={},
        custom_summit_gem_ids={**_IDS, ('Pack/Extra', LevelSide.A): {extra}},
        source_communal=donor,
        target_communal=previous,
    )
    assert previous.summit_gems == {_GEM, _UNRELATED}
    assert donor.summit_gems == {_GEM}


@pytest.mark.parametrize('shared', [False, True])
def test_custom_preflight_failure_keeps_all_documents_unchanged(shared: bool) -> None:
    source, target = MainSave(), MainSave()
    for save in (source, target):
        save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    donor.summit_gems = {_GEM}
    previous.summit_gems = donor.summit_gems if shared else {_OTHER}
    before = source.tostring(), target.tostring(), donor.tostring(), previous.tostring()
    with pytest.raises(ProgressConflictError):
        ops.move_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            poem_id=None,
            mode='exclusive' if not shared else 'overwrite',
            goldenberry_ids={},
            custom_summit_gem_ids=_IDS,
            source_communal=donor,
            target_communal=previous,
        )
    assert (source.tostring(), target.tostring(), donor.tostring(), previous.tostring()) == before


def test_shared_side_key_moves_only_when_explicitly_selected() -> None:
    source, target = MainSave(), MainSave()
    for save in (source, target):
        save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    donor.summit_gems = {_GEM}
    previous.summit_gems = set()
    # Even though the same key can be used by A, the caller explicitly selects it for B.
    ops.move_map_progress(
        source,
        target,
        'Pack/Map',
        LevelSide.B,
        poem_id=None,
        mode='merge',
        goldenberry_ids={},
        custom_summit_gem_ids={('Pack/Map', LevelSide.B): {_GEM}},
        source_communal=donor,
        target_communal=previous,
    )
    assert donor.summit_gems == set()
    assert previous.summit_gems == {_GEM}


def test_custom_edits_are_not_applied_when_a_later_global_check_fails() -> None:
    source, target = MainSave(), MainSave()
    for save in (source, target):
        save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    target_map = target.flat_levels['Pack/Map'].maps[LevelSide.A]
    assert target_map is not None
    target_map.deaths = 1  # A historical inconsistent global offset would become negative.
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    donor.summit_gems, previous.summit_gems = {_GEM}, {_OTHER}
    before = source.tostring(), target.tostring(), donor.tostring(), previous.tostring()
    with pytest.raises(ValueError):
        ops.move_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            poem_id=None,
            mode='overwrite',
            goldenberry_ids={},
            custom_summit_gem_ids=_IDS,
            source_communal=donor,
            target_communal=previous,
        )
    assert (source.tostring(), target.tostring(), donor.tostring(), previous.tostring()) == before


@pytest.mark.parametrize('omit', ['ids', 'source', 'target'])
def test_custom_parameters_must_be_supplied_together(omit: str) -> None:
    source, target = MainSave(), MainSave()
    with pytest.raises(ValueError, match='together'):
        ops.copy_map_progress(
            source,
            target,
            _SID,
            LevelSide.A,
            poem_id=None,
            mode='merge',
            goldenberry_ids={},
            custom_summit_gem_ids=None if omit == 'ids' else {},
            source_communal=None if omit == 'source' else CommunalHelperSave(),
            target_communal=None if omit == 'target' else CommunalHelperSave(),
        )


def test_unrelated_arrays_keep_their_object_identity() -> None:
    source, target = MainSave(), MainSave()
    source.summit_gems, target.summit_gems = [True] * 6, [False] * 6
    original_source, original_target = source.summit_gems, target.summit_gems
    ops.move_map_progress(
        source,
        target,
        _SID,
        LevelSide.B,
        poem_id=None,
        mode='merge',
        goldenberry_ids={},
    )
    assert source.summit_gems is original_source
    assert target.summit_gems is original_target
