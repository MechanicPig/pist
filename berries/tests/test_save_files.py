"""Real codec round trips on synthetic saves; no installed game's data is modified."""

import struct
from collections.abc import Callable
from copy import copy, deepcopy
from dataclasses import replace
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import yaml
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.xml import fields as xml_fields
from berries.game.saves.codecs.xml import tree
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.save import Save
from berries.game.saves.rooms import RoomStats
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from berries.map_entity_id import MapEntityID
from test_support.save_docs import (
    complete_vanilla,
    get_map,
    parsed_session,
    read_save,
    reopen_save,
    require_map,
    save_bytes,
)

ORDER = 'mapDict_roomStat_firstClear_roomOrder'
DEATHS = 'mapDict_roomStat_firstClear_death'
TIMER = 'mapDict_roomStat_firstClear_timer'

SESSION = """<Session InArea="true" Level="start" Time="-1" Deaths="3" DeathsInCurrentLevel="1" Dashes="9">
<Area SID="Map" Mode="Normal"/><RespawnPoint><X>8</X><Y>16</Y></RespawnPoint>
<Flags><string>keep</string></Flags><RestartedFromGolden>true</RestartedFromGolden>
<OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats>
</Session>"""
AREA = """<AreaStats SID="Celeste/0-Intro" Cassette="false"><Modes>
<AreaModeStats TimePlayed="17" Deaths="2" Completed="false" SingleRunCompleted="false" HeartGem="false" BestTime="999">
<Strawberries><EntityID Key="start:1"/></Strawberries><Checkpoints><string>keep</string></Checkpoints>
</AreaModeStats><AreaModeStats TimePlayed="0" Deaths="0"/><AreaModeStats TimePlayed="0" Deaths="0"/></Modes></AreaStats>"""


def _main(*, session: str = SESSION) -> MainSave:
    safe = (
        session.replace('<Session ', '<CurrentSession_Safe ')
        .replace('</Session>', '</CurrentSession_Safe>')
        .replace('SID="Map"', 'SID="Celeste/0-Intro"')
    )
    return read_save(
        MainSave,
        complete_vanilla(f"""<?xml version="1.0"?>
<SaveData xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:ext="urn:extra">
<!--retain comment--><?pist retain?><Time>10</Time><TotalDeaths>20</TotalDeaths>
<HasModdedSaveData>true</HasModdedSaveData>
<Areas>{AREA}</Areas><LevelSets/><LevelSetRecycleBin/>{safe}
<Flags><string>keep</string></Flags>
</SaveData>"""),
    )


def test_map_edit_preserves_known_values_precision_and_independent_domain_copy() -> None:
    main = _main()
    original = save_bytes(main)
    progress = require_map(main.flat_levels['Celeste/0-Intro'].maps[LevelSide.A])
    assert progress.time_played.to_dotnet_ticks() == 17
    assert get_map(main, 'celeste/Map', LevelSide.A) is None
    assert (
        require_map(main.flat_levels['Celeste/0-Intro'].maps[LevelSide.B]).time_played == Duration()
    )
    assert sum(len(level.maps) for level in main.flat_levels.values()) == 33
    edited = reopen_save(main)
    edited.install_campaigns((edited, deepcopy(main.vanilla.snapshot())))
    changed = require_map(edited.flat_levels['Celeste/0-Intro'].maps[LevelSide.A])
    changed.time_played = Duration.from_dotnet_ticks(-7)
    changed.deaths = 8
    changed.completed = True
    assert save_bytes(main) == original
    encoded = save_bytes(edited)
    loaded = read_save(MainSave, encoded)
    restored = require_map(loaded.flat_levels['Celeste/0-Intro'].maps[LevelSide.A])
    assert restored.deaths == 8 and restored.completed
    assert restored.time_played == Duration.from_dotnet_ticks(-7)
    assert save_bytes(loaded) == encoded and encoded != original
    assert loaded.flags == {'keep'}
    assert b'<!--retain comment-->' not in encoded and b'<?pist retain?>' not in encoded
    root = ET.fromstring(encoded)
    mode = root.find('Areas/AreaStats/Modes/AreaModeStats')
    assert mode is not None and mode.get('BestTime') == '999'
    entity = mode.find('Strawberries/EntityID')
    assert entity is not None and entity.get('Key') == 'start:1'
    assert mode.findtext('Checkpoints/string') == 'keep'
    assert loaded.time_played == main.time_played and loaded.deaths == main.deaths


@pytest.mark.parametrize(
    ('attribute', 'expected'),
    ((None, False), ('false', False), ('true', True), ('0', False), ('1', True)),
)
def test_cassette_only_defaults_when_attribute_is_missing(
    attribute: str | None, expected: bool
) -> None:
    cassette = '' if attribute is None else f' Cassette="{attribute}"'
    area = AREA.replace(' Cassette="false"', cassette)
    main = read_save(MainSave, complete_vanilla(f'<SaveData><Areas>{area}</Areas></SaveData>'))
    assert main.flat_levels['Celeste/0-Intro'].cassette is expected


