"""Complete Session fields, structured editing and nested unknown-field policies."""

import struct
from copy import deepcopy
from typing import Annotated

import pytest
from lxml import etree
from pydantic import ValidationError

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.session import Session
from berries.game.saves.progress.session_state import AudioState, PlayerInventory
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from berries.game.vanilla import VANILLA_SIDS
from berries.map_entity_id import MapEntityID
from berries.model_behavior import FieldOptions, PrivateAttr

_SESSION = """<Session Level="room" Time="170000" Deaths="4" DeathsInCurrentLevel="2"
 InArea="true" FirstLevel="false" DarkRoomAlpha="0.1" StartedFromBeginning="true"
 Dashes="8" DashesAtLevelStart="3" StartCheckpoint="checkpoint" Cassette="true"
 HeartGem="true" Dreaming="true" ColorGrade="cold" LightingAlphaAdd="0.2"
 BloomBaseAdd="0.3" CoreMode="Cold" GrabbedGolden="true" HitCheckpoint="true">
 <Area ID="42" SID="Pack/Map" Mode="BSide"/>
 <RespawnPoint><X>-0</X><Y>0.1</Y></RespawnPoint>
 <Audio><Music Event="event:/music/test"><Parameters><MEP Key="progress" Value="0.1"/></Parameters></Music>
 <Ambience><Parameters><MEP Value="0.5"/></Parameters></Ambience><AmbienceVolume>0.25</AmbienceVolume></Audio>
 <Inventory><Dashes>2</Dashes><DreamDash>true</DreamDash><Backpack>true</Backpack><NoRefills>true</NoRefills></Inventory>
 <OldStats ID="42" SID="Pack/Map" Cassette="true"><Modes>
 <AreaModeStats Deaths="2"/><AreaModeStats Deaths="3"/><AreaModeStats Deaths="4"/>
 </Modes></OldStats>
 <Flags><string>flag</string></Flags><LevelFlags><string>visited</string></LevelFlags>
 <Strawberries><EntityID Key="room:1"/></Strawberries><DoNotLoad><EntityID Key="room:2"/></DoNotLoad>
 <Keys><EntityID Key="room:3"/></Keys><Counters><Counter key="count" value="-2"/><Counter key="count" value="3"/></Counters>
 <SummitGems><boolean>true</boolean><boolean>false</boolean><boolean>false</boolean>
 <boolean>false</boolean><boolean>false</boolean><boolean>false</boolean></SummitGems>
 <UnlockedCSide>true</UnlockedCSide><FurthestSeenLevel>goal</FurthestSeenLevel><BeatBestTime>true</BeatBestTime>
 <RestartedFromGolden>true</RestartedFromGolden><Sliders><Slider Name="slider" Value="0.1"/></Sliders>
 </Session>"""


def test_session_constructor_establishes_complete_initial_edit_baseline() -> None:
    session = Session(
        sid='Pack/Map',
        side=LevelSide.B,
        room='room',
        time_played=Duration(),
        deaths=0,
        room_deaths=0,
        old_stats=LevelProgress('Pack/Map'),
    )
    assert not any(session.iter_changed_fields())
    assert Session.from_xml(session.to_xml()) == session
    assert not any(Session.from_xml(session.to_xml()).iter_changed_fields())
    session.deaths = 1
    assert tuple(session.iter_changed_fields()) == ('deaths',)
    with pytest.raises(AttributeError):
        del session.sid  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError, match='frozen'):
        del session.side


