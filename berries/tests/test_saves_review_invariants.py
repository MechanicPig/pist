"""Game-consumer regressions from the 2026-10-21 saves review."""

from copy import copy
from io import BytesIO

import pytest
import yaml
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves import operations
from berries.game.saves.codecs.xml import tree
from berries.game.saves.errors import BadProgressSave, UnsupportedProgressError
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress import (
    CampaignProgress,
    LevelProgress,
    MapProgress,
    PersonalBests,
    Session,
)
from berries.game.saves.rooms import RoomStats
from berries.game.vanilla import VANILLA_SIDS
from berries.map_entity_id import MapEntityID
from test_support.save_docs import parsed_session, require_map


def _mod_save() -> MainSave:
    save = MainSave()
    save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    return save


@pytest.mark.parametrize('field', ['Name', 'Time', 'TotalDeaths', 'AssistMode', 'VariantMode'])
def test_strict_rejects_missing_main_fields_but_lenient_uses_game_defaults(field: str) -> None:
    root = etree.fromstring(MainSave().tostring())
    node = root.find(field)
    assert node is not None
    root.remove(node)
    source = etree.tostring(root)
    with pytest.raises(ValueError, match=field):
        MainSave.fromstring(source)
    save = MainSave.fromstring(source, strict=False)
    assert save.time_played == Duration() and save.deaths == 0
    assert save.name == 'Madeline'
    repaired = etree.fromstring(save.tostring())
    assert repaired.find(field) is not None


@pytest.mark.parametrize('field', ['TimePlayed', 'Deaths', 'Completed', 'BestTime'])
def test_strict_map_fields_are_checked_before_a_property_is_accessed(field: str) -> None:
    root = etree.fromstring(MainSave().tostring())
    node = root.find('Areas/AreaStats/Modes/AreaModeStats')
    assert node is not None
    del node.attrib[field]
    source = etree.tostring(root)
    with pytest.raises(ValueError, match=field):
        MainSave.fromstring(source)
    lenient = MainSave.fromstring(source, strict=False)
    assert (
        require_map(lenient.flat_levels[VANILLA_SIDS[0]].maps[LevelSide.A]).time_played
        == Duration()
    )
    MainSave.fromstring(lenient.tostring())


@pytest.mark.parametrize('key', ['room:2147483648', 'room:extra:1', 'room:١'])
def test_entity_keys_cannot_escape_the_game_parser(key: str) -> None:
    with pytest.raises(ValueError):
        MapEntityID.parse(key)
    root = etree.fromstring(MainSave().tostring())
    berries = root.find('Areas/AreaStats/Modes/AreaModeStats/Strawberries')
    assert berries is not None
    etree.SubElement(berries, 'EntityID', Key=key)
    with pytest.raises(ValueError):
        MainSave.fromstring(etree.tostring(root))


@pytest.mark.parametrize('entity', [MapEntityID('room', 2**31), MapEntityID('room:extra', 1)])
def test_invalid_entity_write_does_not_touch_destination(entity: MapEntityID) -> None:
    save = MainSave()
    require_map(save.flat_levels[VANILLA_SIDS[0]].maps[LevelSide.A]).strawberries.add(entity)
    stream = BytesIO(b'untouched')
    with pytest.raises(ValueError):
        save.write(stream)
    assert stream.getvalue() == b'untouched'


