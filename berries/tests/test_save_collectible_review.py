"""Failure boundaries and protocol regressions from the collectible linkage review."""

from copy import copy, deepcopy

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves import operations as ops
from berries.game.saves.errors import BadProgressSave, ProgressConflictError
from berries.game.saves.files import CommunalHelperSave, EndersBlenderSave, MainSave
from berries.game.saves.progress import CampaignProgress, LevelProgress, Session
from berries.game.saves.unknown import (
    UnknownFieldPolicy,
    UnknownSaveFieldError,
    UnknownSaveFieldWarning,
)
from berries.map_entity_id import MapEntityID
from test_support.save_docs import parsed_session, require_map

_SID = 'Pack/Map'
_GOLD = MapEntityID('r', 1)
_GEM = 'Pack/Map/r/0'
_GOLD_IDS: ops.GoldenberryIDs = {(_SID, LevelSide.A): {_GOLD}}
_GEM_IDS: ops.CustomSummitGemIDs = {(_SID, LevelSide.A): {_GEM}}
type _CampaignState = tuple[
    str, int, tuple[tuple[str, tuple[frozenset[MapEntityID] | None, ...]], ...]
]
type _SaveState = tuple[Duration, int, int, bool, tuple[_CampaignState, ...]]


@pytest.mark.parametrize('same_candidate', [False, True])
@pytest.mark.parametrize('copied_mods', [False, True])
def test_root_install_rejects_shared_actual_target_before_any_member_or_marker_change(
    same_candidate: bool, copied_mods: bool
) -> None:
    first, second, independent = MainSave(), MainSave(), MainSave()
    second.install_mod_progress(copy(first.mods) if copied_mods else first.mods)
    left = CampaignProgress('Pack', [LevelProgress('Pack/First')])
    right = left if same_candidate else CampaignProgress('Pack', [LevelProgress('Pack/Second')])
    separate = CampaignProgress('Pack', [LevelProgress('Pack/Independent')])
    saves = first, second, independent
    views = tuple(view for save in saves for view in (save.flat_levels, save.mods.flat_levels))
    before = tuple(_state(save) for save in saves)
    before_xml = tuple(save.tostring() for save in saves)
    before_views = tuple(dict(view) for view in views)
    roots = tuple(save.mods for save in saves)
    with pytest.raises(ProgressConflictError, match='same campaign membership'):
        MainSave.install_campaigns((independent, separate), (first, left), (second, right))
    assert tuple(_state(save) for save in saves) == before
    assert tuple(save.tostring() for save in saves) == before_xml
    assert tuple(dict(view) for view in views) == before_views
    assert all(save.mods is root for save, root in zip(saves, roots, strict=True))


def test_root_install_uses_member_container_identity_not_the_shared_old_campaign() -> None:
    first, second = MainSave(), MainSave()
    old = CampaignProgress('Pack', [LevelProgress('Pack/Old')])
    for save in (first, second):
        save.mods.campaigns.add(old)
    left = CampaignProgress('Pack', [LevelProgress('Pack/First')])
    right = CampaignProgress('Pack', [LevelProgress('Pack/Second')])
    MainSave.install_campaigns((first, left), (second, right))
    assert first.mods.campaigns['Pack'] is left
    assert second.mods.campaigns['Pack'] is right
    assert tuple(first.mods.flat_levels) == ('Pack/First',)
    assert tuple(second.mods.flat_levels) == ('Pack/Second',)


def test_shared_vanilla_value_does_not_make_separate_root_slots_the_same_target() -> None:
    first, second = MainSave(), MainSave()
    second.install_campaigns((second, first.vanilla))
    left, right = deepcopy(first.vanilla.snapshot()), deepcopy(second.vanilla.snapshot())
    left.total_strawberries, right.total_strawberries = 1, 2
    first_view, second_view = first.flat_levels, second.flat_levels
    MainSave.install_campaigns((first, left), (second, right))
    assert first.vanilla.levels is left.levels and second.vanilla.levels is right.levels
    sid = next(iter(left.levels))
    assert first_view[sid] is left.levels[sid]
    assert second_view[sid] is right.levels[sid]


