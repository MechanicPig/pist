"""Unknown YAML tags survive rebuilt known fields and lazy record materialization."""

from io import BytesIO

import pytest
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from berries.game.duration import Duration
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.enders_blender import EndersBlenderSave


def _member(node: Node, key: str) -> Node:
    assert isinstance(node, MappingNode)
    return next(
        value for name, value in node.value if isinstance(name, ScalarNode) and name.value == key
    )


@pytest.mark.parametrize('materialize', [False, True])
def test_blender_known_containers_keys_and_scalars_preserve_custom_tags(materialize: bool) -> None:
    data = (
        b'mapDict_roomStat_firstClear_death: !group\n'
        b'  !map-key Map: &rooms !rooms {!room-key room: !count 2}\n'
        b'mapDict_roomStat_firstClear_roomOrder: !order\n'
        b'  Map: !list [!room room]\n'
        b'mapDict_roomStat_firstClear_timer: {Map: {room: 0}}\n'
        b'mapDict_roomStatCustomNameDict: !names {Map: !room-names {room: !name Before}}\n'
        b'mapDict_roomStat_colorIndex: !colors {Map: !indices {room: !color 1}}\n'
    )
    output = BytesIO(data)
    save = EndersBlenderSave.read(output)
    if materialize:
        save.first_clear['Map'].room_deaths['room'] = 3
        save.first_clear['Map'].room_order.insert(0, 'new')
        save.first_clear['Map'].room_deaths['new'] = 0
        save.first_clear['Map'].room_times['new'] = Duration()
        save.room_names['Map']['room'] = 'After'
        save.room_colors['Map']['room'] = 2
    output.seek(0)
    save.write(output)
    output.truncate()
    root = yaml.compose(output.getvalue())
    assert root is not None
    deaths = _member(root, 'mapDict_roomStat_firstClear_death')
    rooms = _member(deaths, 'Map')
    room = _member(rooms, 'room')
    assert deaths.tag == '!group' and rooms.tag == '!rooms' and room.tag == '!count'
    assert isinstance(room, ScalarNode) and room.value == ('3' if materialize else '2')
    assert isinstance(deaths, MappingNode) and deaths.value[0][0].tag == '!map-key'
    assert isinstance(rooms, MappingNode) and rooms.value[0][0].tag == '!room-key'
    order = _member(root, 'mapDict_roomStat_firstClear_roomOrder')
    sequence = _member(order, 'Map')
    assert order.tag == '!order' and sequence.tag == '!list'
    assert isinstance(sequence, SequenceNode)
    tagged_room = sequence.value[-1]
    assert tagged_room.tag == '!room'
    if materialize:
        assert sequence.value[0].tag == 'tag:yaml.org,2002:str'
    names = _member(root, 'mapDict_roomStatCustomNameDict')
    assert names.tag == '!names'
    assert _member(names, 'Map').tag == '!room-names'
    assert _member(_member(names, 'Map'), 'room').tag == '!name'
    colors = _member(root, 'mapDict_roomStat_colorIndex')
    assert colors.tag == '!colors' and _member(_member(colors, 'Map'), 'room').tag == '!color'


@pytest.mark.parametrize('edit', [False, True])
def test_collab_known_mapping_sequence_and_scalar_tags_survive_edits(edit: bool) -> None:
    output = BytesIO(
        b'SpeedBerryPBs: !pbs {!sid Map: !ticks 170000}\n'
        b'OpenedMiniHeartDoors: !doors [!door z]\n'
        b'LearnedTech: !tech {Collab: !set [!move WaveDash]}\n'
    )
    save = CollabUtils2Save.read(output)
    if edit:
        save.speed_berry_pbs['Map'] = Duration.from_timer_frames(2)
        save.opened_mini_heart_doors.add('a')
        save.learned_tech['Collab'].add('Hyper')
    output.seek(0)
    save.write(output)
    output.truncate()
    root = yaml.compose(output.getvalue())
    assert root is not None
    pbs = _member(root, 'SpeedBerryPBs')
    assert pbs.tag == '!pbs' and _member(pbs, 'Map').tag == '!ticks'
    assert isinstance(pbs, MappingNode) and pbs.value[0][0].tag == '!sid'
    ticks = _member(pbs, 'Map')
    assert isinstance(ticks, ScalarNode) and ticks.value == ('340000' if edit else '170000')
    doors = _member(root, 'OpenedMiniHeartDoors')
    assert doors.tag == '!doors' and isinstance(doors, SequenceNode)
    assert doors.value[-1].tag == '!door'
    if edit:
        assert doors.value[0].tag == 'tag:yaml.org,2002:str'
    tech = _member(root, 'LearnedTech')
    assert tech.tag == '!tech'
    moves = _member(tech, 'Collab')
    assert moves.tag == '!set' and isinstance(moves, SequenceNode)
    assert moves.value[-1].tag == '!move'


def test_removed_tagged_members_are_not_resurrected() -> None:
    output = BytesIO(b'SpeedBerryPBs: !pbs {Map: !ticks 170000}\n')
    save = CollabUtils2Save.read(output)
    save.speed_berry_pbs.clear()
    output.seek(0)
    save.write(output)
    output.truncate()
    root = yaml.compose(output.getvalue())
    assert root is not None
    pbs = _member(root, 'SpeedBerryPBs')
    assert pbs.tag == '!pbs' and isinstance(pbs, MappingNode) and not pbs.value


def test_collab_session_xml_scalar_tag_survives_materialization_and_editing() -> None:
    xml = (
        '<Session InArea="true" Level="room" Time="0" Deaths="2" DeathsInCurrentLevel="0">'
        '<Area SID="Pack/Map" Mode="Normal"/><OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    )
    output = BytesIO(f"SessionsPerLevel: !sessions {{Pack/Map: !xml '{xml}'}}\n".encode())
    save = CollabUtils2Save.read(output)
    save.sessions['Pack/Map'].session.deaths = 3
    output.seek(0)
    save.write(output)
    output.truncate()
    root = yaml.compose(output.getvalue())
    assert root is not None
    sessions = _member(root, 'SessionsPerLevel')
    assert sessions.tag == '!sessions'
    value = _member(sessions, 'Pack/Map')
    assert value.tag == '!xml' and isinstance(value, ScalarNode) and 'Deaths="3"' in value.value