def test_session_snapshots_follow_comparison_metadata_for_added_fields() -> None:
    class Extended(Session):
        extra: int = 0
        note: Annotated[str, FieldOptions(compare=False)] = ''
        _score: int = PrivateAttr(alias='score', default=0)
        _cache: int = PrivateAttr(default=0, compare=False)

        @property
        def score(self) -> int:
            return self._score

        @score.setter
        def score(self, value: int) -> None:
            self._score = value

    value = Extended(
        sid='Pack/Map', side=LevelSide.A, room='', time_played=Duration(), deaths=0, room_deaths=0
    )
    other = deepcopy(value)
    value.note = 'ignored'
    assert value == other and not any(value.iter_changed_fields())
    value.extra = 1
    value.score = 2
    assert set(value.iter_changed_fields()) == {'extra', 'score'}
    assert value != other
    value.extra = 0
    value.score = 0
    assert value == other and not any(value.iter_changed_fields())


@pytest.mark.parametrize(('sid', 'side'), [('', 0), ('Pack/Map', 3)])
def test_session_constructor_validates_identity(sid: str, side: int) -> None:
    with pytest.raises(ValidationError):
        Session.model_validate(
            {
                'sid': sid,
                'side': side,
                'room': 'room',
                'time_played': Duration(),
                'deaths': 0,
                'room_deaths': 0,
            }
        )


@pytest.mark.parametrize('level_id', (0, 1))
def test_session_can_derive_readonly_sid_from_validated_constructor_id(level_id: int) -> None:
    session = Session(
        level_id=level_id,
        side=LevelSide.A,
        room='room',
        time_played=Duration(),
        deaths=0,
        room_deaths=0,
    )
    assert session.sid == VANILLA_SIDS[level_id]
    assert not any(session.iter_changed_fields())
    with pytest.raises(AttributeError):
        session.sid = 'Pack/Map'  # pyright: ignore[reportAttributeAccessIssue]


def test_session_constructor_rejects_missing_identity_before_using_numeric_defaults() -> None:
    with pytest.raises(ValueError, match='Area/ID or session Area/SID'):
        Session(side=LevelSide.A, room='', time_played=Duration(), deaths=0, room_deaths=0)


def test_missing_numeric_id_is_defaulted_after_sid_and_manual_edits_remain_explicit() -> None:
    root = etree.fromstring(_SESSION)
    area = root.find('Area')
    assert area is not None
    del area.attrib['ID']
    value = Session.from_xml(root)
    assert value.sid == 'Pack/Map' and value.level_id == 0 and not any(value.iter_changed_fields())
    assert value.model_dump(include={'sid', 'level_id'}) == {'sid': 'Pack/Map', 'level_id': 0}
    node = value.to_xml(exclude_unset=True).find('Area')
    assert node is not None and node.get('SID') == 'Pack/Map' and 'ID' not in node.attrib
    value.level_id = 43
    assert tuple(value.iter_changed_fields()) == ('level_id',)
    node = value.to_xml(exclude_unset=True).find('Area')
    assert node is not None and node.get('ID') == '43'


def test_respawn_shape_changes_are_reported_before_output_validation() -> None:
    value = Session.from_xml(etree.fromstring(_SESSION))
    value.respawn = (1.0,)  # pyright: ignore[reportAttributeAccessIssue]
    assert tuple(value.iter_changed_fields()) == ('respawn',)
    with pytest.raises(ValueError):
        value.to_xml()


@pytest.mark.parametrize('tag', ('Session', 'CurrentSession', 'CurrentSession_Safe'))
@pytest.mark.parametrize('strict', (False, True))
def test_numeric_session_identity_is_only_inferred_in_original_branch(
    tag: str, strict: bool
) -> None:
    root = Session.from_xml(etree.fromstring(_SESSION)).to_xml()
    root.tag = tag
    area = root.find('Area')
    old = root.find('OldStats')
    assert area is not None and old is not None
    del area.attrib['SID']
    area.set('ID', '1')
    old.set('SID', VANILLA_SIDS[1])
    if tag == 'CurrentSession':
        value = Session.from_xml(root, strict=strict)
        assert value.sid == VANILLA_SIDS[1] and not any(value.iter_changed_fields())
    else:
        with pytest.raises(ValueError, match='Area/SID'):
            Session.from_xml(root, strict=strict)


