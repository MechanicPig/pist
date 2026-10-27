"""Main and backup file APIs use editable domain values, not node proxies."""

from copy import deepcopy
from io import BytesIO

import pytest
from lxml import etree
from pydantic import ValidationError

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves import operations
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress import CampaignProgress, LevelProgress, Session
from berries.game.saves.progress.members import CampaignMembers
from test_support.save_docs import (
    complete_vanilla,
    normal_save_xml,
    parsed_session,
    read_save,
    require_map,
    save_bytes,
)

_MODES = '<Modes>' + '<AreaModeStats TimePlayed="170000" Deaths="2"/>' * 3 + '</Modes>'


def _area(sid: str) -> str:
    return f'<AreaStats SID="{sid}" Cassette="true">{_MODES}</AreaStats>'


def _session(sid: str, *, tag: str = 'CurrentSession_Safe', deaths: int = 3) -> str:
    return (
        f'<{tag} InArea="true" Level="room" Time="00170000" Deaths="{deaths}" '
        f'DeathsInCurrentLevel="1"><Area SID="{sid}" Mode="BSide"/>'
        '<RespawnPoint><X>8.000</X><Y>16.000</Y></RespawnPoint>'
        '<Audio><Music Event="event:/music/test"/></Audio>'
        f'<OldStats SID="{sid}"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></{tag}>'
    )


def _main(session: str = '') -> MainSave:
    return read_save(
        MainSave,
        complete_vanilla(
            '<?before keep?><SaveData xmlns:e="urn:source">'
            '<Name>Player</Name><Time>-0001</Time><TotalDeaths>0020</TotalDeaths>'
            '<UnlockedAreas>02</UnlockedAreas><TotalStrawberries>0</TotalStrawberries>'
            '<Poem><string>z</string><string>a</string></Poem>'
            f'<Areas>{_area("Celeste/0-Intro")}</Areas>'
            '<LevelSets><LevelSetStats Name="Celeste"><Areas/><Poem/></LevelSetStats>'
            f'<LevelSetStats Name="Pack"><Areas>{_area("Pack/Map")}</Areas></LevelSetStats></LevelSets>'
            f'<LevelSetRecycleBin><LevelSetStats Name="Old"><Areas>{_area("Old/Map")}</Areas>'
            f'</LevelSetStats></LevelSetRecycleBin>{session}'
            '</SaveData><!--after--><?after keep?>'
        ),
    )


def test_main_domain_views_and_globals_keep_distinct_ownership() -> None:
    main = _main()
    assert main.name == 'Player'
    assert main.time_played == Duration.from_dotnet_ticks(-1) and main.deaths == 20
    assert main.vanilla.name == 'Celeste'
    assert [campaign.name for campaign in main.mods.campaigns.values()] == ['Pack']
    assert [campaign.name for campaign in main.mods.recycled_campaigns.values()] == ['Old']
    assert (
        main.recycled_flat_levels['Old/Map'].maps[LevelSide.A]
        is next(iter(main.mods.recycled_campaigns['Old'].levels.values())).maps[0]
    )
    level = main.flat_levels['Pack/Map']
    progress = require_map(main.flat_levels['Pack/Map'].maps[LevelSide.B])
    assert progress is level.maps[LevelSide.B]
    progress.deaths = 5
    assert main.deaths == 20
    main.name = 'Other'
    main.time_played = Duration.from_seconds(12)
    main.deaths = 25
    main.assist_mode = True
    main.variant_mode = True
    main.vanilla.poem.append('z')
    restored = MainSave.fromstring(save_bytes(main), strict=False)
    assert restored.name == 'Other' and restored.assist_mode and restored.variant_mode
    assert restored.time_played == Duration.from_seconds(12) and restored.deaths == 25
    assert require_map(restored.flat_levels['Pack/Map'].maps[LevelSide.B]).deaths == 5
    assert restored.vanilla.poem == ['z', 'a', 'z']


def test_held_views_follow_assigned_snapshots_and_vanilla_without_hidden_copies() -> None:
    main = _main()
    levels = main.flat_levels
    previous = main.vanilla.snapshot()
    replacement = CampaignProgress('Celeste', [LevelProgress('Celeste/New')])
    main.install_campaigns((main, replacement))
    assert (
        levels['Celeste/New'] is next(iter(replacement.levels.values()))
        and 'Celeste/0-Intro' not in levels
    )
    assert require_map(previous.levels['Celeste/0-Intro'].maps[0]).deaths == 2
    main.install_mod_progress(
        ModSaveData(
            campaigns=CampaignMembers([CampaignProgress('Other', [LevelProgress('Other/Map')])])
        )
    )
    assert levels['Other/Map'] is next(iter(main.mods.campaigns['Other'].levels.values()))
    assert 'Pack/Map' not in levels
    assert not main.needs_mod_recovery
    assert deepcopy(main.mods.campaigns)['Other'] is not main.mods.campaigns['Other']


