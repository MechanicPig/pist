"""Transfer tests use real codecs but never open the installed game's saves."""

from copy import copy, deepcopy
from inspect import Parameter, signature
from io import BytesIO
from typing import Literal, cast

import pytest
import yaml
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.operations import (
    DEFAULT_POEM_ID,
    PoemID,
    TransferMode,
    copy_map_progress,
    move_campaign_progress,
    move_level_progress,
    move_map_progress,
)
from berries.game.saves.progress import CampaignProgress, LevelProgress, PersonalBests
from berries.game.saves.rooms import RoomStats, room_key
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from berries.map_entity_id import MapEntityID
from test_support.save_docs import (
    complete_vanilla,
    parsed_session,
    read_save,
    require_map,
    save_bytes,
)


def _main(*, populated: bool = True) -> MainSave:
    save = read_save(
        MainSave,
        complete_vanilla('<SaveData><Time>100</Time><TotalDeaths>100</TotalDeaths></SaveData>'),
    )
    campaign = CampaignProgress(
        'Pack', total_strawberries=10, unlocked_levels=2, poem=['pack_map_a', 'custom']
    )
    save.mods.campaigns.add(campaign)
    if populated:
        level = LevelProgress('Pack/Map', cassette=True)
        for progress in level.maps:
            assert progress is not None
            progress.time_played = Duration.from_dotnet_ticks(10)
            progress.deaths = 2
            progress.strawberries = {MapEntityID('room', 1)}
            progress.checkpoints = {'room'}
        campaign.levels.add(level)
    return save


def test_blender_copy_preserves_explicit_empty_fields_and_detaches_members() -> None:
    source, target = _main(), _main()
    source_blender = EndersBlenderSave.fromstring(
        'mapDict_roomStat_firstClear_roomOrder:\n  Pack/Map: []\n'
        'mapDict_roomStat_firstClear_death:\n  Pack/Map: {}\n'
    )
    target_blender = EndersBlenderSave()
    original = source_blender.first_clear['Pack/Map']
    copy_map_progress(
        source,
        target,
        'Pack/Map',
        LevelSide.A,
        mode='overwrite',
        poem_id=None,
        source_blender=source_blender,
        target_blender=target_blender,
        goldenberry_ids={},
    )
    encoded = yaml.safe_load(target_blender.tostring())
    assert encoded['mapDict_roomStat_firstClear_roomOrder']['Pack/Map'] == []
    assert encoded['mapDict_roomStat_firstClear_death']['Pack/Map'] == {}
    copied = target_blender.first_clear['Pack/Map']
    assert copied is not original
    for name in (
        'room_order',
        'room_deaths',
        'room_times',
        'room_rta_times',
        'room_strawberries',
        'pause_flags',
    ):
        assert getattr(copied, name) is not getattr(original, name)
    copied.room_deaths['start'] = 2
    assert not original.room_deaths


def test_missing_selected_source_progress_is_rejected_before_transfer() -> None:
    source, target = _main(), _main()
    level = LevelProgress.from_xml(
        etree.fromstring(
            '<AreaStats SID="Pack/Map"><Modes><AreaModeStats TimePlayed="0" Deaths="0"/></Modes></AreaStats>'
        ),
        strict=False,
    )
    source.mods.campaigns['Pack'].levels.replace(level)
    before = save_bytes(target)
    totals = source.time_played, source.deaths
    with pytest.raises(BadProgressSave, match='three'):
        move_map_progress(
            source, target, level.sid, LevelSide.B, mode='merge', poem_id=None, goldenberry_ids={}
        )
    assert save_bytes(target) == before
    assert (source.time_played, source.deaths) == totals
    assert source.mods.campaigns['Pack'].levels[level.sid] is level and len(level.maps) == 1