@pytest.mark.parametrize('level_id', (None, '-1', '11', 'not-an-int', '2147483648'))
def test_original_session_never_defaults_missing_or_invalid_identity(level_id: str | None) -> None:
    root = etree.fromstring(_SESSION)
    root.tag = 'CurrentSession'
    area = root.find('Area')
    assert area is not None
    del area.attrib['SID']
    if level_id is None:
        del area.attrib['ID']
    else:
        area.set('ID', level_id)
    with pytest.raises(ValueError):
        Session.from_xml(root)


def test_old_stats_identity_is_checked_at_construction_and_after_replacement() -> None:
    with pytest.raises(ValidationError, match='OldStats SID'):
        Session(
            sid='Pack/Map',
            side=LevelSide.A,
            room='room',
            time_played=Duration(),
            deaths=0,
            room_deaths=0,
            old_stats=LevelProgress('Other/Map'),
        )
    session = Session.from_xml(tree.parse(_SESSION).getroot())
    session.old_stats = LevelProgress('Other/Map')
    with pytest.raises(ValueError, match='OldStats SID'):
        session.to_xml()


def test_area_binding_defaults_missing_mode_without_mutating_input() -> None:
    root = tree.parse(_SESSION).getroot()
    area = root.find('Area')
    assert area is not None
    del area.attrib['Mode']
    before = etree.tostring(root)
    session = Session.from_xml(root)
    assert session.sid == 'Pack/Map' and session.side is LevelSide.A and session.level_id == 42
    assert etree.tostring(root) == before
    encoded = session.to_xml().find('Area')
    assert encoded is not None and encoded.attrib == {
        'SID': 'Pack/Map',
        'Mode': 'Normal',
        'ID': '42',
    }


def test_session_domain_accepts_an_embedded_element_name() -> None:
    root = tree.parse(_SESSION).getroot()
    expected = Session.from_xml(root)
    root.tag = 'EmbeddedSession'
    assert Session.from_xml(root) == expected


def test_every_session_field_roundtrips_without_retaining_input_xml() -> None:
    value = Session.from_xml(tree.parse(_SESSION).getroot())
    assert (value.sid, value.side, value.level_id) == ('Pack/Map', LevelSide.B, 42)
    assert value.started_from_beginning and value.dashes == 8 and value.dashes_at_room_start == 3
    assert value.start_checkpoint == 'checkpoint' and value.cassette and value.heart
    assert value.dreaming and value.color_grade == 'cold' and value.core_mode == 'Cold'
    assert value.grabbed_golden and value.hit_checkpoint and not value.first_room
    assert value.audio.music.event == 'event:/music/test'
    assert value.audio.music.parameters[0].key == 'progress'
    assert value.audio.ambience.parameters[0].key is None
    assert value.audio.ambience_volume == 0.25
    assert value.inventory == PlayerInventory(
        dashes=2, dream_dash=True, backpack=True, no_refills=True
    )
    assert value.old_stats is not None and value.old_stats.id == 42
    assert value.flags == {'flag'} and value.room_flags == {'visited'}
    assert value.strawberries == {MapEntityID('room', 1)}
    assert value.do_not_load == {MapEntityID('room', 2)} and value.keys == {MapEntityID('room', 3)}
    assert [(counter.key, counter.value) for counter in value.counters] == [
        ('count', -2),
        ('count', 3),
    ]
    assert value.unlocked_c_side and value.furthest_seen_room == 'goal' and value.beat_best_time
    assert value.restarted_from_golden and 'slider' in value.sliders
    assert value.respawn is not None
    assert struct.pack('<f', value.respawn[0]) == bytes.fromhex('00000080')
    assert not any(value.iter_changed_fields())
    restored = Session.from_xml(tree.parse(value.tostring()).getroot(), strict=True)
    assert restored.tostring() == value.tostring()
    assert not any(restored.iter_changed_fields())