def test_mod_campaigns_can_be_cleared_without_touching_vanilla_or_celeste_metadata() -> None:
    main = _main()
    vanilla = main.vanilla
    entry = main.mods.vanilla_entry
    assert entry is not None
    main.mods.campaigns.clear()
    assert not main.mods.campaigns
    assert main.vanilla is vanilla and 'Celeste/0-Intro' in main.flat_levels
    assert 'Pack/Map' not in main.flat_levels
    root = etree.fromstring(save_bytes(main))
    group = root.find('LevelSets')
    assert group is not None
    assert [node.get('Name') for node in group] == ['Celeste']
    assert group.find('LevelSetStats/Poem/string') is None
    assert main.mods.vanilla_entry is entry
    assert main.vanilla.poem == ['z', 'a']


def test_main_noop_is_canonical_and_edits_preserve_other_values() -> None:
    main = _main(_session('Pack/Map'))
    before = save_bytes(main)
    assert main.current_map == ('Pack/Map', LevelSide.B)
    session = main.session
    assert session is not None and session.respawn == (8, 16)
    assert save_bytes(main) == before
    main.deaths = 21
    encoded = save_bytes(main)
    restored = read_save(MainSave, encoded)
    assert restored.time_played == Duration.from_dotnet_ticks(-1)
    assert (
        restored.session is not None
        and restored.session.time_played == Duration.from_dotnet_ticks(170000)
    )
    assert restored.session.respawn == (8, 16)
    assert restored.session.audio.music.event == 'event:/music/test'
    assert b'berries-' not in encoded and b'<?after keep?>' not in encoded


def test_new_save_detects_in_place_mod_changes_against_empty_baseline() -> None:
    main = MainSave()
    assert etree.fromstring(save_bytes(main)).findtext('HasModdedSaveData') == 'false'
    main.mods.campaigns.add(CampaignProgress('New', [LevelProgress('New/Map')]))
    assert etree.fromstring(save_bytes(main)).findtext('HasModdedSaveData') == 'true'
    main.mods.campaigns.clear()
    assert etree.fromstring(save_bytes(main)).findtext('HasModdedSaveData') == 'false'


def test_loaded_save_detects_and_reverts_nested_mod_changes() -> None:
    main = _main()
    assert etree.fromstring(save_bytes(main)).findtext('HasModdedSaveData') == 'false'
    progress = require_map(main.flat_levels['Pack/Map'].maps[0])
    progress.deaths += 1
    assert etree.fromstring(save_bytes(main)).findtext('HasModdedSaveData') == 'true'
    restored = read_save(MainSave, save_bytes(main))
    assert etree.fromstring(save_bytes(restored)).findtext('HasModdedSaveData') == 'true'
    progress.deaths -= 1
    assert etree.fromstring(save_bytes(main)).findtext('HasModdedSaveData') == 'false'


def test_same_identity_edits_sync_only_changed_fields_and_keep_each_audio_state() -> None:
    legacy = _session('Celeste/0-Intro', tag='CurrentSession', deaths=5).replace(
        'event:/music/test', 'event:/music/legacy'
    )
    main = _main(legacy + _session('Celeste/0-Intro'))
    session = main.session
    assert session is not None and session.deaths == 3
    session.room_deaths = 9
    encoded = save_bytes(main)
    root = etree.fromstring(encoded)
    old, safe = root.find('CurrentSession'), root.find('CurrentSession_Safe')
    assert old is not None and safe is not None
    assert old.get('Deaths') == '5' and safe.get('Deaths') == '3'
    assert old.get('DeathsInCurrentLevel') == safe.get('DeathsInCurrentLevel') == '9'
    old_music, safe_music = old.find('Audio/Music'), safe.find('Audio/Music')
    assert old_music is not None and old_music.get('Event') == 'event:/music/legacy'
    assert safe_music is not None and safe_music.get('Event') == 'event:/music/test'