def _save(*, populated: bool, wrong: str | None = None) -> MainSave:
    save = MainSave()
    if wrong is not None:
        root = etree.fromstring(save.tostring())
        partition = root.find(wrong)
        if partition is None:
            partition = etree.SubElement(root, wrong)
        campaign = etree.SubElement(partition, 'LevelSetStats', Name='Wrong')
        etree.SubElement(etree.SubElement(campaign, 'Areas'), 'AreaStats', SID='Beta/Map')
        save = MainSave.fromstring(etree.tostring(root), strict=False)
    level = LevelProgress(_SID)
    if populated:
        require_map(level.maps[0]).strawberries.add(_GOLD)
    save.mods.campaigns.add(CampaignProgress('Pack', [level], total_strawberries=int(populated)))
    save.total_goldenberries = int(populated)
    return save


def _state(save: MainSave) -> _SaveState:
    return (
        save.time_played,
        save.deaths,
        save.total_goldenberries,
        save.needs_mod_recovery,
        tuple(
            (
                campaign.name,
                campaign.total_strawberries,
                tuple(
                    (
                        sid,
                        tuple(
                            None if progress is None else frozenset(progress.strawberries)
                            for progress in level.maps
                        ),
                    )
                    for sid, level in campaign.levels.items()
                ),
            )
            for partition in (save.mods.campaigns, save.mods.recycled_campaigns)
            for campaign in partition.values()
        ),
    )


@pytest.mark.parametrize('partition', ['LevelSets', 'LevelSetRecycleBin'])
@pytest.mark.parametrize(
    'operation, bad_side',
    [
        ('clear', 'source'),
        ('copy', 'target'),
        ('move', 'source'),
        ('move', 'target'),
    ],
)
def test_root_failure_precedes_all_member_counter_and_gem_changes(
    partition: str, operation: str, bad_side: str
) -> None:
    source = _save(populated=True, wrong=partition if bad_side == 'source' else None)
    target = _save(populated=False, wrong=partition if bad_side == 'target' else None)
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    donor.summit_gems, previous.summit_gems = {_GEM}, set()
    source_blender, target_blender = EndersBlenderSave(), EndersBlenderSave()
    source_blender.room_names[_SID] = {'r': 'room'}
    blender_before = source_blender.tostring(), target_blender.tostring()
    before = _state(source), _state(target), donor.tostring(), previous.tostring()
    source_view, target_view = source.flat_levels, target.flat_levels
    source_level, target_level = source_view[_SID], target_view[_SID]
    source_campaign, target_campaign = source.mods.campaigns['Pack'], target.mods.campaigns['Pack']
    if operation == 'clear':
        with pytest.raises(BadProgressSave, match='does not belong'):
            ops.clear_map_progress(
                source,
                _SID,
                LevelSide.A,
                poem_id=None,
                goldenberry_ids=_GOLD_IDS,
                custom_summit_gem_ids=_GEM_IDS,
                communal=donor,
            )
    else:
        action = ops.copy_map_progress if operation == 'copy' else ops.move_map_progress
        with pytest.raises(BadProgressSave, match='does not belong'):
            action(
                source,
                target,
                _SID,
                LevelSide.A,
                poem_id=None,
                mode='overwrite',
                goldenberry_ids=_GOLD_IDS,
                custom_summit_gem_ids=_GEM_IDS,
                source_communal=donor,
                target_communal=previous,
                source_blender=source_blender,
                target_blender=target_blender,
            )
    assert (_state(source), _state(target), donor.tostring(), previous.tostring()) == before
    assert source_view[_SID] is source_level and target_view[_SID] is target_level
    assert source.mods.campaigns['Pack'] is source_campaign
    assert target.mods.campaigns['Pack'] is target_campaign
    assert (source_blender.tostring(), target_blender.tostring()) == blender_before