def test_nested_session_edits_are_detected_from_structured_values() -> None:
    value = Session.from_xml(tree.parse(_SESSION).getroot())
    assert value.old_stats is not None
    history = value.old_stats.maps[LevelSide.B]
    assert history is not None
    history.deaths = 10
    value.audio.music.parameters[0].value = 0.5
    value.inventory.dashes = 1
    value.sliders['slider'] = 2.0
    assert set(value.iter_changed_fields()) == {'old_stats', 'audio', 'inventory', 'sliders'}
    restored = Session.from_xml(tree.parse(value.tostring()).getroot())
    assert restored.old_stats is not None
    restored_history = restored.old_stats.maps[LevelSide.B]
    assert restored_history is not None and restored_history.deaths == 10
    assert restored.audio.music.parameters[0].value == 0.5
    history.deaths = 3
    value.audio.music.parameters[0].value = (
        Session.from_xml(tree.parse(_SESSION).getroot()).audio.music.parameters[0].value
    )
    value.inventory.dashes = 2
    value.sliders['slider'] = Session.from_xml(tree.parse(_SESSION).getroot()).sliders['slider']
    assert not any(value.iter_changed_fields())


def test_numeric_level_id_edits_are_detected_and_can_be_reverted() -> None:
    value = Session.from_xml(tree.parse(_SESSION).getroot())
    value.level_id = 43
    assert tuple(value.iter_changed_fields()) == ('level_id',)
    assert Session.from_xml(tree.parse(value.tostring()).getroot()).level_id == 43
    value.level_id = 42
    assert not any(value.iter_changed_fields())


@pytest.mark.parametrize('text', ('NaN', 'INF', '-INF'))
@pytest.mark.parametrize(
    ('path', 'attribute', 'field'),
    (
        ('.', 'DarkRoomAlpha', 'dark_room_alpha'),
        ('.', 'LightingAlphaAdd', 'lighting_alpha_add'),
        ('.', 'BloomBaseAdd', 'bloom_base_add'),
        ('RespawnPoint/X', None, 'respawn'),
        ('RespawnPoint/Y', None, 'respawn'),
        ('Audio/Music/Parameters/MEP', 'Value', 'audio'),
        ('Audio/Ambience/Parameters/MEP', 'Value', 'audio'),
        ('Audio/AmbienceVolume', None, 'audio'),
    ),
)
def test_nonfinite_game_fields_require_repair_before_writing(
    text: str, path: str, attribute: str | None, field: str
) -> None:
    expected = Session.from_xml(etree.fromstring(_SESSION))
    root = expected.to_xml()
    node = root.find(path)
    assert node is not None
    if attribute is None:
        node.text = text
    else:
        node.set(attribute, text)
    with pytest.raises(ValueError, match='finite'):
        Session.from_xml(root, strict=True)
    value = Session.from_xml(root, strict=False)
    with pytest.raises(ValueError, match='finite'):
        value.to_xml()
    setattr(value, field, getattr(expected, field))
    assert Session.from_xml(value.to_xml(), strict=True) == expected


@pytest.mark.parametrize('text', ('NaN', 'INF', '-INF'))
def test_sliders_keep_the_open_single_value_contract(text: str) -> None:
    root = Session.from_xml(etree.fromstring(_SESSION)).to_xml()
    node = root.find('Sliders/Slider')
    assert node is not None
    node.set('Value', text)
    value = Session.from_xml(root, strict=True)
    node = value.to_xml().find('Sliders/Slider')
    assert node is not None and node.get('Value') == text


def test_session_comparison_does_not_override_nan_equality() -> None:
    old = Session.from_xml(etree.fromstring(_SESSION))
    new = Session.from_xml(etree.fromstring(_SESSION))
    old.dark_room_alpha = float('nan')
    new.dark_room_alpha = float('nan')
    assert old != new
    assert tuple(old.iter_changed_fields()) == ('dark_room_alpha',)