@pytest.mark.parametrize('modes', ['', '<Modes/>', '<Modes><AreaModeStats/></Modes>'])
def test_existing_session_old_stats_defaults_do_not_repair_explicit_bad_modes(modes: str) -> None:
    data = (
        '<Session InArea="false" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
        f'<Area SID="Pack/Map"/><OldStats SID="Pack/Map">{modes}</OldStats></Session>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    if not modes:
        assert session.old_stats is not None and len(session.old_stats.maps) == 3
        assert Session.from_xml(tree.parse(session.tostring()).getroot()).old_stats is not None
        return
    with pytest.raises(ValueError, match='OldStats'):
        session.tostring()
    source = yaml.safe_dump({'SessionsPerLevel': {'Pack/Map': data}})
    collab = CollabUtils2Save.fromstring(source)
    stream = BytesIO(b'original')
    with pytest.raises(ValueError, match='OldStats'):
        collab.write(stream)
    assert stream.getvalue() == b'original'


@pytest.mark.parametrize(
    'field', ['time', 'full_clear_time', 'deaths', 'session_time', 'session_deaths']
)
def test_correction_preflights_bests_and_session_without_partial_edits(field: str) -> None:
    save = _mod_save()
    save.time_played = Duration.from_seconds(100)
    save.deaths = 100
    progress = require_map(save.flat_levels['Pack/Map'].maps[LevelSide.A])
    progress.time_played = Duration.from_seconds(10)
    progress.deaths = 10
    if field.startswith('session'):
        save.session = parsed_session(
            'Pack/Map', LevelSide.A, time_played=Duration.from_seconds(8), deaths=8
        )
    elif field == 'deaths':
        progress.single_run_completed = True
        progress.bests = PersonalBests(deaths=3, dashes=0)
    else:
        setattr(progress.bests, field, Duration.from_seconds(5))
    before = save.tostring()
    if 'deaths' in field:
        with pytest.raises(ValueError):
            operations.correct_map_progress(save, 'Pack/Map', LevelSide.A, deaths=0)
    else:
        with pytest.raises(ValueError):
            operations.correct_map_progress(
                save, 'Pack/Map', LevelSide.A, time_played=Duration.from_seconds(1)
            )
    assert save.tostring() == before


@pytest.mark.parametrize('side', list(LevelSide))
def test_vanilla_completion_unlocks_the_next_chapter_for_every_side(side: LevelSide) -> None:
    source, target = MainSave(), MainSave()
    require_map(source.flat_levels[VANILLA_SIDS[1]].maps[side]).completed = True
    operations.copy_map_progress(
        source, target, VANILLA_SIDS[1], side, poem_id=None, mode='overwrite', goldenberry_ids={}
    )
    assert target.vanilla.unlocked_levels == 2
    assert MainSave.fromstring(target.tostring()).vanilla.unlocked_levels == 2


def test_mod_completion_needs_explicit_facts_before_either_save_changes() -> None:
    source, target = _mod_save(), _mod_save()
    require_map(source.flat_levels['Pack/Map'].maps[LevelSide.B]).completed = True
    before = source.tostring(), target.tostring()
    with pytest.raises(UnsupportedProgressError, match='unlock'):
        operations.move_level_progress(
            source, target, 'Pack/Map', mode='overwrite', poem_ids={}, goldenberry_ids={}
        )
    assert (source.tostring(), target.tostring()) == before
    operations.move_level_progress(
        source,
        target,
        'Pack/Map',
        mode='overwrite',
        poem_ids={},
        unlock_requirements={'Pack/Map': 4},
        goldenberry_ids={},
    )
    assert target.mods.campaigns['Pack'].unlocked_levels == 4


@pytest.mark.parametrize('field', ['room_deaths', 'room_times'])
@pytest.mark.parametrize('materialize', [False, True])
def test_blender_required_room_values_are_checked_even_without_querying(
    field: str, materialize: bool
) -> None:
    save = EndersBlenderSave()
    stats = RoomStats(['r'], {'r': 1}, {'r': Duration()})
    getattr(stats, field).clear()
    save.first_clear['Map'] = stats
    if not materialize:
        suffix = 'timer' if field == 'room_deaths' else 'death'
        save = EndersBlenderSave.fromstring(
            f'mapDict_roomStat_firstClear_roomOrder: {{Map: [r]}}\n'
            f'mapDict_roomStat_firstClear_{suffix}: {{Map: {{r: 0}}}}'
        )
    with pytest.raises(ValueError, match='Missing Blender room'):
        save.tostring()


@pytest.mark.parametrize('tagged_target', [False, True])
def test_blender_transfers_refuse_affected_tags_atomically(tagged_target: bool) -> None:
    source, target = _mod_save(), _mod_save()
    tagged = EndersBlenderSave.fromstring(
        'mapDict_roomStat_firstClear_death: {Pack/Map: {r: !custom 2}}'
    )
    plain = EndersBlenderSave()
    a, b = (plain, tagged) if tagged_target else (tagged, plain)
    before = source.tostring(), target.tostring(), a.tostring(), b.tostring()
    with pytest.raises(UnsupportedProgressError, match='tagged'):
        operations.copy_map_progress(
            source,
            target,
            'Pack/Map',
            LevelSide.A,
            poem_id=None,
            mode='overwrite',
            source_blender=a,
            target_blender=b,
            goldenberry_ids={},
        )
    assert (source.tostring(), target.tostring(), a.tostring(), b.tostring()) == before


def test_blender_unrelated_tags_do_not_block_transfer() -> None:
    source, target = _mod_save(), _mod_save()
    a = EndersBlenderSave.fromstring(
        'mapDict_roomStat_firstClear_death: {Other: {r: !custom 2}, Pack/Map: {r: 1}}'
    )
    b = EndersBlenderSave()
    operations.copy_map_progress(
        source,
        target,
        'Pack/Map',
        LevelSide.A,
        poem_id=None,
        mode='overwrite',
        source_blender=a,
        target_blender=b,
        goldenberry_ids={},
    )
    assert b.first_clear['Pack/Map'].room_deaths == {'r': 1}
    assert '!custom' in a.tostring()


def test_room_records_shallow_copy_has_independent_storage_and_shared_members() -> None:
    stats = RoomStats(room_deaths={'r': 1})
    original = {'Map': stats}
    branch = copy(original)
    assert branch['Map'] is stats
    del branch['Map']
    assert original['Map'] is stats and not branch
    loaded = EndersBlenderSave.fromstring(
        'mapDict_roomStat_firstClear_death: {Map: {r: 1}}'
    ).first_clear
    branch = loaded.copy()
    assert branch['Map'] is loaded['Map']
    del branch['Map']
    assert 'Map' in loaded and not branch


def test_celeste_entry_requires_empty_poem_and_mirrors_root_counters() -> None:
    source = (
        MainSave()
        .tostring()
        .replace(
            '<LevelSets/>',
            '<LevelSets><LevelSetStats Name="Celeste"><UnlockedAreas>0</UnlockedAreas>'
            '<TotalStrawberries>0</TotalStrawberries><Poem><string>metadata</string></Poem>'
            '</LevelSetStats></LevelSets>',
        )
    )
    save = MainSave.fromstring(source, strict=False)
    save.vanilla.unlocked_levels = 4
    save.vanilla.total_strawberries = 7
    entry = save.mods.vanilla_entry
    assert entry is not None and entry.poem == ['metadata']
    with pytest.raises(BadProgressSave, match='Poem'):
        save.tostring()
    assert entry.poem == ['metadata']
    entry.poem.clear()
    node = etree.fromstring(save.tostring()).find('LevelSets/LevelSetStats')
    assert node is not None
    assert node.findtext('UnlockedAreas') == '4'
    assert node.findtext('TotalStrawberries') == '7'
    assert node.findall('Poem/string') == []
    assert entry.unlocked_levels == 0 and entry.total_strawberries == 0
    restored = MainSave.fromstring(save.tostring())
    assert restored.mods.vanilla_entry is not None
    assert restored.mods.vanilla_entry.unlocked_levels == restored.vanilla.unlocked_levels == 4
    assert (
        restored.mods.vanilla_entry.total_strawberries == restored.vanilla.total_strawberries == 7
    )


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('total', ['not-int', '2147483648'])
def test_invalid_berry_totals_are_rejected_without_accessing_members(
    total: str, strict: bool
) -> None:
    root = etree.fromstring(MainSave().tostring())
    node = root.find('Areas/AreaStats/Modes/AreaModeStats')
    assert node is not None
    node.set('TotalStrawberries', total)
    with pytest.raises(ValueError, match='TotalStrawberries'):
        MainSave.fromstring(etree.tostring(root), strict=strict)


def test_missing_berry_count_uses_zero_for_noop_delta_and_offset() -> None:
    node = etree.fromstring(
        '<AreaModeStats TimePlayed="0" Deaths="0">'
        '<Strawberries><EntityID Key="r:1"/></Strawberries></AreaModeStats>'
    )
    progress = MapProgress.from_xml(node)
    assert progress.berry_offset() == -1
    assert etree.fromstring(progress.tostring()).get('TotalStrawberries') == '0'
    progress.strawberries.add(MapEntityID('r', 2))
    encoded = progress.tostring()
    assert etree.fromstring(encoded).get('TotalStrawberries') == '1'
    assert MapProgress.from_xml(etree.fromstring(encoded)).berry_offset() == -1


@pytest.mark.parametrize('total', ['0', '17'])
def test_unedited_berry_count_is_canonicalized_without_changing_value(total: str) -> None:
    node = etree.fromstring(f'<AreaModeStats TotalStrawberries="{total.zfill(4)}"/>')
    progress = MapProgress.from_xml(node)
    assert etree.fromstring(progress.tostring()).get('TotalStrawberries') == total


def test_unknown_vanilla_session_is_rejected_without_fabricating_an_identity() -> None:
    data = (
        MainSave()
        .tostring()
        .replace(
            '</SaveData>',
            '<CurrentSession InArea="true" Level="r" Time="0" Deaths="0" '
            'DeathsInCurrentLevel="0"><Area ID="999"/></CurrentSession></SaveData>',
        )
    )
    with pytest.raises(ValueError, match=r'Unknown.*session'):
        MainSave.fromstring(data)
    with pytest.raises(ValueError, match=r'Unknown.*session'):
        MainSave.fromstring(data, strict=False)


@pytest.mark.parametrize('in_level', [False, True])
@pytest.mark.parametrize('field', ['time', 'deaths'])
def test_correction_preserves_session_history_even_after_the_run_ends(
    in_level: bool, field: str
) -> None:
    save = _mod_save()
    save.time_played = Duration.from_seconds(100)
    save.deaths = 100
    progress = require_map(save.flat_levels['Pack/Map'].maps[LevelSide.B])
    progress.time_played = Duration.from_seconds(18)
    progress.deaths = 11
    node = etree.fromstring(
        parsed_session(
            'Pack/Map',
            LevelSide.B,
            in_level=in_level,
            time_played=Duration.from_seconds(8),
            deaths=8,
        ).tostring()
    )
    old = node.findall('OldStats/Modes/AreaModeStats')[1]
    old.set('TimePlayed', str(Duration.from_seconds(10).to_dotnet_ticks()))
    old.set('Deaths', '3')
    save.session = Session.from_xml(node)
    before = save.tostring()
    if field == 'time':
        with pytest.raises(ValueError, match='OldStats'):
            operations.correct_map_progress(
                save, 'Pack/Map', LevelSide.B, time_played=Duration.from_seconds(8)
            )
    else:
        with pytest.raises(ValueError, match='OldStats'):
            operations.correct_map_progress(save, 'Pack/Map', LevelSide.B, deaths=8)
    assert save.tostring() == before
    assert operations.correct_map_progress(
        save,
        'Pack/Map',
        LevelSide.B,
        time_played=Duration.from_seconds(18),
        deaths=11,
    ) == (Duration(), 0)


@pytest.mark.parametrize('last_saved', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_blender_output_provides_journal_auxiliary_containers(
    last_saved: bool, empty: bool
) -> None:
    save = EndersBlenderSave()
    records = save.last_saved if last_saved else save.first_clear
    stats = RoomStats() if empty else RoomStats(['r'], {'r': 0}, {'r': Duration()})
    records['Pack/Map'] = stats
    before_names, before_colors = dict(save.room_names), dict(save.room_colors)
    encoded = save.tostring()
    data = yaml.safe_load(encoded)
    assert data['mapDict_roomStatCustomNameDict']['Pack/Map'] == {}
    if last_saved or not empty:
        prefix = 'latestSession' if last_saved else 'firstClear'
        assert data['mapDict_roomStat_colorIndex']['Pack/Map'] == {}
        assert data[f'mapDict_roomStat_{prefix}_pauseType']['Pack/Map'] == {}
    assert save.room_names == before_names and save.room_colors == before_colors
    assert save.tostring() == encoded
    assert EndersBlenderSave.fromstring(encoded).tostring() == encoded