@pytest.mark.parametrize('attribute', ('', 'invalid'))
def test_cassette_rejects_explicit_invalid_boolean(attribute: str) -> None:
    area = AREA.replace('Cassette="false"', f'Cassette="{attribute}"')
    with pytest.raises(ValueError):
        read_save(MainSave, complete_vanilla(f'<SaveData><Areas>{area}</Areas></SaveData>'))


@pytest.mark.parametrize('mod_backup', (False, True))
@pytest.mark.parametrize(
    'key',
    (
        MapEntityID('', 1),
        MapEntityID('room', -1),
        MapEntityID('room', True),
        *(
            MapEntityID(f'bad{char}room', 2)
            for char in ('\x00', '\x01', '\ud800', '\ufffe', '\uffff')
        ),
    ),
)
def test_invalid_collected_ids_prevent_file_commit(key: MapEntityID, mod_backup: bool) -> None:
    area = AREA.replace('Celeste/0-Intro', 'Pack/Map')
    data = (
        (
            f'<ModSaveData><LevelSets><LevelSetStats Name="Pack"><Areas>{area}</Areas>'
            '</LevelSetStats></LevelSets></ModSaveData>'
        ).encode()
        if mod_backup
        else save_bytes(_main())
    )
    stream = BytesIO(data)
    save = (
        ModBackupSave.read(stream, strict=False)
        if mod_backup
        else MainSave.read(stream, strict=False)
    )
    campaign = save.vanilla if isinstance(save, MainSave) else save.mods.campaigns['Pack']
    progress = require_map(next(iter(campaign.levels.values())).maps[LevelSide.A])
    progress.deaths = 8
    progress.strawberries = {key}
    with pytest.raises(ValueError):
        save.write(stream)
    assert stream.getvalue() == data


def test_collected_ids_replace_members_and_preserve_xml_whitespace_and_unicode() -> None:
    main = _main()
    key = MapEntityID('房间\t\n\r<&"\U0001f353', 2)
    require_map(main.flat_levels['Celeste/0-Intro'].maps[LevelSide.A]).strawberries = {key}
    restored = require_map(
        read_save(MainSave, save_bytes(main)).flat_levels['Celeste/0-Intro'].maps[LevelSide.A]
    )
    assert restored.strawberries == {key}
    with pytest.raises(KeyError):
        require_map(main.flat_levels['Celeste/Missing'].maps[LevelSide.A])


@pytest.mark.parametrize('bad', (Duration.from_seconds(1, 3), Duration.from_dotnet_ticks(2**63)))
@pytest.mark.parametrize('session', (False, True))
def test_unrepresentable_time_does_not_partially_write(bad: Duration, session: bool) -> None:
    data = save_bytes(_main())
    stream = BytesIO(data)
    main = MainSave.read(stream, strict=False)
    if session:
        current = main.session
        assert current is not None
        current.time_played = bad
        current.deaths = 9
    else:
        progress = require_map(main.flat_levels['Celeste/0-Intro'].maps[LevelSide.A])
        progress.time_played = bad
        progress.deaths = 9
    with pytest.raises(ValueError):
        main.write(stream)
    assert stream.getvalue() == data


@pytest.mark.parametrize('value', (float('nan'), float('inf'), -float('inf')))
def test_nonfinite_session_output_does_not_write_the_destination(value: float) -> None:
    save = _main()
    assert save.session is not None
    save.session.dark_room_alpha = value
    stream = BytesIO(b'original destination')
    with pytest.raises(ValueError, match='finite'):
        save.write(stream)
    assert stream.getvalue() == b'original destination'


def test_totals_compare_numeric_values_without_rewriting_unchanged_text() -> None:
    main = read_save(
        MainSave,
        complete_vanilla('<SaveData><Time>-01</Time><TotalDeaths>0002</TotalDeaths></SaveData>'),
    )
    original = save_bytes(main)
    main.time_played = Duration.from_dotnet_ticks(-1)
    main.deaths = 2
    assert save_bytes(main) == original
    main.time_played = Duration()
    main.deaths = 3
    assert read_save(MainSave, save_bytes(main)).deaths == 3


@pytest.mark.parametrize('bad', (-1, True, 2**31))
def test_invalid_global_counts_prevent_commit_without_rolling_back_memory(bad: int) -> None:
    data = save_bytes(_main())
    stream = BytesIO(data)
    main = MainSave.read(stream, strict=False)
    main.time_played = Duration()
    main.deaths = bad
    with pytest.raises(ValueError):
        main.write(stream)
    assert main.deaths == bad and main.time_played == Duration()
    assert stream.getvalue() == data