@pytest.mark.parametrize('sid', ('Celeste/1-ForsakenCity', 'Celeste/Other', 'Mod/Other'))
def test_replacing_session_identity_follows_vanilla_compatibility(sid: str) -> None:
    main = _main(_session('Celeste/0-Intro', tag='CurrentSession') + _session('Celeste/0-Intro'))
    new = parsed_session(sid, LevelSide.C, room='new', deaths=4)
    main.session = new
    assert main.session is new and main.current_map == (sid, LevelSide.C)
    root = etree.fromstring(save_bytes(main))
    legacy, safe = root.find('CurrentSession'), root.find('CurrentSession_Safe')
    assert safe is not None
    area = safe.find('Area')
    assert area is not None and area.get('SID') == sid
    if sid == 'Celeste/1-ForsakenCity':
        assert legacy is not None
        area = legacy.find('Area')
        assert area is not None and area.get('ID') == '1' and area.get('SID') is None
    else:
        assert legacy is None
    main.session = None
    assert main.session is None and main.current_map is None
    root = etree.fromstring(save_bytes(main))
    assert root.find('CurrentSession') is None and root.find('CurrentSession_Safe') is None


def test_missing_safe_falls_back_but_nil_or_inactive_safe_does_not_revive_legacy() -> None:
    legacy = _session('Celeste/0-Intro', tag='CurrentSession')
    nil = '<CurrentSession_Safe xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>'
    with pytest.raises(BadProgressSave, match='Nil CurrentSession_Safe'):
        _main(legacy + nil)
    main = _main(legacy)
    assert main.current_map == ('Celeste/0-Intro', LevelSide.B)
    assert main.session is not None
    inactive = _main(legacy + _session('Pack/Map').replace('InArea="true"', 'InArea="false"'))
    assert inactive.current_map is None
    session = inactive.session
    assert session is not None and not session.in_level and session.sid == 'Pack/Map'


def test_in_level_edit_keeps_identity_and_updates_matching_legacy_peer() -> None:
    main = _main(_session('Celeste/0-Intro', tag='CurrentSession') + _session('Celeste/0-Intro'))
    baseline = save_bytes(main)
    session = main.session
    assert session is not None and session.in_level
    session.in_level = False
    assert main.current_map is None
    assert session.sid == 'Celeste/0-Intro' and session.side is LevelSide.B
    root = etree.fromstring(save_bytes(main))
    for name in ('CurrentSession', 'CurrentSession_Safe'):
        node = root.find(name)
        assert node is not None and node.get('InArea') == 'false'
        area = node.find('Area')
        assert area is not None
        assert area.get('SID') == ('Celeste/0-Intro' if name.endswith('_Safe') else None)
    restored = read_save(MainSave, save_bytes(main))
    value = restored.session
    assert value is not None and not value.in_level and value.side is LevelSide.B
    session.in_level = True
    assert main.current_map == ('Celeste/0-Intro', LevelSide.B)
    assert save_bytes(main) == baseline


@pytest.mark.parametrize('via_main', [False, True])
def test_inactive_lazy_session_keeps_current_queries_empty(via_main: bool) -> None:
    main = _main(_session('Celeste/0-Intro', tag='CurrentSession'))
    inactive = Session.from_xml(
        etree.fromstring(_session('Pack/Map').replace('InArea="true"', 'InArea="false"'))
    )
    assert inactive.sid == 'Pack/Map' and inactive.side is LevelSide.B
    assert not inactive.in_level
    if via_main:
        main.session = inactive
    else:
        main.mods.session = inactive
    assert main.mods.has_session_data
    assert main.mods.current_map is None and main.mods.session is inactive
    assert main.current_map is None and main.session is inactive
    restored = read_save(MainSave, save_bytes(main))
    assert restored.mods.has_session_data
    assert restored.current_map is None
    session = restored.session
    assert session is not None and not session.in_level and session.side is LevelSide.B


def test_vanilla_session_edits_do_not_create_a_safe_session() -> None:
    main = _main(_session('Celeste/0-Intro', tag='CurrentSession'))
    session = main.session
    assert session is not None
    session.deaths = 8
    main.session = session
    root = etree.fromstring(save_bytes(main))
    legacy = root.find('CurrentSession')
    assert legacy is not None and legacy.get('Deaths') == '8'
    safe_node = root.find('CurrentSession_Safe')
    assert safe_node is None or safe_node.get('{http://www.w3.org/2001/XMLSchema-instance}nil') == (
        'true'
    )
    assert not main.mods.has_session_data


@pytest.mark.parametrize('tag', ['CurrentSession', 'CurrentSession_Safe'])
def test_current_session_is_fully_validated_during_loading(tag: str) -> None:
    node = parsed_session('Celeste/0-Intro', LevelSide.B).to_xml()
    node.tag = tag
    node.set('Deaths', 'invalid')
    xml = etree.tostring(node, encoding='unicode')
    with pytest.raises(ValidationError):
        _main(xml)


