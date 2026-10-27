"""Session slot conflicts and progress edits are preflighted as one operation."""

from typing import Literal

import pytest
import yaml
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.errors import ProgressConflictError
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.main import MainSave
from berries.game.saves.operations import (
    TransferMode,
    clear_campaign_progress,
    clear_level_progress,
    clear_map_progress,
    copy_campaign_progress,
    copy_level_progress,
    copy_map_progress,
    move_campaign_progress,
    move_level_progress,
    move_map_progress,
)
from berries.game.saves.progress import CampaignProgress, LevelProgress
from berries.game.saves.session_input import SessionInput
from test_support.save_docs import complete_vanilla, parsed_session, require_map

type Scope = Literal['map', 'level', 'campaign']

_SID = 'Pack/Map'


def _main(*, populated: bool = False) -> MainSave:
    save = MainSave.fromstring(
        complete_vanilla(
            '<SaveData><Time>1000000000</Time><TotalDeaths>100</TotalDeaths></SaveData>'
        ),
        strict=False,
    )
    campaign = CampaignProgress('Pack')
    level = LevelProgress(_SID)
    if populated:
        for progress in level.maps:
            assert progress is not None
            progress.time_played = Duration.from_seconds(10)
            progress.deaths = 2
    campaign.levels.add(level)
    save.mods.campaigns.add(campaign)
    return save


def _collab(side: LevelSide | None = None, *, sid: str = _SID) -> CollabUtils2Save:
    save = CollabUtils2Save()
    if side is not None:
        xml = parsed_session(sid, side, room='saved', in_level=True).tostring()
        save.sessions.add(SessionInput(sid, xml))
        save.mod_sessions[sid] = {'Example': 'opaque: text\nnumber: 01\n'}
        save.mod_sessions_binary[sid] = {'Other': 'AAH/'}
    return save


def _transfer(
    source: MainSave,
    target: MainSave,
    scope: Scope,
    *,
    move: bool = True,
    mode: TransferMode = 'merge',
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab: bool = False,
    discard_current: bool = False,
) -> None:
    if scope == 'map':
        action = move_map_progress if move else copy_map_progress
        action(
            source,
            target,
            _SID,
            LevelSide.A,
            goldenberry_ids={},
            poem_id=None,
            mode=mode,
            source_collab=source_collab,
            target_collab=target_collab,
            discard_collab_session=discard_collab,
            discard_current_session=discard_current,
        )
    elif scope == 'level':
        level_action = move_level_progress if move else copy_level_progress
        level_action(
            source,
            target,
            _SID,
            goldenberry_ids={},
            poem_ids={},
            mode=mode,
            source_collab=source_collab,
            target_collab=target_collab,
            discard_collab_session=discard_collab,
            discard_current_session=discard_current,
        )
    else:
        campaign_action = move_campaign_progress if move else copy_campaign_progress
        campaign_action(
            source,
            target,
            'Pack',
            goldenberry_ids={},
            mode=mode,
            source_collab=source_collab,
            target_collab=target_collab,
            discard_collab_session={_SID: discard_collab},
            discard_current_session=discard_current,
        )


