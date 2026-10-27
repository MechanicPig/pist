"""Collab's editable domain values, lazy sessions and opaque extension boundaries."""

from io import BytesIO

import pytest
import yaml
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.session_input import SessionInput
from test_support.save_docs import parsed_session, read_save, save_bytes


def test_collab_all_declared_values_round_trip() -> None:
    doc = read_save(
        CollabUtils2Save,
        'SpeedBerryPBs: &pbs {"01": 170000}\n'
        'OpenedMiniHeartDoors: ["01"]\n'
        'CombinedRainbowBerries: [Lobby]\n'
        'CompletedWarpPedestalSIDs: [Map]\n'
        'LearnedTech: {"01": [WaveDash]}\n'
        'SpeedberryOptionMessageShown: true\n'
        'VisitedLobbyPositions: {Lobby: "AAE="}\n'
        'RevealMap: true\nPauseVisitingPoints: false\nShowVisitedPoints: true\n',
    )
    assert doc.speed_berry_pbs == {'01': Duration.from_timer_frames(1)}
    assert doc.opened_mini_heart_doors == {'01'}
    assert doc.combined_rainbow_berries == {'Lobby'}
    assert doc.completed_warp_pedestals == {'Map'}
    assert doc.learned_tech == {'01': {'WaveDash'}}
    assert doc.speedberry_option_message_shown
    assert doc.visited_lobby_positions == {'Lobby': 'AAE='}
    assert doc.reveal_map and not doc.pause_visiting_points and doc.show_visited_points
    doc.speedberry_option_message_shown = False
    doc.visited_lobby_positions['Lobby'] = 'AgM='
    doc.reveal_map = False
    doc.pause_visiting_points = True
    doc.show_visited_points = False
    doc.speed_berry_pbs['01'] = Duration.from_timer_frames(2)
    doc.opened_mini_heart_doors.clear()
    doc.combined_rainbow_berries.add('Next')
    doc.completed_warp_pedestals.clear()
    doc.learned_tech['01'].add('Hyper')
    encoded = save_bytes(doc)
    restored = read_save(CollabUtils2Save, encoded)
    assert restored.speed_berry_pbs == doc.speed_berry_pbs
    assert restored.opened_mini_heart_doors == set()
    assert restored.combined_rainbow_berries == {'Lobby', 'Next'}
    assert restored.completed_warp_pedestals == set()
    assert restored.learned_tech == {'01': {'WaveDash', 'Hyper'}}
    assert not restored.speedberry_option_message_shown
    assert restored.visited_lobby_positions == {'Lobby': 'AgM='}
    assert not restored.reveal_map and restored.pause_visiting_points
    assert not restored.show_visited_points


def test_collab_query_is_lazy_and_loaded_output_uses_canonical_session_values() -> None:
    xml = (
        '<Session InArea="true" Level="r" Time="000170000" Deaths="02" '
        'DeathsInCurrentLevel="0"><Area SID="Map" Mode="Normal"/>'
        '<RespawnPoint><X>8.000</X><Y>16.000</Y></RespawnPoint>'
        '<Flags><string>keep</string></Flags><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    doc = read_save(
        CollabUtils2Save,
        yaml.safe_dump({'SessionsPerLevel': {'Map': xml, 'Bad': '<bad'}}),
    )
    assert len(doc.sessions) == 2
    assert 'Map' in doc.sessions
    session = doc.sessions['Map'].session
    assert session.side is LevelSide.A
    with pytest.raises(ValueError):
        save_bytes(doc)
    del doc.sessions['Bad']
    assert yaml.safe_load(save_bytes(doc))['SessionsPerLevel']['Map'] == session.tostring()
    session.deaths = 3
    encoded = yaml.safe_load(save_bytes(doc))['SessionsPerLevel']
    root = etree.fromstring(encoded['Map'])
    assert root.get('Deaths') == '3' and root.get('Time') == '170000'
    assert root.findtext('RespawnPoint/X') == '8'
    assert root.findtext('Flags/string') == 'keep'


def test_collab_keyed_dict_does_not_parse_xml_to_load_iterate_or_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = yaml.safe_dump(
        {
            'SessionsPerLevel': {
                'Map': '<Session InArea="true" Level="r" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Map" Mode="BSide"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>',
                'Bad': '<broken',
            }
        },
        sort_keys=False,
    )

    def fail(data: str | bytes) -> etree.ElementTree:
        raise AssertionError('Building or copying the SID index must not parse session XML.')

    with monkeypatch.context() as patch:
        patch.setattr(tree, 'parse', fail)
        doc = read_save(CollabUtils2Save, data)
        assert list(doc.sessions) == ['Map', 'Bad']
        assert [value.sid for value in doc.sessions.values()] == ['Map', 'Bad']
        copied = doc.sessions.copy()
        assert copied['Map'] is doc.sessions['Map']
        assert list(copied.items()) == list(doc.sessions.items())
        assert doc.sessions.get('Bad') is copied['Bad']
    assert doc.sessions['Map'].session.side is LevelSide.B
    with pytest.raises(ValueError, match='Bad'):
        doc.sessions['Bad'].session  # noqa: B018