def test_missing_totals_use_game_defaults_and_can_be_edited() -> None:
    main = read_save(MainSave, complete_vanilla('<SaveData><Time>5</Time></SaveData>'))
    assert main.deaths == 0 and main.name == 'Madeline'
    main.deaths = 1
    main.time_played = Duration.from_dotnet_ticks(-5)
    restored = read_save(MainSave, save_bytes(main))
    assert restored.deaths == 1 and restored.time_played == Duration.from_dotnet_ticks(-5)


def test_branch_copies_preserve_missing_side_positions() -> None:
    main = read_save(
        MainSave,
        complete_vanilla(
            '<SaveData><Areas><AreaStats SID="Celeste/0-Intro"><Modes>'
            '<AreaModeStats TimePlayed="01" Deaths="02"/>'
            '</Modes></AreaStats></Areas></SaveData>'
        ),
    )
    original = main.vanilla.snapshot()
    branch = copy(original)
    branch.levels = original.levels.copy()
    level = copy(next(iter(original.levels.values())))
    level.maps = level.maps.copy()
    assert len(level.maps) == 1
    branch.levels.replace(level)
    main.install_campaigns((main, branch))
    level.maps[LevelSide.A] = copy(require_map(level.maps[LevelSide.A]))
    assert (
        next(iter(branch.levels.values())).maps[LevelSide.A]
        is not next(iter(original.levels.values())).maps[LevelSide.A]
    )
    assert len(next(iter(original.levels.values())).maps) == 1
    assert len(next(iter(branch.levels.values())).maps) == 1


def test_branch_copies_preserve_canonical_output_and_known_values() -> None:
    main = _main(session=SESSION.replace('Time="-1"', 'Time="-01"'))
    baseline = save_bytes(main)
    original = main.vanilla.snapshot()
    branch = copy(original)
    branch.levels = original.levels.copy()
    for level in original.levels.values():
        branch.levels.replace(copy(level))
    main.install_campaigns((main, branch))
    assert save_bytes(main) == baseline


def test_sessions_are_live_and_copies_can_restore_original_content() -> None:
    main = _main()
    session = main.session
    assert session is not None and session.respawn == (8, 16)
    assert session.time_played.to_dotnet_ticks() == -1
    original = save_bytes(main)
    unchanged = deepcopy(session)
    session.room, session.respawn, session.deaths = 'goal', None, 5
    restored = read_save(MainSave, save_bytes(main))
    assert restored.session is not None
    assert (restored.session.room, restored.session.respawn, restored.session.deaths) == (
        'goal',
        None,
        5,
    )
    root = ET.fromstring(save_bytes(main))
    assert root.findtext('CurrentSession_Safe/Flags/string') == 'keep'
    node = root.find('CurrentSession_Safe')
    assert node is not None and node.get('Dashes') == '9'
    main.session = unchanged
    assert save_bytes(main) == original


def test_session_edit_preserves_unchanged_text_and_can_restore_original_content() -> None:
    main = _main(
        session=SESSION.replace('Time="-1"', 'Time="-01"')
        .replace('DeathsInCurrentLevel="1"', 'DeathsInCurrentLevel="01"')
        .replace('<X>8</X><Y>16</Y>', '<X>8.000</X><Y>1.6E1</Y>')
    )
    session = main.session
    assert session is not None
    original = deepcopy(session)
    baseline = save_bytes(main)
    session.deaths = 4
    assert save_bytes(main) == baseline.replace(b'Deaths="3"', b'Deaths="4"')
    main.session = original
    assert save_bytes(main) == baseline


@pytest.mark.parametrize('codepoint', (0, 0x0B, 0xD800, 0xDFFF, 0xFFFE, 0xFFFF))
def test_invalid_xml_room_prevents_commit(codepoint: int) -> None:
    data = save_bytes(_main())
    stream = BytesIO(data)
    main = MainSave.read(stream, strict=False)
    session = main.session
    assert session is not None
    session.room, session.deaths = chr(codepoint), 4
    with pytest.raises(ValueError):
        main.write(stream)
    assert stream.getvalue() == data


@pytest.mark.parametrize(
    'room', ('', '\t\n\r', '房间😀', '\ud7ff\ue000\ufffd', '\U00010000\U0010ffff')
)
def test_session_xml_characters_round_trip(room: str) -> None:
    main = _main()
    session = main.session
    assert session is not None
    session.room = room
    restored = read_save(MainSave, save_bytes(main)).session
    assert restored is not None and restored.room == room