def test_session_output_does_not_reparse_input(monkeypatch: pytest.MonkeyPatch) -> None:
    value = Session.from_xml(tree.parse(_SESSION).getroot())

    def unexpected_parse(data: str | bytes) -> None:
        raise AssertionError('Modeled session output must not reparse XML.')

    with monkeypatch.context() as patch:
        patch.setattr(tree, 'parse', unexpected_parse)
        value.tostring()
        assert not any(value.iter_changed_fields())


def test_unknown_audio_parameter_has_full_context_and_warn_discards_it() -> None:
    xml = _SESSION.replace('Key="progress"', 'Key="progress" Future="1"')
    with pytest.raises(UnknownSaveFieldError) as caught:
        Session.from_xml(tree.parse(xml).getroot(), path='Main.CurrentSession_Safe')
    expected = 'Main.CurrentSession_Safe.Audio.Music.Parameters[0].@Future'
    assert caught.value.path == expected
    with pytest.warns(UnknownSaveFieldWarning) as recorded:
        value = Session.from_xml(
            tree.parse(xml).getroot(), unknown='warn', path='Main.CurrentSession_Safe'
        )
    assert isinstance(recorded[0].message, UnknownSaveFieldWarning)
    assert recorded[0].message.path == expected and 'Future' not in value.tostring()


def test_non_nil_respawn_unknown_field_warns_once() -> None:
    xml = _SESSION.replace('<RespawnPoint>', '<RespawnPoint Future="1">')
    with pytest.warns(UnknownSaveFieldWarning) as recorded:
        value = Session.from_xml(tree.parse(xml).getroot(), unknown='warn')
    assert len(recorded) == 1
    assert value.respawn is not None and 'Future' not in value.tostring()


def test_nonnullable_object_nil_is_ignored_but_collection_nil_keeps_constructor_defaults() -> None:
    xml = _SESSION.replace(
        '<Audio>', '<Audio xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true">'
    )
    value = Session.from_xml(tree.parse(xml).getroot())
    assert value.audio.music.event == 'event:/music/test'
    root = etree.fromstring(_SESSION)
    flags = root.find('Flags')
    gems = root.find('SummitGems')
    assert flags is not None and gems is not None
    flags.set(f'{{{tree.XSI}}}nil', 'true')
    gems.set(f'{{{tree.XSI}}}nil', 'true')
    value = Session.from_xml(root)
    assert value.flags == set() and value.summit_gems == [False] * 6


def test_missing_session_nodes_use_field_constructor_defaults() -> None:
    root = etree.fromstring(_SESSION)
    for name in ('Audio', 'Inventory', 'SummitGems'):
        node = root.find(name)
        assert node is not None
        root.remove(node)
    value = Session.from_xml(root)
    assert value.audio == AudioState()
    assert value.inventory == PlayerInventory()
    assert value.summit_gems == [False] * 6


def test_nil_session_gems_still_report_unknown_fields_before_defaulting() -> None:
    root = etree.fromstring(_SESSION)
    gems = root.find('SummitGems')
    assert gems is not None
    gems.set(f'{{{tree.XSI}}}nil', 'true')
    gems.set('Future', 'value')
    with pytest.raises(UnknownSaveFieldError):
        Session.from_xml(root)
    with pytest.warns(UnknownSaveFieldWarning, match='SummitGems.@Future'):
        session = Session.from_xml(root, unknown='warn')
    assert session.summit_gems == [False] * 6


def test_sliders_follow_everest_last_wins_only_in_lenient_input() -> None:
    value = Session.from_xml(tree.parse(_SESSION).getroot())
    root = etree.fromstring(value.tostring())
    sliders = root.find('Sliders')
    assert sliders is not None
    etree.SubElement(sliders, 'Slider', Name='slider', Value='2')
    assert Session.from_xml(root).sliders == {'slider': 2.0}
    with pytest.raises(ValueError, match='Duplicate Slider'):
        Session.from_xml(root, strict=True)