@pytest.mark.parametrize('tag', ['CurrentSession', 'CurrentSession_Safe'])
@pytest.mark.parametrize('strict', [False, True])
def test_nil_current_session_is_not_treated_as_absence(tag: str, strict: bool) -> None:
    xml = (
        f'<{tag} xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:nil="true" Future="keep"/>'
    )
    with pytest.raises(BadProgressSave, match=f'Nil {tag}'):
        MainSave.fromstring(normal_save_xml(f'<SaveData>{xml}</SaveData>'), strict=strict)


def test_original_ids_are_shared_by_level_and_vanilla_session() -> None:
    data = (
        f'<SaveData><Areas><AreaStats ID="1">{_MODES}</AreaStats></Areas>'
        '<CurrentSession InArea="true" Level="r" Time="1" Deaths="2" DeathsInCurrentLevel="0">'
        '<Area ID="1" Mode="CSide"/><OldStats ID="1"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession></SaveData>'
    )
    data = normal_save_xml(data).encode()
    main = MainSave.read(BytesIO(data), strict=False)
    assert main.flat_levels['Celeste/1-ForsakenCity'].sid == 'Celeste/1-ForsakenCity'
    assert main.current_map == ('Celeste/1-ForsakenCity', LevelSide.C)
    assert main.session is not None and main.session.room == 'r'
    assert b'SID=' not in save_bytes(main)


def test_record_queries_do_not_require_missing_accumulated_data() -> None:
    modes = (
        '<AreaModeStats BestTime="170000" SingleRunCompleted="true" BestDeaths="0" BestDashes="3"/>'
    )
    main = read_save(
        MainSave,
        complete_vanilla(
            f'<SaveData><Areas><AreaStats SID="Celeste/0-Intro"><Modes>{modes * 3}</Modes></AreaStats></Areas></SaveData>'
        ),
    )
    progress = require_map(main.flat_levels['Celeste/0-Intro'].maps[LevelSide.A])
    assert progress.bests.time == Duration.from_timer_frames(1)
    assert progress.bests.deaths == 0 and progress.bests.dashes == 3
    assert progress.time_played == Duration() and progress.deaths == 0


def test_invalid_known_records_and_duplicate_containers_fail_during_loading() -> None:
    data = (
        '<SaveData><LevelSets><LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map">'
        '<Modes>'
        + '<AreaModeStats TimePlayed="170000" Deaths="2" BestTime="bad"><Checkpoints/><Checkpoints/></AreaModeStats>'
        * 3
        + '</Modes></AreaStats></Areas></LevelSetStats></LevelSets></SaveData>'
    )
    with pytest.raises(ValueError):
        read_save(
            MainSave,
            complete_vanilla(data.replace('<Checkpoints/><Checkpoints/>', '<Checkpoints/>')),
        )
    with pytest.raises(ValueError, match='Duplicate'):
        read_save(MainSave, complete_vanilla(data.replace('BestTime="bad"', 'BestTime="0"')))


def test_backup_snapshot_assignment_does_not_copy_globals_or_share_mutable_data() -> None:
    main = _main(_session('Pack/Map'))
    with BytesIO() as stream:
        backup = ModBackupSave()
        backup.install_mod_progress(deepcopy(main).mods)
        backup.write(stream)
        restored = ModBackupSave.read(BytesIO(stream.getvalue()))
    assert restored.mods.last_map == main.mods.last_map
    assert restored.mods.session is not None and restored.mods.session.deaths == 3
    require_map(restored.mods.campaigns['Pack'].levels['Pack/Map'].maps[0]).deaths = 8
    assert require_map(main.mods.campaigns['Pack'].levels['Pack/Map'].maps[0]).deaths == 2
    main.install_mod_progress(deepcopy(restored).mods)
    assert (
        main.deaths == 20
        and require_map(main.mods.campaigns['Pack'].levels['Pack/Map'].maps[0]).deaths == 8
    )
    assert main.mods is not restored.mods
    assert not main.needs_mod_recovery
    root = etree.fromstring(save_bytes(main))
    assert root.find('LevelSets/LevelSetStats/Poem/string') is None
    assert root.findtext('Poem/string') == 'z'