@pytest.mark.parametrize('name', ('X', 'Y'))
@pytest.mark.parametrize('nil', [False, True])
def test_duplicate_respawn_children_are_ignored_only_when_nullable_position_is_nil(
    name: str, nil: bool
) -> None:
    marker = ' xsi:nil="true"' if nil else ''
    source = SESSION.replace(
        '<RespawnPoint><X>8</X><Y>16</Y></RespawnPoint>',
        f'<RespawnPoint{marker}><X>1</X><Y>2</Y><{name}>3</{name}></RespawnPoint>',
    )
    if not nil:
        with pytest.raises(ValueError, match='Duplicate'):
            _main(session=source)
        return
    main = _main(session=source)
    data = save_bytes(main)
    stream = BytesIO(data)
    edited = MainSave.read(stream, strict=False)
    session = edited.session
    assert session is not None
    session.deaths, session.respawn = 4, (1, 2)
    restored = read_save(MainSave, save_bytes(edited))
    assert restored.session is not None and restored.session.respawn == (1, 2)
    assert restored.session.deaths == 4 and stream.getvalue() == data


def test_session_coordinate_edit_keeps_other_component_value() -> None:
    main = _main(session=SESSION.replace('<Y>16</Y>', '<Y>1.6E1</Y>'))
    session = main.session
    assert session is not None
    original = deepcopy(session)
    baseline = save_bytes(main)
    session.respawn = (9, 16)
    root = ET.fromstring(save_bytes(main))
    position = root.find('CurrentSession_Safe/RespawnPoint')
    assert position is not None
    assert float(position.findtext('X', 'nan')) == 9
    assert float(position.findtext('Y', 'nan')) == 16
    main.session = original
    assert save_bytes(main) == baseline


@pytest.mark.parametrize(
    'value',
    (9.0, -0.0, 2.5, 1.23456789, 1e-5),
)
def test_session_coordinate_format_preserves_binary32_precision(value: float) -> None:
    main = _main()
    session = main.session
    assert session is not None
    session.respawn = (value, 16)
    root = ET.fromstring(save_bytes(main))
    encoded = root.findtext('CurrentSession_Safe/RespawnPoint/X')
    assert encoded is not None
    assert struct.pack('<f', float(encoded)) == struct.pack('<f', value)


def test_invalid_same_identity_second_session_prevents_file_commit() -> None:
    data = (
        '<SaveData>'
        + SESSION.replace('Session', 'CurrentSession_Safe')
        + SESSION.replace('Session', 'CurrentSession').replace('Deaths="3"', 'Deaths="bad"')
        + '</SaveData>'
    ).encode()
    stream = BytesIO(data)
    with pytest.raises(ValueError):
        MainSave.read(stream)
    assert stream.getvalue() == data


def test_duplicate_snapshot_fields_are_rejected_when_loading() -> None:
    with pytest.raises(ValueError, match='Duplicate'):
        read_save(ModBackupSave, '<ModSaveData><LastArea_Safe/><LastArea_Safe/></ModSaveData>')


def test_reopening_documents_preserves_large_known_collections_and_isolation() -> None:
    main = read_save(
        MainSave,
        complete_vanilla(
            '<SaveData><Time>1</Time><TotalDeaths>2</TotalDeaths>'
            + '<Flags>'
            + ''.join(f'<string>flag{i}</string>' for i in range(2000))
            + '</Flags>'
            + '</SaveData>'
        ),
    )
    original = save_bytes(main)
    copied = reopen_save(main)
    copied.deaths = 3
    assert main.deaths == 2 and save_bytes(main) == original
    assert copied.deaths == 3 and save_bytes(copied) != original
    assert len(copied.flags) == len(main.flags) == 2000
    copied.flags.remove('flag0')
    assert 'flag0' in main.flags


def test_reopen_and_session_edit_keep_values_and_mark_mod_changes() -> None:
    main = read_save(
        MainSave,
        complete_vanilla(
            '<?first a?><!--before--><SaveData><Time>1</Time><TotalDeaths>2</TotalDeaths>'
            + SESSION.replace('Session', 'CurrentSession_Safe')
            + '</SaveData><!--after--><?last b?>'
        ),
    )
    original = save_bytes(main)
    copied = reopen_save(main)
    assert save_bytes(copied) == original
    session = copied.session
    assert session is not None
    unchanged = deepcopy(session)
    session.deaths = 4
    marked = original.replace(b'<HasModdedSaveData>false', b'<HasModdedSaveData>true')
    assert save_bytes(copied) == marked.replace(b'Deaths="3"', b'Deaths="4"')
    another = reopen_save(copied)
    assert save_bytes(another) == save_bytes(copied)
    another.session = unchanged
    assert save_bytes(another) == marked and save_bytes(main) == original


def test_removing_respawn_preserves_other_session_values_not_ignored_text() -> None:
    main = _main(session=SESSION.replace('</RespawnPoint>', '</RespawnPoint>keep following text'))
    session = main.session
    assert session is not None
    session.respawn = None
    node = ET.fromstring(save_bytes(main)).find('CurrentSession_Safe/Area')
    assert node is not None and node.get('SID') == 'Celeste/0-Intro'
    assert b'keep following text' not in save_bytes(main)