@pytest.mark.parametrize('partition', ['LevelSets', 'LevelSetRecycleBin'])
def test_copy_can_read_valid_scope_from_a_source_with_unrelated_lenient_data(
    partition: str,
) -> None:
    source, target = _save(populated=True, wrong=partition), _save(populated=False)
    before = _state(source)
    target_root, target_mod_index = target.mods, target.mods.flat_levels
    ops.copy_map_progress(
        source,
        target,
        _SID,
        LevelSide.A,
        poem_id=None,
        mode='overwrite',
        goldenberry_ids=_GOLD_IDS,
    )
    assert _state(source) == before
    assert target.total_goldenberries == 1
    assert target.mods is target_root and not target.needs_mod_recovery
    assert require_map(target_mod_index[_SID].maps[0]).strawberries == {_GOLD}
    assert MainSave.fromstring(target.tostring()).total_goldenberries == 1


@pytest.mark.parametrize('move', [False, True])
def test_related_index_refusal_does_not_commit_any_root(move: bool) -> None:
    source = _save(populated=True)
    target = MainSave()
    target.mods.campaigns.add(CampaignProgress('Pack'))
    root = etree.fromstring(MainSave().tostring())
    area = root.find('Areas/AreaStats')
    assert area is not None
    area.set('SID', _SID)
    observer = MainSave.fromstring(etree.tostring(root), strict=False)
    observer.install_mod_progress(target.mods)
    views = source.flat_levels, target.flat_levels, observer.flat_levels
    before_views = tuple(dict(view) for view in views)
    before = _state(source), _state(target), _state(observer)
    action = ops.move_level_progress if move else ops.copy_level_progress
    with pytest.raises(BadProgressSave, match='Duplicate progress SID'):
        action(source, target, _SID, poem_ids={}, mode='overwrite', goldenberry_ids=_GOLD_IDS)
    assert tuple(dict(view) for view in views) == before_views
    assert (_state(source), _state(target), _state(observer)) == before


@pytest.mark.parametrize(
    'token, value',
    [
        ('true', True),
        ('false', False),
        ('1', True),
        ('0', False),
        (' \t\r\ntrue\n', True),
        ('\t0\r ', False),
    ],
)
@pytest.mark.parametrize('field', ['gems', 'assist', 'completed', 'session'])
def test_xml_boolean_lexical_space_is_shared(token: str, value: bool, field: str) -> None:
    if field == 'session':
        root = etree.fromstring(parsed_session(_SID, LevelSide.A).tostring())
        root.set('InArea', token)
        assert Session.from_xml(root).in_level is value
        return
    root = etree.fromstring(MainSave().tostring())
    if field == 'gems':
        node = etree.SubElement(root, 'SummitGems')
        for _ in range(6):
            etree.SubElement(node, 'boolean').text = token
    elif field == 'assist':
        node = root.find('AssistMode')
        assert node is not None
        node.text = token
    else:
        node = root.find('Areas/AreaStats/Modes/AreaModeStats')
        assert node is not None
        node.set('Completed', token)
    save = MainSave.fromstring(etree.tostring(root))
    if field == 'gems':
        assert save.summit_gems == [value] * 6
    elif field == 'assist':
        assert save.assist_mode is value
    else:
        assert require_map(next(iter(save.vanilla.levels.values())).maps[0]).completed is value


@pytest.mark.parametrize('token, value', [('TRUE', True), ('False', False), ('true', True)])
def test_xml_boolean_fix_does_not_narrow_blender_yaml(token: str, value: bool) -> None:
    save = EndersBlenderSave.fromstring(
        f'mapDict_roomStat_firstClear_pauseType:\n  Pack/Map:\n    Pause: {token}\n'
    )
    assert save.first_clear['Pack/Map'].pause_flags['Pause'] is value


