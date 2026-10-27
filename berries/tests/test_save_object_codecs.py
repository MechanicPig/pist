"""Object-level conversion preserves lazy input and never owns file submission."""

from copy import copy, deepcopy

import pytest
from lxml import etree
from pydantic import ValidationError
from yaml.nodes import MappingNode, SequenceNode

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.codecs.yaml import nodes
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress.map import MapProgress, PersonalBests
from berries.game.saves.progress.session import Session
from berries.game.saves.rooms import RoomStats, read_records
from berries.game.saves.session_input import SessionInput
from test_support.save_docs import parsed_session


def test_session_requires_identity_and_progress_and_preserves_assigned_values() -> None:
    duration = Duration.from_dotnet_ticks(17)
    position = (8.0, 16.0)
    session = parsed_session('Map', LevelSide.B, time_played=duration, respawn=position)
    assert session.time_played is duration and session.respawn is position
    with pytest.raises(ValidationError):
        Session.model_validate({})
    with pytest.raises(ValidationError, match='sid'):
        Session(sid='', side=LevelSide.A, room='', time_played=Duration(), deaths=0, room_deaths=0)


@pytest.mark.parametrize('deaths', [-1, 2147483648])
def test_session_edit_leaves_count_range_validation_to_output(deaths: int) -> None:
    session = parsed_session('Map', LevelSide.A, deaths=deaths)
    assert session.deaths == deaths
    with pytest.raises(ValueError):
        session.tostring()


@pytest.mark.parametrize('x', [float('inf'), float('nan')])
def test_session_edit_leaves_coordinate_validation_to_output(x: float) -> None:
    session = parsed_session('Map', LevelSide.A, respawn=(x, 0.0))
    with pytest.raises(ValueError, match='finite'):
        session.tostring()