@pytest.mark.parametrize('value', (0, 1, 2**31 - 1))
def test_count_parsing_and_formatting_have_explicit_result_types(value: int) -> None:
    assert values_codec.parse_count(value) == values_codec.parse_count(str(value)) == value
    assert type(values_codec.parse_count(str(value))) is int
    assert values_codec.format_count(value) == str(value)
    main = read_save(
        MainSave, complete_vanilla(f'<SaveData><TotalDeaths>{value:012d}</TotalDeaths></SaveData>')
    )
    assert main.deaths == value


@pytest.mark.parametrize('value', (-1, True, 2**31))
def test_count_read_and_write_reject_invalid_values(value: int) -> None:
    with pytest.raises(ValueError):
        values_codec.parse_count(value)
    with pytest.raises(ValueError):
        values_codec.format_count(value)
    with pytest.raises(ValueError):
        read_save(
            MainSave, complete_vanilla(f'<SaveData><TotalDeaths>{value}</TotalDeaths></SaveData>')
        )


@pytest.mark.parametrize('parse_number', (values_codec.parse_count, values_codec.parse_ticks))
@pytest.mark.parametrize('value', ('1.0', '+1', ' 1 ', '', 'true', True, 1.0, None))
def test_save_integer_parsers_reject_non_decimal_inputs(
    parse_number: Callable[[object], int], value: object
) -> None:
    with pytest.raises(ValueError):
        parse_number(value)


@pytest.mark.parametrize('value', (0, 1, -1, 9_999, -9_999, -(2**63), 2**63 - 1))
def test_tick_boundaries_decode_to_duration_and_encode_losslessly(value: int) -> None:
    decoded = Duration.from_dotnet_ticks(value)
    assert values_codec.parse_duration(value) == values_codec.parse_duration(str(value)) == decoded
    assert values_codec.parse_duration(decoded) is decoded
    assert values_codec.format_duration(decoded) == str(value)
    assert values_codec.parse_ticks(value) == values_codec.parse_ticks(str(value)) == value
    main = read_save(MainSave, complete_vanilla(f'<SaveData><Time>{value}</Time></SaveData>'))
    assert main.time_played == decoded


@pytest.mark.parametrize('value', (-(2**63) - 1, 2**63))
def test_ticks_range_is_enforced_at_io_not_on_duration(value: int) -> None:
    duration = Duration.from_dotnet_ticks(value)
    assert values_codec.parse_duration(duration) is duration
    with pytest.raises(ValueError):
        values_codec.parse_duration(str(value))
    with pytest.raises(ValueError):
        values_codec.format_duration(duration)
    with pytest.raises(ValueError):
        values_codec.parse_ticks(value)


def test_internal_field_models_keep_duration_without_protocol_precision_constraints() -> None:
    duration = Duration.from_seconds(1, 3)
    assert values_codec.parse_duration(duration) is duration
    fields = xml_fields.MapFields(
        TimePlayed=duration,
        Deaths=0,
        Completed=False,
        SingleRunCompleted=False,
        FullClear=False,
        HeartGem=False,
    )
    assert fields.time_played == duration
    session = parsed_session('Pack/Map', LevelSide.A)
    session.time_played = duration
    assert session.time_played == duration
    with pytest.raises(ValueError, match='not exactly representable'):
        values_codec.format_duration(duration)


@pytest.mark.parametrize(
    'namespace',
    (
        'xmlns:ext="urn:target"',
        'xmlns:alias="urn:donor"',
        'xmlns:ext="urn:target" xmlns:alias="urn:donor"',
    ),
)
def test_snapshot_warn_policy_drops_opaque_qnames_across_namespace_conflicts(
    namespace: str,
) -> None:
    data = """<ModSaveData xmlns:ext="urn:donor">
<LevelSets><Unknown type="ext:Special"><![CDATA[  keep <raw>  ]]></Unknown>
<Nested xmlns:ext="urn:nested"><Unknown type="ext:Other"/></Nested></LevelSets>
</ModSaveData>"""
    with pytest.raises(UnknownSaveFieldError):
        ModBackupSave.fromstring(data)
    with pytest.warns(UnknownSaveFieldWarning) as warnings:
        donor = ModBackupSave.fromstring(data, unknown='warn')
    assert len(warnings) == 2
    main = read_save(
        MainSave,
        complete_vanilla(f"""<?before keep?><SaveData {namespace}>
<HasModdedSaveData>true</HasModdedSaveData><LevelSets/>after
</SaveData><?after keep?>"""),
    )
    main.install_mod_progress(deepcopy(donor).mods)
    encoded = save_bytes(main)
    root = etree.fromstring(encoded)
    unknown = root.find('LevelSets/Unknown')
    nested = root.find('LevelSets/Nested/Unknown')
    assert unknown is None and nested is None
    assert b'<![CDATA[  keep <raw>  ]]>' not in encoded
    group = root.find('LevelSets')
    assert group is not None and not len(group)
    assert b'berries-import' not in encoded
    baseline = encoded
    main.install_mod_progress(deepcopy(donor).mods)
    assert save_bytes(main) == baseline