@pytest.mark.parametrize('token', ['TRUE', 'False', 'yes', '\u00a0true', 'true\u00a0'])
@pytest.mark.parametrize('strict', [False, True])
def test_non_xml_boolean_spellings_are_not_preserved_as_game_unreadable_output(
    token: str, strict: bool
) -> None:
    root = etree.fromstring(MainSave().tostring())
    node = etree.SubElement(root, 'SummitGems')
    for _ in range(6):
        etree.SubElement(node, 'boolean').text = token
    with pytest.raises(ValueError, match='XML boolean'):
        MainSave.fromstring(etree.tostring(root), strict=strict)


@pytest.mark.parametrize('session', [False, True])
@pytest.mark.parametrize('length', [2, 6, 7])
def test_gem_position_edits_require_warn_to_drop_unknown_attributes(
    session: bool, length: int
) -> None:
    root = etree.fromstring(
        parsed_session(_SID, LevelSide.A).tostring() if session else MainSave().tostring()
    )
    previous = root.find('SummitGems')
    if previous is not None:
        root.remove(previous)
    gems = etree.SubElement(root, 'SummitGems', nsmap={'mod': 'urn:mod'})
    gems.set('{urn:mod}type', 'mod:GemArray')
    for i in range(length):
        node = etree.SubElement(gems, 'boolean')
        node.text = 'false'
        node.set('{urn:mod}note', str(i))
        node.append(etree.Comment(f'keep-{i}'))

    def load(unknown: UnknownFieldPolicy = 'error') -> Session | MainSave:
        return (
            Session.from_xml(root, unknown=unknown)
            if session
            else MainSave.fromstring(etree.tostring(root), strict=False, unknown=unknown)
        )

    with pytest.raises(UnknownSaveFieldError):
        load()
    with pytest.warns(UnknownSaveFieldWarning):
        save = load('warn')
    save.summit_gems = [True, False, False, False, False, False]
    encoded = etree.fromstring(save.tostring())
    result = encoded.find('SummitGems')
    assert result is not None and result.get('{urn:mod}type') is None
    items = result.findall('boolean')
    assert len(items) == 6 and ''.join(items[0].itertext()) == 'true'
    assert all(not item.attrib and not len(item) for item in items)


@pytest.mark.parametrize('malformed', ['nil', 'short'])
@pytest.mark.parametrize('same_identity', [False, True])
def test_safe_session_mirror_repairs_legacy_candidate_before_encoding(
    malformed: str, same_identity: bool
) -> None:
    root = etree.fromstring(MainSave().tostring())
    for tag in ('CurrentSession', 'CurrentSession_Safe'):
        sid = (
            'Celeste/7-Summit' if same_identity or tag.endswith('_Safe') else 'Celeste/6-Reflection'
        )
        node = etree.fromstring(parsed_session(sid, LevelSide.A, room=tag).tostring())
        node.tag = tag
        previous = node.find('SummitGems')
        assert previous is not None
        node.remove(previous)
        gems = etree.SubElement(node, 'SummitGems')
        if tag == 'CurrentSession' and malformed == 'nil':
            gems.set('{http://www.w3.org/2001/XMLSchema-instance}nil', 'true')
        else:
            for _ in range(1 if tag == 'CurrentSession' else 6):
                etree.SubElement(gems, 'boolean').text = 'false'
        root.append(node)
    save = MainSave.fromstring(etree.tostring(root), strict=False)
    selected = save.session
    assert selected is not None
    selected.summit_gems[0] = True
    if not same_identity and malformed == 'short':
        with pytest.raises(ValueError):
            save.tostring()
    else:
        encoded = etree.fromstring(save.tostring())
        for tag in ('CurrentSession', 'CurrentSession_Safe'):
            node = encoded.find(tag)
            assert node is not None and node.get('Level') == tag
            collected = same_identity or tag.endswith('_Safe')
            assert (
                node.xpath('SummitGems/boolean/text()') == [str(collected).lower()] + ['false'] * 5
            )
        assert MainSave.fromstring(save.tostring()).session is not None
    assert tuple(selected.iter_changed_fields()) == ('summit_gems',)