def test_session_conversion_tracks_edits_without_changing_input() -> None:
    data = (
        '<Session InArea="true" Level="start" Time="00170000" Deaths="02" '
        'DeathsInCurrentLevel="1"><Area SID="Pack/Map" Mode="BSide"/><OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    assert session.sid == 'Pack/Map' and session.side is LevelSide.B
    assert not any(session.iter_changed_fields())
    assert Session.from_xml(tree.parse(session.tostring()).getroot()) == session
    session.deaths = 3
    assert tuple(session.iter_changed_fields()) == ('deaths',)
    result = session.tostring()
    assert 'Time="170000"' in result
    assert Session.from_xml(tree.parse(result).getroot()).deaths == 3
    assert Session.from_xml(tree.parse(data).getroot()).deaths == 2


def test_lazy_session_caches_one_fully_decoded_value(monkeypatch: pytest.MonkeyPatch) -> None:
    data = (
        '<Session InArea="true" Level="start" Time="00170000" Deaths="02" '
        'DeathsInCurrentLevel="1"><Area SID="Pack/Map" Mode="BSide"/>'
        '<RespawnPoint><X>8.000</X><Y>16.000</Y></RespawnPoint><OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    parsed: list[str | bytes] = []
    original = tree.parse

    def parse(value: str | bytes) -> etree.ElementTree:
        parsed.append(value)
        return original(value)

    monkeypatch.setattr(tree, 'parse', parse)
    pending = SessionInput('Pack/Map', data)
    assert pending.orig_xml == data
    assert not parsed
    session = pending.session
    assert session is pending.session and parsed == [data]
    assert session.side is LevelSide.B and session.in_level
    assert session.room == 'start' and session.time_played == Duration.from_timer_frames(1)
    assert session.deaths == 2 and session.room_deaths == 1 and session.respawn == (8, 16)
    assert not any(session.iter_changed_fields())
    session.deaths = 7
    assert tuple(session.iter_changed_fields()) == ('deaths',)
    assert Session.from_xml(original(session.tostring()).getroot()).deaths == 7
    with pytest.raises(AttributeError):
        session.sid = 'Other'  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError, match='frozen'):
        session.side = LevelSide.A  # pyright: ignore[reportAttributeAccessIssue]


def test_session_from_element_is_eager_and_does_not_reparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tree.parse(
        '<SaveData><Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
        '<Area SID="Map"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session><Other/></SaveData>'
    ).getroot()
    node = parent.find('Session')
    assert node is not None

    def fail_parse(data: str | bytes) -> etree.ElementTree:
        raise AssertionError('An existing element must not be parsed again.')

    with monkeypatch.context() as patch:
        patch.setattr(tree, 'parse', fail_parse)
        session = Session.from_xml(node)
        assert session.room == 'room' and session.deaths == 0
    node.set('Level', 'externally changed')
    session.deaths = 2
    encoded = session.tostring()
    assert 'Level="room"' in encoded
    assert '<SaveData' not in encoded and '<Other' not in encoded
    session.deaths = 0
    assert tuple(session.iter_changed_fields()) == ()


@pytest.mark.parametrize(
    'data',
    [
        '<Session Level="room"><Area SID="Map"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>',
        (
            '<Session Level="room" Time="invalid" Deaths="0" DeathsInCurrentLevel="0">'
            '<Area SID="Map"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
        ),
        (
            '<Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
            '<Area SID="Map"/><RespawnPoint><X>invalid</X><Y>0</Y></RespawnPoint><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
        ),
    ],
)
def test_session_factory_rejects_any_invalid_modeled_field(data: str) -> None:
    with pytest.raises(ValidationError):
        Session.from_xml(tree.parse(data).getroot())
    pending = SessionInput('Map', data)
    with pytest.raises(ValueError):
        pending.tostring()
    with pytest.raises(ValueError):
        pending.session  # noqa: B018


def test_respawn_change_query_uses_value_equality() -> None:
    session = Session.from_xml(
        tree.parse(
            '<Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
            '<Area SID="Map"/><RespawnPoint><X>8.000</X><Y>16.000</Y></RespawnPoint><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
        ).getroot()
    )
    session.respawn = (10, 16)
    assert tuple(session.iter_changed_fields()) == ('respawn',)
    session.respawn = (8, 16)
    assert tuple(session.iter_changed_fields()) == ()


def test_copies_of_decoded_sessions_keep_independent_edits() -> None:
    data = (
        '<Session InArea="true" Level="start" Time="170000" Deaths="2" '
        'DeathsInCurrentLevel="1"><Area SID="Pack/Map" Mode="Normal"/><OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    original = Session.from_xml(tree.parse(data).getroot())
    assert original.room == 'start' and original.deaths == 2
    for branch in (copy(original), deepcopy(original)):
        branch.deaths = 3
        assert branch.room == 'start' and branch.deaths == 3
        assert original.deaths == 2 and not any(original.iter_changed_fields())
        assert Session.from_xml(tree.parse(branch.tostring()).getroot()).deaths == 3


def test_noncurrent_session_keeps_identity_and_editable_fields() -> None:
    data = (
        '<Session InArea="false" Level="room" Time="170000" Deaths="2" '
        'DeathsInCurrentLevel="1"><Area SID="Pack/Map" Mode="BSide"/><OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    assert not session.in_level
    assert session.sid == 'Pack/Map' and session.side is LevelSide.B
    assert session.room == 'room' and session.deaths == 2 and session.room_deaths == 1
    assert not any(session.iter_changed_fields())
    assert Session.from_xml(tree.parse(session.tostring()).getroot()) == session
    session.in_level = True
    assert tuple(session.iter_changed_fields()) == ('in_level',)
    assert tree.parse(session.tostring()).getroot().get('InArea') == 'true'
    session.in_level = False
    assert not any(session.iter_changed_fields())


def test_missing_in_area_uses_game_constructor_default_without_rewriting() -> None:
    data = (
        '<Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
        '<Area SID="Map" Mode="CSide"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    assert session.in_level and session.side is LevelSide.C
    assert Session.from_xml(tree.parse(session.tostring()).getroot()) == session
    session.in_level = False
    root = tree.parse(session.tostring()).getroot()
    assert root.get('InArea') == 'false'


def test_nil_input_does_not_construct_a_session() -> None:
    data = '<Session xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>'
    with pytest.raises(ValueError, match='Nil Session'):
        Session.from_xml(tree.parse(data).getroot())


@pytest.mark.parametrize('in_level', [False, True])
def test_parsed_session_encodes_edited_in_level(in_level: bool) -> None:
    original = parsed_session('Map', LevelSide.A, in_level=in_level)
    restored = Session.from_xml(tree.parse(original.tostring()).getroot())
    assert restored.in_level is in_level and restored.side is LevelSide.A


def test_in_level_assignment_before_lazy_field_reads_is_not_overwritten() -> None:
    data = (
        '<Session InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
        '<Area SID="Map" Mode="CSide"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    session.in_level = False
    assert session.room == 'room' and session.deaths == 0 and session.side is LevelSide.C
    assert not session.in_level and tuple(session.iter_changed_fields()) == ('in_level',)
    assert tree.parse(session.tostring()).getroot().get('InArea') == 'false'


def test_session_resolves_vanilla_ids_and_preserves_context_for_edits() -> None:
    data = (
        '<CurrentSession InArea="true" Level="start" Time="00170000" Deaths="02" '
        'DeathsInCurrentLevel="1"><Area ID="4" Mode="BSide"/><OldStats ID="4"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    assert session.sid == 'Celeste/4-GoldenRidge' and session.side is LevelSide.B
    assert Session.from_xml(tree.parse(session.tostring()).getroot()) == session
    session.deaths = 3
    assert tuple(session.iter_changed_fields()) == ('deaths',)
    encoded = session.tostring()
    area = tree.parse(encoded).getroot().find('Area')
    assert area is not None and dict(area.attrib) == {
        'ID': '4',
        'Mode': 'BSide',
        'SID': 'Celeste/4-GoldenRidge',
    }
    assert 'Time="170000"' in encoded
    restored = Session.from_xml(tree.parse(encoded).getroot())
    assert restored.sid == session.sid and restored.side is LevelSide.B and restored.deaths == 3


def test_known_sid_does_not_substitute_for_a_missing_xml_sid() -> None:
    with pytest.raises(ValueError, match='Area/SID'):
        Session.from_xml(
            tree.parse(
                '<Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
                '<Area Mode="Normal"/><OldStats SID="Map"><Modes><AreaModeStats/>'
                '<AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
            ).getroot()
        )


def test_explicit_xml_sid_wins_over_vanilla_mapping() -> None:
    data = (
        '<CurrentSession InArea="true" Level="start" Time="0" Deaths="0" '
        'DeathsInCurrentLevel="0"><Area ID="4" SID="Existing/Map" Mode="CSide"/>'
        '<OldStats SID="Existing/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession>'
    )
    session = Session.from_xml(tree.parse(data).getroot())
    assert session.sid == 'Existing/Map' and session.side is LevelSide.C


def test_map_factory_validates_all_known_fields_during_loading() -> None:
    with pytest.raises(ValueError):
        MapProgress.from_xml(
            tree.parse(
                '<AreaModeStats TimePlayed="170000" Deaths="2" BestTime="invalid"/>'
            ).getroot(),
        )


def test_personal_bests_factory_uses_subclass_constructor() -> None:
    class CustomBests(PersonalBests):
        pass

    bests = CustomBests.from_xml(
        tree.parse(
            '<AreaModeStats SingleRunCompleted="true" BestTime="170000" '
            'BestDeaths="0" BestDashes="0"/>'
        ).getroot()
    )
    assert isinstance(bests, CustomBests)
    assert bests.time == Duration.from_timer_frames(1)
    assert bests.deaths == bests.dashes == 0


def test_room_stats_conversion_preserves_present_empty_fields() -> None:
    root = nodes.compose('room_order: []\nroom_deaths: {}\n')
    assert root is not None
    stats = RoomStats.from_yaml(nodes.mapping(root), key='Pack/Map', prefix='firstClear')
    encoded = stats.to_yaml()
    assert set(encoded) == {'room_order', 'room_deaths'}
    assert isinstance(encoded['room_order'], SequenceNode)
    assert isinstance(encoded['room_deaths'], MappingNode)
    assert all(not node.value for node in encoded.values())
    prepared = deepcopy(stats)
    prepared.room_deaths['start'] = 2
    assert not stats.room_deaths
    assert set(stats.to_yaml()) == set(encoded)


def test_room_record_conversion_rejects_invalid_stats_during_loading() -> None:
    root = nodes.compose('firstClear_death:\n  Pack/Map: {start: invalid}\n')
    assert root is not None
    with pytest.raises(ValueError, match='Invalid Blender statistics'):
        read_records(nodes.mapping(root), 'firstClear')


@pytest.mark.parametrize(
    ('fragment', 'present'),
    [
        ('', False),
        (
            (
                '<CurrentSession_Safe xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                'xsi:nil="true"/>'
            ),
            False,
        ),
        (
            (
                '<CurrentSession_Safe InArea="false" Level="r" Time="0" Deaths="0" '
                'DeathsInCurrentLevel="0"><Area SID="Map"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession_Safe>'
            ),
            True,
        ),
    ],
)
def test_mod_session_presence_distinguishes_inactive_from_missing(
    fragment: str, present: bool
) -> None:
    if 'xsi:nil="true"' in fragment:
        with pytest.raises(ValueError, match='Nil CurrentSession_Safe'):
            ModSaveData.from_xml(tree.parse(f'<SaveData>{fragment}</SaveData>').getroot())
        return
    mods = ModSaveData.from_xml(
        tree.parse(f'<SaveData>{fragment}</SaveData>').getroot(), strict=False
    )
    assert mods.mods.has_session_data is present
    assert mods.mods.current_map is None
    session = mods.safe_session
    if present:
        assert session is not None and not session.in_level
        assert session.sid == 'Map' and session.side is LevelSide.A
    else:
        assert session is None
    assert session is None or not any(session.iter_changed_fields())


def test_mod_session_is_fully_decoded_during_loading() -> None:
    node = parsed_session('Pack/Map', LevelSide.B).to_xml()
    node.tag = 'CurrentSession_Safe'
    node.set('Deaths', 'invalid')
    root = etree.Element('SaveData')
    root.append(node)
    with pytest.raises(ValidationError):
        ModSaveData.from_xml(root, strict=True)


def test_mod_session_edit_query_keeps_the_editable_session_reference() -> None:
    mods = ModSaveData.from_xml(
        tree.parse(
            '<SaveData><CurrentSession_Safe InArea="true" Level="start" Time="170000" '
            'Deaths="2" DeathsInCurrentLevel="1"><Area SID="Pack/Map" Mode="Normal"/>'
            '<OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession_Safe></SaveData>'
        ).getroot(),
        strict=False,
    )
    session = mods.safe_session
    assert session is not None and not any(session.iter_changed_fields())
    session.deaths = 3
    assert mods.safe_session is session and tuple(session.iter_changed_fields()) == ('deaths',)
    session.deaths = 2
    assert not any(session.iter_changed_fields())
    mods.safe_session = None
    assert not mods.mods.has_session_data and mods.mods.current_map is None