def test_level_transfer_moves_fixed_sides_and_keeps_history_offsets() -> None:
    source, target = _main(), _main(populated=False)
    held = source.flat_levels['Pack/Map']
    view = source.flat_levels
    move_level_progress(source, target, held.sid, mode='merge', poem_ids={}, goldenberry_ids={})
    assert source.mods.campaigns['Pack'].total_strawberries == 7
    assert target.mods.campaigns['Pack'].total_strawberries == 13
    assert source.time_played.to_dotnet_ticks() == 70 and source.deaths == 94
    assert target.time_played.to_dotnet_ticks() == 130 and target.deaths == 106
    assert not source.flat_levels[held.sid].cassette
    assert target.flat_levels[held.sid].cassette
    assert (
        require_map(held.maps[0]).deaths == 2
    )  # previously held values remain usable, now detached
    for side in LevelSide:
        assert require_map(view[held.sid].maps[side]).deaths == 0
        assert not require_map(view[held.sid].maps[side]).strawberries
        assert require_map(target.flat_levels[held.sid].maps[side]).deaths == 2
        assert require_map(target.flat_levels[held.sid].maps[side]) is not held.maps[side]
    assert not target.needs_mod_recovery
    restored = read_save(MainSave, save_bytes(target))
    assert require_map(restored.flat_levels[held.sid].maps[LevelSide.C]).deaths == 2


def test_merge_keeps_old_ids_and_records_including_zero_and_full_clear() -> None:
    source, target = _main(), _main()
    for progress in source.flat_levels['Pack/Map'].maps:
        assert progress is not None
        progress.single_run_completed = progress.full_clear = progress.completed = True
        progress.bests = PersonalBests(Duration.from_dotnet_ticks(5), Duration(), 0, 4)
        progress.strawberries.add(MapEntityID('new-version-room', 2))
    for progress in target.flat_levels['Pack/Map'].maps:
        assert progress is not None
        progress.single_run_completed = True
        progress.bests = PersonalBests(Duration.from_dotnet_ticks(8), Duration(), 3, 0)
    source.mods.campaigns['Pack'].poem = ['old_poem', 'new_poem', 'new_poem']
    target.mods.campaigns['Pack'].poem = ['old_poem']
    move_campaign_progress(
        source,
        target,
        'Pack',
        mode='merge',
        unlock_requirements={'Pack/Map': 1},
        goldenberry_ids={},
    )
    assert target.mods.campaigns['Pack'].total_strawberries == 13
    assert source.mods.campaigns['Pack'].total_strawberries == 4
    assert target.mods.campaigns['Pack'].poem == ['old_poem', 'new_poem']
    assert source.mods.campaigns['Pack'].poem == []
    for progress in target.flat_levels['Pack/Map'].maps:
        assert progress is not None
        assert progress.deaths == 4 and progress.time_played.to_dotnet_ticks() == 20
        assert progress.completed and progress.single_run_completed and progress.full_clear
        assert progress.bests == PersonalBests(Duration.from_dotnet_ticks(5), Duration(), 0, 0)
        assert len(progress.strawberries) == 2