def test_explicit_recovery_is_whole_snapshot_and_marks_the_main() -> None:
    main = read_save(
        MainSave,
        complete_vanilla(
            '<SaveData><Name>Main</Name><Time>7</Time><TotalDeaths>8</TotalDeaths></SaveData>'
        ),
    )
    backup = read_save(ModBackupSave, '<ModSaveData/>')
    backup.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/New')]))
    backup.mods.session = parsed_session('Pack/New', LevelSide.A, deaths=4)
    assert main.needs_mod_recovery
    operations.restore_mod_progress(main, backup)
    assert main.flat_levels.get('Pack/New') is not None
    assert main.current_map == ('Pack/New', LevelSide.A)
    assert main.name == 'Main' and main.deaths == 8
    assert main.mods is not backup.mods and not main.needs_mod_recovery
    previous = main.mods
    operations.restore_mod_progress(main, read_save(ModBackupSave, '<ModSaveData/>'))
    assert main.mods is previous


def test_new_file_nested_mod_edit_is_not_replaced_by_an_old_backup() -> None:
    stream = BytesIO()
    main = MainSave()
    main.mods.campaigns.add(CampaignProgress('New', [LevelProgress('New/Map')]))
    stream.seek(0)
    main.write(stream)
    stream.truncate()
    restored = MainSave.read(BytesIO(stream.getvalue()))
    assert not restored.needs_mod_recovery
    backup = read_save(ModBackupSave, '<ModSaveData/>')
    backup.mods.campaigns.add(CampaignProgress('Old', [LevelProgress('Old/Map')]))
    operations.restore_mod_progress(restored, backup)
    assert [campaign.name for campaign in restored.mods.campaigns.values()] == ['New']


@pytest.mark.parametrize('edit', ['map', 'session', 'last_map', 'clear'])
def test_nested_edits_mark_previously_unmarked_mod_progress(edit: str) -> None:
    data = (
        '<SaveData><LevelSets><LevelSetStats Name="Pack"><Areas>'
        + _area('Pack/Map')
        + '</Areas></LevelSetStats></LevelSets>'
        + _session('Pack/Map')
        + '</SaveData>'
    ).encode()
    data = normal_save_xml(data).encode()
    stream = BytesIO(data)
    main = MainSave.read(stream, strict=False)
    assert main.needs_mod_recovery
    if edit == 'map':
        require_map(main.mods.campaigns['Pack'].levels['Pack/Map'].maps[0]).deaths += 1
    elif edit == 'session':
        session = main.mods.session
        assert session is not None
        session.deaths += 1
    elif edit == 'last_map':
        main.mods.last_map = ('Pack/Map', LevelSide.C)
    else:
        main.mods.campaigns.clear()
    stream.seek(0)
    main.write(stream)
    stream.truncate()
    restored = MainSave.read(BytesIO(stream.getvalue()))
    assert not restored.needs_mod_recovery


@pytest.mark.parametrize('edit_name', [False, True])
def test_unmodified_mod_snapshot_keeps_recovery_semantics(edit_name: bool) -> None:
    data = (
        '<SaveData xmlns:e="urn:extension"><Name>Before</Name><LevelSets>\n'
        '<LevelSetStats Name="Pack"><Areas>'
        + _area('Pack/Map')
        + '</Areas><Poem><string>metadata</string></Poem></LevelSetStats>\n</LevelSets>'
        + _session('Pack/Map')
        + '</SaveData>'
    ).encode()
    data = normal_save_xml(data).encode()
    stream = BytesIO(data)
    main = MainSave.read(stream, strict=False)
    assert main.needs_mod_recovery
    assert stream.getvalue() == data
    stream = BytesIO(data)
    main = MainSave.read(stream, strict=False)
    assert main.mods.session is not None
    if edit_name:
        main.name = 'After'
    stream.seek(0)
    main.write(stream)
    stream.truncate()
    restored = MainSave.read(BytesIO(stream.getvalue()))
    assert restored.needs_mod_recovery
    assert restored.name == ('After' if edit_name else 'Before')


@pytest.mark.parametrize('save_type,root', ((MainSave, 'SaveData'), (ModBackupSave, 'ModSaveData')))
def test_strict_side_shape_and_membership_apply_at_the_file_boundary(
    save_type: type[MainSave | ModBackupSave], root: str
) -> None:
    data = f'<{root}><LevelSets><LevelSetStats Name="Pack"><Areas><AreaStats SID="Other/Map"><Modes><AreaModeStats TimePlayed="0" Deaths="0"/></Modes></AreaStats></Areas></LevelSetStats></LevelSets></{root}>'.encode()
    with pytest.raises(BadProgressSave):
        save_type.read(BytesIO(data))
    lenient = save_type.read(BytesIO(data), strict=False)
    assert lenient.mods.campaigns['Pack'].levels['Other/Map'].sid == 'Other/Map'
    with pytest.raises(BadProgressSave):
        save_bytes(lenient)