def _state(main: MainSave, collab: CollabUtils2Save) -> tuple[str, str]:
    return main.tostring(), collab.tostring()


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
@pytest.mark.parametrize('move', (False, True))
@pytest.mark.parametrize('mode', ('merge', 'overwrite', 'exclusive'))
def test_sessions_follow_progress_without_double_counting(
    scope: Scope,
    move: bool,
    mode: TransferMode,
) -> None:
    source, target = _main(populated=True), _main()
    source.session = parsed_session(
        _SID, LevelSide.A, time_played=Duration.from_seconds(7), deaths=1
    )
    origin = source.session
    source_collab, target_collab = _collab(LevelSide.A), _collab()
    source_collab.sessions[_SID].session.room = 'edited'
    before = _state(source, source_collab)
    _transfer(
        source,
        target,
        scope,
        move=move,
        mode=mode,
        source_collab=source_collab,
        target_collab=target_collab,
    )
    assert target.current_map == (_SID, LevelSide.A)
    assert target.session is not origin
    assert target.session is not None and target.session.deaths == 1
    assert MainSave.fromstring(target.tostring(), strict=False).current_map == target.current_map
    restored = CollabUtils2Save.fromstring(target_collab.tostring())
    assert restored.sessions[_SID].session.room == 'edited'
    assert restored.mod_sessions[_SID] == {'Example': 'opaque: text\nnumber: 01\n'}
    assert restored.mod_sessions_binary[_SID] == {'Other': 'AAH/'}
    count = 1 if scope == 'map' else 3
    assert target.deaths == 100 + count * 2
    assert target.time_played == Duration.from_seconds(100 + count * 10)
    if move:
        assert source.current_map is None and source.session is None
        assert _SID not in source_collab.sessions
        assert _SID not in source_collab.mod_sessions
        assert _SID not in source_collab.mod_sessions_binary
        assert source.deaths == 100 - count * 2
    else:
        assert _state(source, source_collab) == before
        assert target_collab.sessions[_SID] is not source_collab.sessions[_SID]
        assert target_collab.mod_sessions[_SID] is not source_collab.mod_sessions[_SID]


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
def test_current_slot_conflicts_even_when_target_is_another_map(scope: Scope) -> None:
    source, target = _main(populated=True), _main()
    source.session = parsed_session(_SID, LevelSide.A)
    target.session = parsed_session('Elsewhere/Map', LevelSide.C)
    before = source.tostring(), target.tostring()
    with pytest.raises(ProgressConflictError, match='target current session'):
        _transfer(source, target, scope)
    assert (source.tostring(), target.tostring()) == before
    _transfer(source, target, scope, discard_current=True)
    assert target.current_map == (_SID, LevelSide.A)


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
def test_collab_slot_conflicts_across_sides_and_all_payloads_are_replaced(scope: Scope) -> None:
    source, target = _main(populated=True), _main()
    a, b = _collab(LevelSide.A), _collab(LevelSide.C)
    del a.mod_sessions_binary[_SID]
    b.mod_sessions[_SID]['Stale'] = 'must disappear'
    before = _state(source, a), _state(target, b)
    with pytest.raises(ProgressConflictError, match='slot is occupied'):
        _transfer(source, target, scope, source_collab=a, target_collab=b)
    assert (_state(source, a), _state(target, b)) == before
    _transfer(source, target, scope, source_collab=a, target_collab=b, discard_collab=True)
    assert b.sessions[_SID].session.side is LevelSide.A
    assert b.mod_sessions[_SID] == {'Example': 'opaque: text\nnumber: 01\n'}
    assert _SID not in b.mod_sessions_binary


def test_map_selection_leaves_other_side_and_unrelated_sessions_alone() -> None:
    source, target = _main(populated=True), _main()
    source.session = parsed_session(_SID, LevelSide.B)
    target.session = parsed_session('Elsewhere/Map', LevelSide.A)
    a, b = _collab(LevelSide.B), _collab(LevelSide.C)
    a.sessions.add(SessionInput('Elsewhere/Broken', '<not-xml'))
    b.sessions.add(SessionInput('Elsewhere/Broken', '<not-xml'))
    _transfer(
        source,
        target,
        'map',
        source_collab=a,
        target_collab=b,
        discard_collab=True,
        discard_current=True,
    )
    assert source.current_map == (_SID, LevelSide.B)
    assert target.current_map == ('Elsewhere/Map', LevelSide.A)
    assert a.sessions[_SID].session.side is LevelSide.B
    assert b.sessions[_SID].session.side is LevelSide.C
    assert b.sessions['Elsewhere/Broken'].orig_xml == '<not-xml'


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
def test_progress_edit_without_incoming_session_still_requires_discard(scope: Scope) -> None:
    source, target = _main(populated=True), _main()
    a, b = _collab(), _collab(LevelSide.A)
    target.session = parsed_session(_SID, LevelSide.A)
    before = _state(source, a), _state(target, b)
    with pytest.raises(ProgressConflictError):
        _transfer(source, target, scope, source_collab=a, target_collab=b)
    assert (_state(source, a), _state(target, b)) == before
    with pytest.raises(ProgressConflictError):
        _transfer(
            source,
            target,
            scope,
            source_collab=a,
            target_collab=b,
            discard_current=True,
        )
    assert (_state(source, a), _state(target, b)) == before
    _transfer(
        source,
        target,
        scope,
        source_collab=a,
        target_collab=b,
        discard_current=True,
        discard_collab=True,
    )
    assert target.session is None
    assert not b.sessions and not b.mod_sessions and not b.mod_sessions_binary


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
def test_clear_requires_explicit_discard_and_clears_payloads(scope: Scope) -> None:
    save, collab = _main(populated=True), _collab(LevelSide.A)
    save.session = parsed_session(_SID, LevelSide.A)

    def clear(*, discard: bool) -> None:
        if scope == 'map':
            clear_map_progress(
                save,
                _SID,
                LevelSide.A,
                goldenberry_ids={},
                poem_id=None,
                collab=collab,
                discard_collab_session=discard,
                discard_current_session=discard,
            )
        elif scope == 'level':
            clear_level_progress(
                save,
                _SID,
                goldenberry_ids={},
                poem_ids={},
                collab=collab,
                discard_collab_session=discard,
                discard_current_session=discard,
            )
        else:
            clear_campaign_progress(
                save,
                'Pack',
                goldenberry_ids={},
                collab=collab,
                discard_collab_session={_SID: discard},
                discard_current_session=discard,
            )

    before = _state(save, collab)
    with pytest.raises(ProgressConflictError):
        clear(discard=False)
    assert _state(save, collab) == before
    clear(discard=True)
    assert save.session is None
    assert not collab.sessions and not collab.mod_sessions and not collab.mod_sessions_binary
    assert require_map(save.flat_levels[_SID].maps[LevelSide.A]).deaths == 0


