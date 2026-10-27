"""Game timers use 17 ms steps; wall-clock RTA retains independent precision."""

from io import BytesIO

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.progress import CampaignProgress, LevelProgress, MapProgress, Session
from berries.game.saves.rooms import RoomStats
from test_support.save_docs import parsed_session


@pytest.mark.parametrize('ticks', [0, 170000, 340000, 1, 170001])
@pytest.mark.parametrize('field', ['Time', 'TimePlayed', 'BestTime', 'BestFullClearTime'])
def test_main_timer_precision_is_checked_only_on_strict_read(ticks: int, field: str) -> None:
    root = etree.fromstring(MainSave().tostring())
    if field == 'Time':
        node = root.find('Time')
        assert node is not None
        node.text = str(ticks)
    else:
        node = root.find('Areas/AreaStats/Modes/AreaModeStats')
        assert node is not None
        node.set(field, str(ticks))
    xml = etree.tostring(root)
    if ticks % 170000:
        with pytest.raises(ValueError, match='17 ms'):
            MainSave.fromstring(xml)
    else:
        MainSave.fromstring(xml)
    loose = MainSave.fromstring(xml, strict=False)
    restored = MainSave.fromstring(loose.tostring(), strict=False)
    assert restored.tostring() == loose.tostring()


@pytest.mark.parametrize('field', ['TimePlayed', 'BestTime', 'BestFullClearTime'])
def test_backup_nested_timers_receive_strict_policy(field: str) -> None:
    save = ModBackupSave()
    save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    root = etree.fromstring(save.tostring())
    node = root.find('LevelSets/LevelSetStats/Areas/AreaStats/Modes/AreaModeStats')
    assert node is not None
    node.set(field, '1')
    xml = etree.tostring(root)
    with pytest.raises(ValueError, match=field):
        ModBackupSave.fromstring(xml)
    loose = ModBackupSave.fromstring(xml, strict=False)
    assert ModBackupSave.fromstring(loose.tostring(), strict=False).tostring() == loose.tostring()


@pytest.mark.parametrize('nested', [False, True])
def test_session_timer_and_old_stats_follow_strict_policy(nested: bool) -> None:
    session = parsed_session('Pack/Map', LevelSide.A)
    root = etree.fromstring(session.tostring())
    if nested:
        node = root.find('OldStats/Modes/AreaModeStats')
        assert node is not None
        node.set('TimePlayed', '1')
    else:
        root.set('Time', '1')
    with pytest.raises(ValueError, match='17 ms'):
        Session.from_xml(root, strict=True)
    loose = Session.from_xml(root)
    assert Session.from_xml(tree.parse(loose.tostring()).getroot()) == loose


def test_direct_map_strict_read_checks_timer_without_changing_write_policy() -> None:
    progress = MapProgress(time_played=Duration.from_dotnet_ticks(1))
    root = progress.to_xml()
    with pytest.raises(ValueError, match='TimePlayed'):
        MapProgress.from_xml(root, strict=True)
    assert MapProgress.from_xml(root).time_played == progress.time_played


@pytest.mark.parametrize('strict', [False, True])
def test_document_strict_setting_never_constrains_timer_edits_or_output(strict: bool) -> None:
    duration = Duration.from_dotnet_ticks(1)
    main = MainSave.fromstring(MainSave().tostring(), strict=strict)
    main.time_played = duration
    assert MainSave.fromstring(main.tostring(), strict=False).time_played == duration
    blender = EndersBlenderSave.fromstring('{}', strict=strict)
    blender.first_clear['Pack/Map'] = RoomStats(room_times={'room': duration})
    restored = EndersBlenderSave.fromstring(blender.tostring(), strict=False)
    assert restored.first_clear['Pack/Map'].room_times['room'] == duration


@pytest.mark.parametrize('prefix', ['firstClear', 'latestSession'])
@pytest.mark.parametrize('ticks', [0, 170000, 340000, 1, 170001])
def test_blender_checks_game_timer_but_never_rta(prefix: str, ticks: int) -> None:
    data = (
        f'mapDict_roomStat_{prefix}_timer: {{Pack/Map: {{room: {ticks}}}}}\n'
        f'mapDict_roomStat_{prefix}_rtatimer: {{Pack/Map: {{room: 13}}}}\n'
    )
    if ticks % 170000:
        with pytest.raises(BadProgressSave, match=r'Pack/Map.*room'):
            EndersBlenderSave.fromstring(data)
    else:
        EndersBlenderSave.fromstring(data)
    loose = EndersBlenderSave.read(BytesIO(data.encode()), strict=False)
    records = loose.first_clear if prefix == 'firstClear' else loose.last_saved
    assert records['Pack/Map'].room_times['room'] == Duration.from_dotnet_ticks(ticks)
    assert records['Pack/Map'].room_rta_times['room'] == Duration.from_dotnet_ticks(13)
    encoded = loose.tostring()
    restored = EndersBlenderSave.fromstring(encoded, strict=False)
    assert restored.tostring() == encoded