def test_single_map_preserves_other_sides_and_level_cassette() -> None:
    source, target = _main(), _main()
    source_a, target_a = (
        require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A]),
        require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]),
    )
    move_map_progress(
        source, target, 'Pack/Map', LevelSide.B, mode='merge', poem_id=None, goldenberry_ids={}
    )
    assert require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A]) is source_a
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]) is target_a
    assert require_map(source.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 0
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 4
    assert source.flat_levels['Pack/Map'].cassette and target.flat_levels['Pack/Map'].cassette
    assert source.deaths == 98 and target.deaths == 102


def test_overwrite_replaces_target_scope_and_adjusts_global_delta() -> None:
    source, target = _main(), _main()
    target.mods.campaigns['Pack'].levels.add(LevelProgress('Pack/TargetOnly'))
    require_map(target.flat_levels['Pack/TargetOnly'].maps[LevelSide.A]).deaths = 9
    require_map(
        target.flat_levels['Pack/TargetOnly'].maps[LevelSide.A]
    ).time_played = Duration.from_dotnet_ticks(7)
    require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths = 5
    target.mods.campaigns['Pack'].poem = ['discard']
    move_campaign_progress(source, target, 'Pack', mode='overwrite', goldenberry_ids={})
    assert 'Pack/TargetOnly' not in target.flat_levels
    assert target.deaths == 88  # +6 source - (5+2+2+9) target
    assert target.time_played.to_dotnet_ticks() == 93
    assert target.mods.campaigns['Pack'].poem == ['pack_map_a', 'custom']
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 2


@pytest.mark.parametrize('poem', (None, DEFAULT_POEM_ID, 'CUSTOM'))
def test_single_map_poem_selection_is_explicit(poem: str | DEFAULT_POEM_ID | None) -> None:
    source, target = _main(), _main()
    target.mods.campaigns['Pack'].poem = ['unrelated']
    move_map_progress(
        source, target, 'Pack/Map', LevelSide.A, mode='merge', poem_id=poem, goldenberry_ids={}
    )
    selected = 'pack_map_a' if poem is DEFAULT_POEM_ID else 'custom' if poem else None
    assert target.mods.campaigns['Pack'].poem == ['unrelated'] + (
        [] if selected is None else [selected]
    )
    assert source.mods.campaigns['Pack'].poem == [
        value for value in ('pack_map_a', 'custom') if value != selected
    ]


def test_missing_mapping_keys_do_not_handle_other_sides_and_c_supports_custom_poem() -> None:
    source, target = _main(), _main()
    source.mods.campaigns['Pack'].poem += ['pack_map_b', 'c_custom']
    target.mods.campaigns['Pack'].poem.clear()
    move_level_progress(
        source,
        target,
        'Pack/Map',
        mode='merge',
        poem_ids={LevelSide.C: 'c_custom'},
        goldenberry_ids={},
    )
    assert target.mods.campaigns['Pack'].poem == ['c_custom']
    assert source.mods.campaigns['Pack'].poem == ['pack_map_a', 'custom', 'pack_map_b']


def test_c_has_no_invented_default_and_absent_registration_is_not_fabricated() -> None:
    source, target = _main(), _main()
    source.mods.campaigns['Pack'].poem.append('pack_map_c')
    target.mods.campaigns['Pack'].poem.clear()
    move_map_progress(
        source,
        target,
        'Pack/Map',
        LevelSide.C,
        mode='merge',
        poem_id=DEFAULT_POEM_ID,
        goldenberry_ids={},
    )
    assert 'pack_map_c' in source.mods.campaigns['Pack'].poem
    assert not target.mods.campaigns['Pack'].poem


def test_poem_parameters_are_required_keyword_only_choices() -> None:
    for function, name in ((move_map_progress, 'poem_id'), (move_level_progress, 'poem_ids')):
        parameter = signature(function).parameters[name]
        assert parameter.kind is Parameter.KEYWORD_ONLY
        assert parameter.default is Parameter.empty


@pytest.mark.parametrize('mode', ('overwrite', 'merge'))
@pytest.mark.parametrize(
    ('poem_ids', 'expected'),
    (
        ((None, None, None), []),
        ((DEFAULT_POEM_ID, None, 'C_CUSTOM'), ['pack_map_a', 'c_custom']),
        ((DEFAULT_POEM_ID, DEFAULT_POEM_ID, None), ['pack_map_a', 'pack_map_b']),
    ),
)
def test_level_poem_tuple_uses_a_b_c_order_without_implicit_defaults(
    mode: Literal['overwrite', 'merge'],
    poem_ids: tuple[PoemID, PoemID, PoemID],
    expected: list[str],
) -> None:
    source, target = _main(), _main()
    source.mods.campaigns['Pack'].poem = ['pack_map_a', 'pack_map_b', 'c_custom']
    target.mods.campaigns['Pack'].poem = ['unrelated']
    move_level_progress(
        source, target, 'Pack/Map', mode=mode, poem_ids=poem_ids, goldenberry_ids={}
    )
    assert target.mods.campaigns['Pack'].poem == ['unrelated', *expected]
    assert source.mods.campaigns['Pack'].poem == [
        value for value in ('pack_map_a', 'pack_map_b', 'c_custom') if value not in expected
    ]


@pytest.mark.parametrize(
    'invalid', ((), (None, None), (None,) * 4, [None] * 3, 'abc', (1, None, None))
)
def test_invalid_poem_tuple_is_rejected_before_either_history_changes(invalid: object) -> None:
    source, target = _main(), _main()
    before = save_bytes(source), save_bytes(target)
    with pytest.raises((TypeError, ValueError)):
        move_level_progress(
            source,
            target,
            'Pack/Map',
            mode='merge',
            poem_ids=cast(tuple[PoemID, PoemID, PoemID], invalid),
            goldenberry_ids={},
        )
    assert (save_bytes(source), save_bytes(target)) == before


def test_single_map_overwrite_changes_only_selected_counter_delta_and_poem() -> None:
    source, target = _main(), _main()
    require_map(target.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths = 7
    require_map(
        target.flat_levels['Pack/Map'].maps[LevelSide.B]
    ).time_played = Duration.from_dotnet_ticks(30)
    old_poem = source.mods.campaigns['Pack'].poem
    move_map_progress(
        source, target, 'Pack/Map', LevelSide.B, mode='overwrite', poem_id=None, goldenberry_ids={}
    )
    assert source.mods.campaigns['Pack'].poem is old_poem
    assert target.deaths == 95 and target.time_played.to_dotnet_ticks() == 80
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 2
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 2


def test_blender_overwrite_replaces_target_records_and_removes_target_only_timelines() -> None:
    source, target = _main(), _main()
    a, b = _blender(), _blender()
    a.first_clear[room_key('Pack/Map', LevelSide.A)] = RoomStats(
        ['source'], {'source': 0}, {'source': Duration()}
    )
    b.first_clear[room_key('Pack/Map', LevelSide.A)] = RoomStats(
        ['old'], {'old': 0}, {'old': Duration()}
    )
    b.last_saved[room_key('Pack/Map', LevelSide.C)] = RoomStats(
        ['old_c'], {'old_c': 0}, {'old_c': Duration()}
    )
    b.room_names['Pack/Map_B'] = {'r': 'Old'}
    b.room_names['Unrelated'] = {'r': 'Keep'}
    move_level_progress(
        source,
        target,
        'Pack/Map',
        mode='overwrite',
        poem_ids={},
        source_blender=a,
        target_blender=b,
        goldenberry_ids={},
    )
    assert b.first_clear[room_key('Pack/Map', LevelSide.A)].room_order == ['source']
    assert ('Pack/Map', LevelSide.C) not in b.last_saved
    assert b.room_names == {'Unrelated': {'r': 'Keep'}}


def test_overwrite_selected_poem_removes_target_registration_when_absent_in_source() -> None:
    source, target = _main(), _main()
    source.mods.campaigns['Pack'].poem.clear()
    move_map_progress(
        source,
        target,
        'Pack/Map',
        LevelSide.A,
        mode='overwrite',
        poem_id=DEFAULT_POEM_ID,
        goldenberry_ids={},
    )
    assert target.mods.campaigns['Pack'].poem == ['custom']


@pytest.mark.parametrize(
    'kind',
    (
        'missing_parent',
        'same_root',
        'underflow',
        'overflow',
        'berries',
        'shared_level',
        'shared_set',
        'shared_group',
    ),
)
def test_rejected_moves_leave_both_histories_unchanged(kind: str) -> None:
    source, target = _main(), _main(populated=False)
    if kind == 'missing_parent':
        target.mods.campaigns.clear()
    elif kind == 'same_root':
        target = source
    elif kind == 'underflow':
        source.deaths = 1
    elif kind == 'overflow':
        target.time_played = Duration.from_dotnet_ticks(2**63 - 1)
    elif kind == 'berries':
        source.mods.campaigns['Pack'].total_strawberries = 1
    elif kind == 'shared_level':
        target.mods.campaigns['Pack'].levels.add(source.flat_levels['Pack/Map'])
    elif kind == 'shared_set':
        target.mods.campaigns['Pack'].levels.add(LevelProgress('Pack/Map'))
        require_map(target.flat_levels['Pack/Map'].maps[LevelSide.B]).strawberries = require_map(
            source.flat_levels['Pack/Map'].maps[LevelSide.A]
        ).strawberries
    else:
        target.install_mod_progress(copy(source.mods))
    before = save_bytes(source), save_bytes(target)
    with pytest.raises((ValueError, KeyError)):
        move_level_progress(
            source, target, 'Pack/Map', mode='merge', poem_ids={}, goldenberry_ids={}
        )
    assert (save_bytes(source), save_bytes(target)) == before


def test_unrelated_shared_progress_remains_shared_without_rejection() -> None:
    source, target = _main(), _main(populated=False)
    other = LevelProgress('Pack/Other')
    source.mods.campaigns['Pack'].levels.add(other)
    target.mods.campaigns['Pack'].levels.add(other)
    move_level_progress(source, target, 'Pack/Map', mode='merge', poem_ids={}, goldenberry_ids={})
    assert source.flat_levels[other.sid] is target.flat_levels[other.sid] is other


def test_unknown_campaign_input_requires_an_explicit_loss_policy_before_transfer() -> None:
    data = save_bytes(_main()).replace(b'Name="Pack"', b'Name="Pack" extra="keep"')
    with pytest.raises(UnknownSaveFieldError):
        read_save(MainSave, data)
    with pytest.warns(UnknownSaveFieldWarning):
        source = MainSave.fromstring(data, strict=False, unknown='warn')
    target = _main()
    move_campaign_progress(source, target, 'Pack', mode='merge', goldenberry_ids={})
    assert b'extra="keep"' not in save_bytes(target)
    assert require_map(target.flat_levels['Pack/Map'].maps[0]).deaths == 4
    assert target.mods.campaigns['Pack'].total_strawberries == 10


def test_original_id_mapping_survives_movement_without_writing_sid() -> None:
    source = _main()
    source.vanilla.levels.add(LevelProgress('Celeste/1-ForsakenCity'))
    source.vanilla.poem = ['fc']
    data = save_bytes(source).replace(b'SID="Celeste/1-ForsakenCity" ', b'')
    source = MainSave.read(BytesIO(data), strict=False)
    target = _main()
    target.vanilla.levels.add(LevelProgress('Celeste/1-ForsakenCity'))
    move_map_progress(
        source,
        target,
        'Celeste/1-ForsakenCity',
        LevelSide.A,
        mode='merge',
        poem_id=DEFAULT_POEM_ID,
        goldenberry_ids={},
    )
    assert target.vanilla.poem == ['fc'] and not source.vanilla.poem
    assert b'SID="Celeste/1-ForsakenCity"' not in save_bytes(source)


def _blender() -> EndersBlenderSave:
    return read_save(EndersBlenderSave, '{}')


@pytest.mark.parametrize('mode', ('overwrite', 'merge', 'exclusive'))
def test_blender_transfers_both_families_names_and_colors(
    mode: TransferMode,
) -> None:
    source, target = _main(), _main(populated=False)
    a, b = _blender(), _blender()
    stats = RoomStats(['r'], {'r': 1}, {'r': Duration.from_dotnet_ticks(17)})
    a.first_clear[room_key('Pack/Map', LevelSide.B)] = stats
    a.last_saved[room_key('Pack/Map', LevelSide.C)] = deepcopy(stats)
    a.room_names['Pack/Map_B'] = {'r': 'Room'}
    a.room_colors['Pack/Map_C'] = {'r': 2}
    move_level_progress(
        source,
        target,
        'Pack/Map',
        mode=mode,
        poem_ids={},
        source_blender=a,
        target_blender=b,
        goldenberry_ids={},
    )
    assert not a.first_clear and not a.last_saved and not a.room_names
    assert b.first_clear[room_key('Pack/Map', LevelSide.B)] == stats
    assert b.first_clear[room_key('Pack/Map', LevelSide.B)] is not stats
    assert b.last_saved[room_key('Pack/Map', LevelSide.C)] == stats
    assert b.room_names == {'Pack/Map_B': {'r': 'Room'}}
    assert b.room_colors == {'Pack/Map_C': {'r': 2}}
    assert (
        read_save(EndersBlenderSave, save_bytes(b)).first_clear[room_key('Pack/Map', LevelSide.B)]
        == stats
    )


@pytest.mark.parametrize('kind', ('one_end', 'same_file', 'conflict', 'ambiguous'))
def test_blender_rejections_are_atomic_across_all_supplied_objects(kind: str) -> None:
    source, target = _main(), _main(populated=False)
    a, b = _blender(), _blender()
    a.first_clear[room_key('Pack/Map', LevelSide.A)] = RoomStats(['r'], {'r': 0}, {'r': Duration()})
    if kind == 'same_file':
        b = a
    elif kind == 'conflict':
        b.room_names['Pack/Map_B'] = {}
    elif kind == 'ambiguous':
        target.mods.campaigns['Pack'].levels.add(LevelProgress('Pack/Map_B'))
    before = tuple(map(save_bytes, (source, target, a, b)))
    with pytest.raises(ValueError):
        move_level_progress(
            source,
            target,
            'Pack/Map',
            mode='merge',
            poem_ids={},
            source_blender=a,
            target_blender=None if kind == 'one_end' else b,
            goldenberry_ids={},
        )
    assert tuple(map(save_bytes, (source, target, a, b))) == before


def test_map_berry_count_offset_is_not_duplicated_into_a_new_target() -> None:
    data = save_bytes(_main()).replace(b'TotalStrawberries="1"', b'TotalStrawberries="6"')
    source = read_save(MainSave, data)
    target = _main(populated=False)
    move_level_progress(source, target, 'Pack/Map', mode='merge', poem_ids={}, goldenberry_ids={})
    for save, expected in ((source, '5'), (target, '1')):
        root = etree.fromstring(save_bytes(save))
        nodes = root.findall('LevelSets/LevelSetStats/Areas/AreaStats/Modes/AreaModeStats')
        assert [node.get('TotalStrawberries') for node in nodes] == [expected] * 3


@pytest.mark.parametrize('mode', ('overwrite', 'merge'))
def test_campaign_move_rejects_unresolved_level_identity_before_changing_either_end(
    mode: Literal['overwrite', 'merge'],
) -> None:
    root = etree.fromstring(save_bytes(_main()))
    node = root.find('LevelSets/LevelSetStats/Areas/AreaStats')
    assert node is not None
    del node.attrib['SID']
    node.set('ID', '123')
    source = read_save(MainSave, etree.tostring(root))
    target = _main()
    campaign = source.mods.campaigns['Pack']
    unresolved = campaign.unresolved_levels[0]
    before = source.time_played, source.deaths, save_bytes(target)
    with pytest.raises(ValueError, match='identities'):
        move_campaign_progress(source, target, 'Pack', mode=mode, goldenberry_ids={})
    assert (source.time_played, source.deaths, save_bytes(target)) == before
    assert source.mods.campaigns['Pack'] is campaign
    assert campaign.unresolved_levels == [unresolved] and unresolved.id == 123


def test_roundtripped_campaign_counters_use_elements_and_preserve_historical_offset() -> None:
    source = read_save(MainSave, save_bytes(_main()))
    target = read_save(MainSave, save_bytes(_main()))
    assert source.mods.campaigns['Pack'].total_strawberries == 10
    move_campaign_progress(source, target, 'Pack', mode='merge', goldenberry_ids={})
    for save, expected in ((source, 7), (target, 10)):
        root = etree.fromstring(save_bytes(save))
        campaign = root.find('LevelSets/LevelSetStats')
        assert campaign is not None
        assert campaign.findtext('TotalStrawberries') == str(expected)
        assert 'TotalStrawberries' not in campaign.attrib
    assert target.mods.campaigns['Pack'].unlocked_levels == 2


def test_empty_poem_mapping_keeps_all_poem_entries_during_whole_level_move() -> None:
    source, target = _main(), _main()
    source.mods.campaigns['Pack'].poem += ['pack_map_b', 'c_custom']
    target.mods.campaigns['Pack'].poem = ['target']
    move_level_progress(
        source, target, 'Pack/Map', mode='overwrite', poem_ids={}, goldenberry_ids={}
    )
    assert source.mods.campaigns['Pack'].poem == ['pack_map_a', 'custom', 'pack_map_b', 'c_custom']
    assert target.mods.campaigns['Pack'].poem == ['target']


@pytest.mark.parametrize('nested', (False, True))
def test_blender_overwrite_rejects_shared_participating_stats_or_nested_state(
    nested: bool,
) -> None:
    source, target = _main(), _main()
    a, b = _blender(), _blender()
    stats = RoomStats(['r'], {'r': 1, 'other': 0}, {'r': Duration()})
    other = RoomStats(['other'], stats.room_deaths, {'other': Duration()}) if nested else stats
    a.first_clear[room_key('Pack/Map', LevelSide.A)] = stats
    b.last_saved[room_key('Pack/Map', LevelSide.B)] = other
    before = tuple(map(save_bytes, (source, target, a, b)))
    with pytest.raises(ValueError, match='overlap'):
        move_level_progress(
            source,
            target,
            'Pack/Map',
            mode='overwrite',
            poem_ids={},
            source_blender=a,
            target_blender=b,
            goldenberry_ids={},
        )
    assert tuple(map(save_bytes, (source, target, a, b))) == before


@pytest.mark.parametrize('scope', ('map', 'level', 'campaign'))
@pytest.mark.parametrize('existing', (False, True))
def test_exclusive_accepts_missing_or_empty_target_progress(scope: str, existing: bool) -> None:
    source, target = _main(), _main(populated=False)
    target.mods.campaigns['Pack'].reset()
    if existing:
        target.mods.campaigns['Pack'].levels.add(LevelProgress('Pack/Map'))
        # Prove loaded default XML is also empty, not just newly constructed objects.
        target = read_save(MainSave, save_bytes(target))
    elif scope == 'campaign':
        del target.mods.campaigns['Pack']
    if scope == 'map':
        move_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.B,
            mode='exclusive',
            poem_id=None,
            goldenberry_ids={},
        )
        assert not target.flat_levels['Pack/Map'].cassette
        assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 0
        assert require_map(source.flat_levels['Pack/Map'].maps[LevelSide.A]).deaths == 2
        assert target.deaths == 102 and source.deaths == 98
    elif scope == 'level':
        move_level_progress(
            source, target, 'Pack/Map', mode='exclusive', poem_ids={}, goldenberry_ids={}
        )
        assert target.deaths == 106 and source.deaths == 94
    else:
        move_campaign_progress(source, target, 'Pack', mode='exclusive', goldenberry_ids={})
        assert target.deaths == 106 and source.deaths == 94
        assert target.mods.campaigns['Pack'].poem == ['pack_map_a', 'custom']
    restored = read_save(MainSave, save_bytes(target))
    assert require_map(restored.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 2


@pytest.mark.parametrize(
    'field',
    (
        'time_played',
        'deaths',
        'completed',
        'single_run_completed',
        'full_clear',
        'heart',
        'strawberries',
        'checkpoints',
        'bests',
        'cassette',
        'offset',
        'poem',
        'session',
    ),
)
def test_exclusive_map_rejects_nonempty_selected_progress_atomically(field: str) -> None:
    source, target = _main(), _main(populated=False)
    target.mods.campaigns['Pack'].reset()
    level = LevelProgress('Pack/Map')
    target.mods.campaigns['Pack'].levels.add(level)
    progress = require_map(level.maps[LevelSide.A])
    if field == 'time_played':
        progress.time_played = Duration.from_dotnet_ticks(-1)
    elif field == 'deaths':
        progress.deaths = 1
    elif field in ('completed', 'full_clear', 'heart'):
        setattr(progress, field, True)
    elif field == 'single_run_completed':
        progress.single_run_completed = True
        progress.bests = PersonalBests(deaths=0, dashes=0)
    elif field == 'strawberries':
        progress.strawberries.add(MapEntityID('r', 1))
    elif field == 'checkpoints':
        progress.checkpoints.add('cp')
    elif field == 'bests':
        progress.bests.time = Duration.from_dotnet_ticks(1)
    elif field == 'cassette':
        level.cassette = True
    elif field == 'offset':
        root = etree.fromstring(save_bytes(target))
        node = root.find('LevelSets/LevelSetStats/Areas/AreaStats/Modes/AreaModeStats')
        assert node is not None
        node.set('TotalStrawberries', '1')
        target = read_save(MainSave, etree.tostring(root))
    elif field == 'poem':
        target.mods.campaigns['Pack'].poem = ['pack_map_a']
    else:
        target.session = parsed_session('Pack/Map', LevelSide.A)
    before = save_bytes(source), save_bytes(target)
    with pytest.raises(ValueError):
        move_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            mode='exclusive',
            poem_id=DEFAULT_POEM_ID,
            goldenberry_ids={},
        )
    assert (save_bytes(source), save_bytes(target)) == before


@pytest.mark.parametrize('field', ('unlocked', 'berries', 'poem', 'other_level'))
def test_exclusive_campaign_checks_all_target_metadata_and_levels(field: str) -> None:
    source, target = _main(), _main(populated=False)
    campaign = target.mods.campaigns['Pack']
    campaign.reset()
    if field == 'unlocked':
        campaign.unlocked_levels = 1
    elif field == 'berries':
        campaign.total_strawberries = 1
    elif field == 'poem':
        campaign.poem.append('other')
    else:
        level = LevelProgress('Pack/Other')
        require_map(level.maps[0]).deaths = 1
        campaign.levels.add(level)
    before = save_bytes(source), save_bytes(target)
    with pytest.raises(ValueError):
        move_campaign_progress(source, target, 'Pack', mode='exclusive', goldenberry_ids={})
    assert (save_bytes(source), save_bytes(target)) == before


def test_exclusive_blender_conflict_does_not_install_prepared_main_progress() -> None:
    source, target = _main(), _main(populated=False)
    a, b = _blender(), _blender()
    b.last_saved[room_key('Pack/Map', LevelSide.A)] = RoomStats(['r'], {'r': 0}, {'r': Duration()})
    before = tuple(map(save_bytes, (source, target, a, b)))
    with pytest.raises(ValueError, match='already exists'):
        move_level_progress(
            source,
            target,
            'Pack/Map',
            mode='exclusive',
            poem_ids={},
            source_blender=a,
            target_blender=b,
            goldenberry_ids={},
        )
    assert tuple(map(save_bytes, (source, target, a, b))) == before


@pytest.mark.parametrize('mode', ('overwrite', 'merge', 'exclusive'))
@pytest.mark.parametrize('side', tuple(LevelSide))
def test_map_transfer_treats_cassette_as_a_side_progress(
    mode: Literal['overwrite', 'merge', 'exclusive'],
    side: LevelSide,
) -> None:
    source, target = _main(), _main(populated=False)
    target.mods.campaigns['Pack'].levels.add(LevelProgress('Pack/Map'))
    move_map_progress(source, target, 'Pack/Map', side, mode=mode, poem_id=None, goldenberry_ids={})
    assert source.flat_levels['Pack/Map'].cassette is (side is not LevelSide.A)
    assert target.flat_levels['Pack/Map'].cassette is (side is LevelSide.A)
    restored = read_save(MainSave, save_bytes(target))
    assert restored.flat_levels['Pack/Map'].cassette is (side is LevelSide.A)


@pytest.mark.parametrize('mode', ('overwrite', 'merge'))
@pytest.mark.parametrize('side', tuple(LevelSide))
def test_missing_source_cassette_only_overwrites_target_when_moving_a(
    mode: Literal['overwrite', 'merge'],
    side: LevelSide,
) -> None:
    source, target = _main(), _main()
    source.flat_levels['Pack/Map'].cassette = False
    move_map_progress(source, target, 'Pack/Map', side, mode=mode, poem_id=None, goldenberry_ids={})
    assert target.flat_levels['Pack/Map'].cassette is not (
        mode == 'overwrite' and side is LevelSide.A
    )


@pytest.mark.parametrize('group', (b'Areas', b'Modes', b'Strawberries'))
def test_external_processing_instruction_is_ignored_not_mistaken_for_a_codec_slot(
    group: bytes,
) -> None:
    data = save_bytes(_main())
    opening = b'<' + group + b'>'
    before, separator, mods = data.partition(b'<LevelSets>')
    data = (
        before
        + separator
        + mods.replace(opening, opening + b'<?berries-slot external-extension?>', 1)
    )
    source, target = read_save(MainSave, data), _main()
    move_campaign_progress(source, target, 'Pack', mode='merge', goldenberry_ids={})
    assert b'berries-slot' not in save_bytes(target)
    assert require_map(target.flat_levels['Pack/Map'].maps[0]).deaths == 4


@pytest.mark.parametrize('operation', ['copy', 'move'])
@pytest.mark.parametrize('donor', [True, False])
def test_entity_ignored_text_does_not_hide_identity_or_prevent_transfer(
    operation: Literal['copy', 'move'],
    donor: bool,
) -> None:
    source, target = _main(), _main()
    data = save_bytes(source if donor else target)
    data = data.replace(b'Key="room:1"/>', b'Key="room:1">unknown-extension</EntityID>', 1)
    if donor:
        source = read_save(MainSave, data)
    else:
        target = read_save(MainSave, data)
    transfer = copy_map_progress if operation == 'copy' else move_map_progress
    transfer(
        source, target, 'Pack/Map', LevelSide.A, poem_id=None, mode='merge', goldenberry_ids={}
    )
    assert require_map(target.flat_levels['Pack/Map'].maps[0]).strawberries == {
        MapEntityID('room', 1)
    }
    assert b'unknown-extension' not in save_bytes(target)
    assert bool(require_map(source.flat_levels['Pack/Map'].maps[0]).strawberries) is (
        operation == 'copy'
    )


def test_entity_whitespace_and_checkpoint_text_remain_supported_by_merge() -> None:
    source, target = _main(), _main()
    data = save_bytes(source).replace(b'Key="room:1"/>', b'Key="room:1"> \n </EntityID>', 1)
    source = read_save(MainSave, data)
    copy_map_progress(
        source, target, 'Pack/Map', LevelSide.A, poem_id=None, mode='merge', goldenberry_ids={}
    )
    assert require_map(target.flat_levels['Pack/Map'].maps[LevelSide.A]).checkpoints == {'room'}