def test_late_progress_failure_does_not_discard_or_move_sessions() -> None:
    source, target = _main(populated=True), _main()
    source.session = parsed_session(_SID, LevelSide.A)
    target.session = parsed_session('Elsewhere/Map', LevelSide.A)
    a, b = _collab(LevelSide.A), _collab(LevelSide.B)
    source.deaths = 0
    before = _state(source, a), _state(target, b)
    with pytest.raises(ValueError):
        _transfer(
            source,
            target,
            'map',
            source_collab=a,
            target_collab=b,
            discard_collab=True,
            discard_current=True,
        )
    assert (_state(source, a), _state(target, b)) == before


@pytest.mark.parametrize('move', (False, True))
def test_related_index_refusal_keeps_sessions_and_all_progress_unchanged(move: bool) -> None:
    source, target = _main(populated=True), _main()
    del target.mods.campaigns['Pack'].levels[_SID]
    source.session = parsed_session(_SID, LevelSide.A)
    target.session = parsed_session('Elsewhere/Map', LevelSide.C)
    a, b = _collab(LevelSide.A), _collab(LevelSide.B)
    root = etree.fromstring(MainSave().tostring())
    area = root.find('Areas/AreaStats')
    assert area is not None
    area.set('SID', _SID)
    observer = MainSave.fromstring(etree.tostring(root), strict=False)
    observer.install_mod_progress(target.mods)
    before = _state(source, a), _state(target, b)
    source_view, target_view = source.flat_levels, target.flat_levels
    source_level = source_view[_SID]
    assert _SID not in target_view
    with pytest.raises(ValueError, match='Duplicate progress SID'):
        _transfer(
            source,
            target,
            'level',
            move=move,
            source_collab=a,
            target_collab=b,
            discard_collab=True,
            discard_current=True,
        )
    assert (_state(source, a), _state(target, b)) == before
    assert source_view[_SID] is source_level and _SID not in target_view


@pytest.mark.parametrize('shared', ('document', 'sessions', 'text', 'binary', 'unpaired'))
def test_collab_pair_and_storage_overlap_are_rejected(shared: str) -> None:
    source, target = _main(populated=True), _main()
    a, b = _collab(LevelSide.A), _collab()
    if shared == 'document':
        b = a
    elif shared == 'sessions':
        b.sessions = a.sessions
    elif shared == 'text':
        b.mod_sessions = a.mod_sessions
    elif shared == 'binary':
        b.mod_sessions_binary = a.mod_sessions_binary
    before = _state(source, a), _state(target, b)
    with pytest.raises(ValueError):
        _transfer(
            source,
            target,
            'map',
            source_collab=a,
            target_collab=None if shared == 'unpaired' else b,
        )
    assert (_state(source, a), _state(target, b)) == before


def test_campaign_policy_is_per_sid_and_omitted_keys_mean_error() -> None:
    source, target = _main(populated=True), _main()
    source.mods.campaigns['Pack'].levels.add(LevelProgress('Pack/Other'))
    a, b = _collab(LevelSide.A), _collab(LevelSide.C)
    extra = _collab(LevelSide.B, sid='Pack/Other')
    a.sessions.update(extra.sessions)
    b.sessions.update(extra.sessions)
    before = _state(source, a), _state(target, b)
    with pytest.raises(ProgressConflictError):
        move_campaign_progress(
            source,
            target,
            'Pack',
            goldenberry_ids={},
            mode='merge',
            source_collab=a,
            target_collab=b,
            discard_collab_session={_SID: True},
        )
    assert (_state(source, a), _state(target, b)) == before
    move_campaign_progress(
        source,
        target,
        'Pack',
        goldenberry_ids={},
        mode='merge',
        source_collab=a,
        target_collab=b,
        discard_collab_session={_SID: True, 'Pack/Other': True},
    )
    assert set(b.sessions) == {_SID, 'Pack/Other'} and not a.sessions