def test_session_warn_policy_drops_nested_opaque_qnames_and_cdata() -> None:
    data = complete_vanilla(f"""<SaveData xmlns:alias="urn:nested">
{SESSION.replace('</Session>', '<Unknown xmlns:ext="urn:nested" type="ext:Special"><![CDATA[ keep <raw> ]]></Unknown></Session>').replace('Session', 'CurrentSession_Safe')}
</SaveData>""")
    with pytest.raises(UnknownSaveFieldError):
        MainSave.fromstring(data, strict=False)
    with pytest.warns(UnknownSaveFieldWarning):
        main = MainSave.fromstring(data, strict=False, unknown='warn')
    session = main.session
    assert session is not None
    session.deaths = 4
    encoded = save_bytes(main)
    assert etree.fromstring(encoded).find('CurrentSession_Safe/Unknown') is None
    assert b'<![CDATA[ keep <raw> ]]>' not in encoded
    restored = read_save(MainSave, encoded).session
    assert restored is not None and restored.deaths == 4 and restored.flags == {'keep'}


@pytest.mark.parametrize('encoding', ('utf-8', 'utf-16'))
def test_xml_bytes_honor_encoding_and_unicode_ignores_stale_declaration(encoding: str) -> None:
    source = f'<?xml version="1.0" encoding="{encoding}"?><SaveData><Name>中文</Name></SaveData>'
    main = read_save(MainSave, complete_vanilla(source).encode(encoding))
    assert ET.fromstring(save_bytes(main)).findtext('Name') == '中文'
    assert tree.parse(source).getroot().findtext('Name') == '中文'


@pytest.mark.parametrize(
    ('save_type', 'data'),
    (
        (MainSave, complete_vanilla('<SaveData/>')),
        (ModBackupSave, '<ModSaveData/>'),
        (CollabUtils2Save, 'SessionsPerLevel: {}'),
        (EndersBlenderSave, '{}'),
    ),
)
def test_read_does_not_implicitly_encode(
    monkeypatch: pytest.MonkeyPatch, save_type: type[Save], data: str
) -> None:
    def unexpected_serialize(self: Save) -> str:
        raise AssertionError('Reading must not encode the document.')

    with monkeypatch.context() as patch:
        patch.setattr(save_type, 'tostring', unexpected_serialize)
        document = read_save(save_type, data)
        assert document is not None
    assert save_bytes(document)