@pytest.mark.parametrize(
    'data',
    (
        'SpeedBerryPBs: {Map: invalid}',
        'OpenedMiniHeartDoors: {}',
        'CombinedRainbowBerries: [[bad]]',
        'CompletedWarpPedestalSIDs: {bad: data}',
        'LearnedTech: {Collab: {bad: data}}',
    ),
)
def test_collab_known_shapes_are_validated_without_loading_sessions(data: str) -> None:
    with pytest.raises(ValueError):
        read_save(CollabUtils2Save, data)


def test_collab_failed_output_validation_never_updates_the_file() -> None:
    original = b'SpeedBerryPBs: {Map: 170000}\n'
    with BytesIO(original) as stream:
        doc = CollabUtils2Save.read(stream)
        doc.speed_berry_pbs['Map'] = Duration.from_seconds(1 / 3)
        with pytest.raises(ValueError):
            doc.write(stream)
        assert stream.getvalue() == original


@pytest.mark.parametrize(
    ('xml', 'message'),
    [
        ('<Session malformed', 'Invalid XML'),
        (
            (
                '<Session Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
                '<Area/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/>'
                '<AreaModeStats/></Modes></OldStats></Session>'
            ),
            'Area/SID',
        ),
        (
            (
                '<Session Level="r" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
                '<Area SID="Other"/><OldStats SID="Other"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
            ),
            'key/SID mismatch',
        ),
    ],
)
def test_collab_deferred_failure_keeps_input_and_reports_expected_sid(
    xml: str,
    message: str,
) -> None:
    doc = read_save(CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Map': xml}}))
    pending = doc.sessions['Map']
    for _ in range(2):
        with pytest.raises(ValueError, match=f"(?s)session 'Map'.*{message}"):
            pending.session  # noqa: B018
        assert pending.orig_xml == xml
        with pytest.raises(ValueError):
            save_bytes(doc)


@pytest.mark.parametrize('read_first', [False, True])
def test_collab_session_reference_rejects_assignment(read_first: bool) -> None:
    xml = parsed_session('Map', LevelSide.A, room='original').tostring()
    doc = read_save(CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Map': xml}}))
    pending = doc.sessions['Map']
    if read_first:
        assert pending.session.room == 'original'
    with pytest.raises(AttributeError):
        pending.session = parsed_session('Map', LevelSide.B)  # pyright: ignore[reportAttributeAccessIssue]
    assert yaml.safe_load(save_bytes(doc))['SessionsPerLevel']['Map'] == xml
    assert pending.session.room == 'original' and pending.session.side is LevelSide.A


def test_collab_session_deletion_discards_edits_and_reparses_original() -> None:
    xml = parsed_session('Map', LevelSide.A, room='original').tostring()
    doc = read_save(CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Map': xml}}))
    pending = doc.sessions['Map']
    with pytest.raises(AttributeError):
        del pending.session
    session = pending.session
    session.room = 'edited'
    assert pending.session is session
    assert yaml.safe_load(save_bytes(doc))['SessionsPerLevel']['Map'] != xml
    del pending.session
    assert yaml.safe_load(save_bytes(doc))['SessionsPerLevel']['Map'] == xml
    with pytest.raises(AttributeError):
        del pending.session
    restored = pending.session
    assert restored is not session and restored.room == 'original'
    assert session.room == 'edited' and pending.orig_xml == xml


def test_collab_new_unevaluated_value_does_not_reuse_old_input() -> None:
    doc = read_save(CollabUtils2Save, 'SessionsPerLevel: {Map: "<broken"}')
    doc.sessions.add(SessionInput('Map', parsed_session('Map', LevelSide.C, deaths=3).tostring()))
    restored = read_save(CollabUtils2Save, save_bytes(doc))
    assert restored.sessions['Map'].session.side is LevelSide.C
    assert restored.sessions['Map'].session.deaths == 3


def test_failed_session_evaluation_leaves_no_cache_to_delete() -> None:
    pending = SessionInput('Map', '<broken')
    with pytest.raises(ValueError):
        pending.session  # noqa: B018
    with pytest.raises(AttributeError):
        del pending.session
    with pytest.raises(ValueError):
        pending.tostring()


def test_session_input_keeps_its_index_and_raw_text_stable() -> None:
    pending = SessionInput('Map', '<broken')
    assert pending.sid == 'Map' and pending.orig_xml == '<broken'
    for name in ('sid', 'orig_xml'):
        with pytest.raises(AttributeError):
            setattr(pending, name, 'Other')
    with pytest.raises(ValueError):
        pending.tostring()
    with pytest.raises(ValueError, match='SID'):
        SessionInput('', '<broken')