def test_vanilla_current_session_installs_both_identities_and_preserves_do_not_load() -> None:
    source, target = _main(), _main()
    sid = 'Celeste/1-ForsakenCity'
    xml = (
        parsed_session(sid, LevelSide.B)
        .tostring()
        .replace(
            '<DoNotLoad/>',
            '<DoNotLoad><EntityID Key="r:1"/></DoNotLoad>',
        )
    )
    source.session = SessionInput(sid, xml).session
    move_level_progress(source, target, sid, mode='merge', poem_ids={}, goldenberry_ids={})
    root = etree.fromstring(target.tostring())
    for tag in ('CurrentSession', 'CurrentSession_Safe'):
        assert root.find(f'{tag}/DoNotLoad/EntityID') is not None
    reread = MainSave.fromstring(target.tostring(), strict=False)
    assert reread.current_map == (sid, LevelSide.B)
    assert source.session is None


@pytest.mark.parametrize('field', ('ModSessionsPerLevel', 'ModSessionsPerLevelBinary'))
def test_collab_payload_codec_keeps_strings(field: str) -> None:
    doc = CollabUtils2Save.fromstring(f'{field}: {{"01": {{"true": "001", "02": "AAH/"}}}}\n')
    member = doc.mod_sessions if field == 'ModSessionsPerLevel' else doc.mod_sessions_binary
    member['01']['true'] = 'changed'
    result = yaml.safe_load(doc.tostring())
    assert result[field]['01']['true'] == 'changed'
    assert result[field]['01']['02'] == 'AAH/'
    assert CollabUtils2Save.fromstring(doc.tostring()).tostring() == doc.tostring()


def test_campaign_retained_session_without_level_is_included() -> None:
    source, target = _main(), _main()
    a, b = _collab(LevelSide.B, sid='Pack/Removed'), _collab()
    _transfer(source, target, 'campaign', source_collab=a, target_collab=b)
    assert 'Pack/Removed' in b.sessions and not a.sessions


def test_invalid_incoming_session_does_not_modify_any_history() -> None:
    source, target = _main(populated=True), _main()
    a, b = _collab(LevelSide.A), _collab()
    a.sessions[_SID].session.summit_gems = []
    before = source.tostring(), target.tostring()
    with pytest.raises(ValueError):
        _transfer(source, target, 'map', source_collab=a, target_collab=b)
    assert (source.tostring(), target.tostring()) == before
    assert _SID in a.sessions and not b.sessions


def test_inactive_current_is_not_a_conflict() -> None:
    source, target = _main(populated=True), _main()
    source.session = parsed_session(_SID, LevelSide.A)
    target.session = parsed_session('Elsewhere/Map', LevelSide.C, in_level=False)
    _transfer(source, target, 'map')
    assert target.current_map == (_SID, LevelSide.A)


def test_clear_level_can_discard_malformed_xml_without_decoding_its_side() -> None:
    save, collab = _main(), _collab()
    collab.sessions.add(SessionInput(_SID, '<broken'))
    with pytest.raises(ProgressConflictError, match='Collab session'):
        clear_level_progress(save, _SID, poem_ids={}, goldenberry_ids={}, collab=collab)
    clear_level_progress(
        save,
        _SID,
        poem_ids={},
        goldenberry_ids={},
        collab=collab,
        discard_collab_session=True,
    )
    assert not collab.sessions


def test_orphan_payload_requires_explicit_repair_before_transfer() -> None:
    source, target = _main(populated=True), _main()
    a, b = _collab(), _collab()
    a.mod_sessions[_SID] = {'Example': 'data'}
    before = _state(source, a), _state(target, b)
    with pytest.raises(ProgressConflictError, match='no retained session'):
        _transfer(source, target, 'map', source_collab=a, target_collab=b)
    assert (_state(source, a), _state(target, b)) == before


def test_explicit_replacement_does_not_need_to_decode_malformed_target_xml() -> None:
    source, target = _main(populated=True), _main()
    a, b = _collab(LevelSide.A), _collab()
    b.sessions.add(SessionInput(_SID, '<broken'))
    _transfer(
        source,
        target,
        'map',
        source_collab=a,
        target_collab=b,
        discard_collab=True,
    )
    assert b.sessions[_SID].session.room == 'saved'


@pytest.mark.parametrize('field', ('ModSessionsPerLevel', 'ModSessionsPerLevelBinary'))
@pytest.mark.parametrize('value', ('[]', '{Map: []}', '{Map: {Example: []}}'))
def test_collab_payload_shapes_are_checked_at_read_boundary(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        CollabUtils2Save.fromstring(f'{field}: {value}\n')