def test_collab_edit_uses_canonical_session_content_without_source_declaration() -> None:
    session_xml = (
        '<?xml version="1.0" encoding="utf-16"?><?first a?><!--before-->'
        + SESSION
        + '<!--after--><?last b?>'
    )
    collab = read_save(CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Map': session_xml}}))
    pending = collab.sessions.get('Map')
    assert pending is not None
    session = pending.session
    normalized = session.tostring()
    assert yaml.safe_load(save_bytes(collab))['SessionsPerLevel']['Map'] == normalized
    assert not normalized.startswith('<?xml')
    session.deaths = 4
    result = yaml.safe_load(save_bytes(collab))['SessionsPerLevel']['Map']
    assert '<?first a?>' not in result and '<!--after-->' not in result
    assert result.startswith('<Session ')
    assert read_save(CollabUtils2Save, save_bytes(collab)).sessions['Map'].session.deaths == 4


def test_session_nil_respawn_can_be_populated_without_namespace_loss() -> None:
    main = _main(
        session=SESSION.replace(
            '<RespawnPoint><X>8</X><Y>16</Y></RespawnPoint>', '<RespawnPoint xsi:nil="true"/>'
        )
    )
    session = main.session
    assert session is not None and session.respawn is None
    session.respawn = (3, 4)
    restored = read_save(MainSave, save_bytes(main)).session
    assert restored is not None and restored.respawn == (3, 4)


@pytest.mark.parametrize(
    'data', ('<broken', '<Wrong/>', '<!DOCTYPE SaveData [<!ENTITY x "oops">]><SaveData/>')
)
def test_invalid_xml_is_a_value_error(data: str) -> None:
    with pytest.raises(ValueError):
        read_save(MainSave, data)


def test_xml_parser_accepts_arbitrary_root_but_save_formats_validate_their_own() -> None:
    assert tree.parse('<Other/>').getroot().tag == 'Other'
    with pytest.raises(ValueError, match='Expected SaveData XML root'):
        read_save(MainSave, '<ModSaveData/>')
    with pytest.raises(ValueError, match='Expected ModSaveData XML root'):
        read_save(ModBackupSave, '<SaveData/>')
    collab = read_save(
        CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Map': '<SaveData/>'}})
    )
    # Invalid documents must not enter the lazy cache and bypass validation later.
    for _ in range(2):
        with pytest.raises(ValueError, match=r"session 'Map'.*validation errors for Session"):
            collab.sessions['Map'].session.side  # noqa: B018


def _collab() -> CollabUtils2Save:
    return read_save(
        CollabUtils2Save,
        yaml.safe_dump(
            {
                'SessionsPerLevel': {'Map': SESSION, 'broken': '<broken'},
                'ModSessionsPerLevel': {'Map': {'Helper': 'untouched'}},
            }
        ),
    )


def test_collab_parses_only_requested_identity_and_preserves_mod_payloads() -> None:
    collab = _collab()
    with pytest.raises(ValueError):
        save_bytes(collab)
    del collab.sessions['broken']
    original = save_bytes(collab)
    assert collab.sessions['Map'].session.side is LevelSide.A
    assert collab.sessions['Map'].session.side is not LevelSide.B
    assert 'missing' not in collab.sessions
    assert save_bytes(collab) == original
    pending = collab.sessions.get('Map')
    assert pending is not None
    session = pending.session
    assert session is not None
    copied = reopen_save(collab)
    changed = copied.sessions['Map'].session
    changed.deaths = 8
    changed.respawn = None
    assert save_bytes(collab) == original and save_bytes(copied) != original
    restored = read_save(CollabUtils2Save, save_bytes(copied))
    restored_session = restored.sessions['Map'].session
    assert restored_session.deaths == 8 and restored_session.respawn is None
    assert restored_session.room == session.room
    assert restored_session.time_played == session.time_played
    assert restored_session.room_deaths == session.room_deaths
    data = yaml.safe_load(save_bytes(restored))
    assert data['ModSessionsPerLevel'] == {'Map': {'Helper': 'untouched'}}
    assert ET.fromstring(data['SessionsPerLevel']['Map']).findtext('Flags/string') == 'keep'


def test_collab_index_does_not_require_full_session_fields() -> None:
    file = read_save(
        CollabUtils2Save,
        yaml.safe_dump(
            {
                'SessionsPerLevel': {
                    '01': '<Session InArea="true"><Area SID="01" Mode="CSide"/></Session>'
                }
            }
        ),
    )
    session = file.sessions.get('01')
    assert session is not None and session.sid == '01'
    with pytest.raises(ValueError):
        session.session  # noqa: B018
    with pytest.raises(ValueError):
        save_bytes(file)


def test_collab_alias_edits_do_not_modify_other_unknown_paths() -> None:
    data = yaml.safe_dump({'Map': SESSION})
    with pytest.warns(UnknownSaveFieldWarning, match='CollabUtils2Save.Future'):
        file = CollabUtils2Save.fromstring(
            'SessionsPerLevel: &sessions\n'
            + '\n'.join('  ' + line for line in data.splitlines())
            + '\nFuture: *sessions\n',
            unknown='warn',
        )
    pending = file.sessions.get('Map')
    assert pending is not None
    session = pending.session
    session.deaths = 9
    result = yaml.safe_load(save_bytes(file))
    assert 'Future' not in result
    assert result['SessionsPerLevel']['Map'] != SESSION


def test_unknown_yaml_tags_and_scalars_are_not_reconstructed() -> None:
    with pytest.warns(UnknownSaveFieldWarning, match='EndersBlenderSave.Future'):
        file = EndersBlenderSave.fromstring(
            f"""{ORDER}: {{Map: ["01"]}}
Future: !custom {{leading: 01, boolean: true, literal: "true"}}
""",
            unknown='warn',
        )
    stats = file.first_clear.get('Map')
    assert stats is not None
    file.first_clear['Map'] = replace(
        stats, room_order=['goal'], room_deaths={'goal': 0}, room_times={'goal': Duration()}
    )
    result = yaml.safe_load(save_bytes(file))
    assert 'Future' not in result
    assert result[ORDER]['Map'] == ['goal']


def test_enders_partial_protocol_keys_edit_copy_and_precise_ticks() -> None:
    file = read_save(
        EndersBlenderSave,
        yaml.safe_dump(
            {
                ORDER: {'Map_B': ['01']},
                DEATHS: {'Map_B': {'01': 0}, 'OnlyDeaths': {'01': 2}},
                TIMER: {'Map_B': {'01': 0}, 'OnlyTime': {'01': -1}},
            }
        ),
    )
    assert list(file.first_clear.keys()) == ['Map_B', 'OnlyDeaths', 'OnlyTime']
    stats = file.first_clear.get('OnlyTime')
    assert stats is not None and stats.room_times['01'].to_dotnet_ticks() == -1
    assert file.first_clear.get('Missing') is None
    original = save_bytes(file)
    copied = reopen_save(file)
    deaths = {'goal': 4}
    times = {'goal': Duration.from_dotnet_ticks(17)}
    replacement = RoomStats(['goal'], deaths, times)
    copied.first_clear['Map_B'] = deepcopy(replacement)
    deaths['goal'] = 999
    times['goal'] = Duration()
    assert save_bytes(file) == original and save_bytes(copied) != original
    restored = read_save(EndersBlenderSave, save_bytes(copied))
    result = restored.first_clear.get('Map_B')
    assert copied.first_clear.get('Map_B') == result
    assert dict(copied.first_clear.items()) == dict(restored.first_clear.items())
    assert result == RoomStats(['goal'], {'goal': 4}, {'goal': Duration.from_dotnet_ticks(17)})
    assert restored.first_clear.get('OnlyDeaths') == file.first_clear.get('OnlyDeaths')
    assert result is not None
    result.room_deaths['goal'] = 0
    assert restored.first_clear.get('Map_B') is result
    assert copied.first_clear['Map_B'].room_deaths['goal'] == 4


def test_enders_encoding_rejects_invalid_values_without_changing_input() -> None:
    file = read_save(EndersBlenderSave, f'{ORDER}:\n  Map: [start]\n')
    stats = file.first_clear.get('Map')
    assert stats is not None
    stats.room_deaths['start'] = 0
    stats.room_times['start'] = Duration()
    original = save_bytes(file)
    file.first_clear['Map'] = stats
    assert save_bytes(file) == original
    file.first_clear['Map'] = RoomStats(['goal'], {'goal': 1}, {'goal': Duration(Fraction(1, 3))})
    with pytest.raises(ValueError):
        save_bytes(file)
    file.first_clear['Map'] = stats
    assert save_bytes(file) == original
    assert file.first_clear.get('Map') == stats
    file.first_clear['missing'] = stats
    assert file.first_clear['missing'] is stats


@pytest.mark.parametrize(
    'field, value',
    ((DEATHS, '-1'), (DEATHS, str(2**31)), (TIMER, 'invalid'), (TIMER, str(2**63))),
)
def test_enders_supported_fields_are_validated_on_load(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        read_save(EndersBlenderSave, f'{field}: {{Map: {{room: {value}}}}}')


@pytest.mark.parametrize(
    'data',
    (
        f'{ORDER}: {{Good: [room], Broken: 1}}',
        f'{DEATHS}: {{Good: {{room: 1}}, Broken: {{room: -1}}}}',
        f'{TIMER}: {{Good: {{room: 1}}, Broken: {{room: invalid}}}}',
    ),
)
def test_enders_load_checks_all_records_without_waiting_for_queries(data: str) -> None:
    with pytest.raises(ValueError):
        read_save(EndersBlenderSave, data)


@pytest.mark.parametrize('data', ('Field: 1\nField: 2', '[]:'))
def test_enders_invalid_root_keys_are_rejected_when_parsed(data: str) -> None:
    with pytest.raises(ValueError, match='unique scalar'):
        read_save(EndersBlenderSave, data)


@pytest.mark.parametrize('data', ('Field: 1\nField: 2', '[]:'))
def test_collab_invalid_root_keys_are_rejected_when_parsed(data: str) -> None:
    with pytest.raises(ValueError, match='unique scalar'):
        read_save(CollabUtils2Save, data)


@pytest.mark.parametrize('cls', (CollabUtils2Save, EndersBlenderSave))
@pytest.mark.parametrize('data', ('[]', 'null', 'text', '{'))
def test_bad_yaml_root_shapes_fail(cls: type[Save], data: str) -> None:
    with pytest.raises(ValueError):
        read_save(cls, data)


@pytest.mark.parametrize('value', ('[]', '{}'))
def test_collab_session_entries_must_be_scalars(value: str) -> None:
    with pytest.raises(ValueError, match='Expected a YAML scalar'):
        read_save(CollabUtils2Save, f'SessionsPerLevel: {{Map: {value}}}')


def test_file_read_has_path_context_and_never_writes(tmp_path: Path) -> None:
    path = tmp_path / 'chosen-name.celeste'
    data = save_bytes(_main())
    path.write_bytes(data)
    file = MainSave.read(path, strict=False)
    assert save_bytes(MainSave.read(str(path), strict=False)) == save_bytes(file)
    file.deaths = 9
    save_bytes(file)
    assert path.read_bytes() == data
    with pytest.raises(FileNotFoundError, match=r'missing\.celeste'):
        MainSave.read(tmp_path / 'missing.celeste')


def test_file_io_errors_keep_their_original_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = PermissionError('Denied')

    def denied(file: str | Path, mode: str) -> BytesIO:
        raise error

    monkeypatch.setattr('berries.text_doc.open', denied, raising=False)
    with pytest.raises(PermissionError) as caught:
        MainSave.read(tmp_path / 'save')
    assert caught.value is error


def test_invalid_file_contents_retain_path_context(tmp_path: Path) -> None:
    path = tmp_path / 'broken.celeste'
    path.write_bytes(b'<broken')
    with pytest.raises(ValueError, match=r'broken\.celeste'):
        MainSave.read(path)