@pytest.mark.parametrize('mode', ['merge', 'overwrite', 'exclusive'])
@pytest.mark.parametrize('move', [False, True])
@pytest.mark.parametrize('empty_campaign', [False, True])
def test_whole_campaign_scope_includes_explicit_old_map_groups(
    mode: ops.TransferMode, move: bool, empty_campaign: bool
) -> None:
    source, target = _save(populated=False), _save(populated=False)
    if empty_campaign:
        source.mods.campaigns['Pack'].levels.clear()
        target.mods.campaigns['Pack'].levels.clear()
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    old, target_old, outside = 'Pack/Removed/r/0', 'Pack/Old/r/1', 'Else/Map/r/0'
    ids: ops.CustomSummitGemIDs = {
        ('Pack/Removed', LevelSide.A): {old},
        ('Pack/Old', LevelSide.B): {target_old},
        ('Else/Map', LevelSide.A): {outside},
    }
    donor.summit_gems = {old, outside}
    previous.summit_gems = {outside} if mode == 'exclusive' else {target_old, outside}
    assert not ops.is_empty_campaign_progress(
        source, 'Pack', communal=donor, custom_summit_gem_ids=ids
    )
    action = ops.move_campaign_progress if move else ops.copy_campaign_progress
    action(
        source,
        target,
        'Pack',
        mode=mode,
        goldenberry_ids={},
        custom_summit_gem_ids=ids,
        source_communal=donor,
        target_communal=previous,
    )
    assert donor.summit_gems == ({outside} if move else {old, outside})
    assert previous.summit_gems == (
        {old, target_old, outside} if mode == 'merge' else {old, outside}
    )
    ops.clear_campaign_progress(
        target, 'Pack', goldenberry_ids={}, communal=previous, custom_summit_gem_ids=ids
    )
    assert previous.summit_gems == {outside}


def test_missing_campaign_query_checks_gems_but_writes_still_require_the_parent() -> None:
    source, target = MainSave(), MainSave()
    custom = CommunalHelperSave()
    custom.summit_gems = {_GEM}
    assert not ops.is_empty_campaign_progress(
        source, 'Pack', communal=custom, custom_summit_gem_ids=_GEM_IDS
    )
    before = source.tostring(), target.tostring(), custom.tostring()
    with pytest.raises(KeyError, match='Pack'):
        ops.clear_campaign_progress(
            source, 'Pack', goldenberry_ids={}, communal=custom, custom_summit_gem_ids=_GEM_IDS
        )
    with pytest.raises(KeyError, match='Pack'):
        ops.move_campaign_progress(
            source,
            target,
            'Pack',
            mode='merge',
            goldenberry_ids={},
            source_communal=custom,
            target_communal=CommunalHelperSave(),
            custom_summit_gem_ids=_GEM_IDS,
        )
    assert (source.tostring(), target.tostring(), custom.tostring()) == before


def test_exclusive_rejects_explicit_old_map_gems_without_level_records() -> None:
    source, target = _save(populated=False), _save(populated=False)
    donor, previous = CommunalHelperSave(), CommunalHelperSave()
    old = 'Pack/Removed/r/0'
    ids: ops.CustomSummitGemIDs = {('Pack/Removed', LevelSide.C): {old}}
    donor.summit_gems, previous.summit_gems = set(), {old}
    before = source.tostring(), target.tostring(), donor.tostring(), previous.tostring()
    with pytest.raises(ProgressConflictError, match='Gem progress'):
        ops.move_campaign_progress(
            source,
            target,
            'Pack',
            mode='exclusive',
            goldenberry_ids={},
            source_communal=donor,
            target_communal=previous,
            custom_summit_gem_ids=ids,
        )
    assert (source.tostring(), target.tostring(), donor.tostring(), previous.tostring()) == before
