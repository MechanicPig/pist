"""Public backup reading without installed maps, global environment edits or private indexes."""

from pathlib import Path

import pytest

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.directory import SavesDir
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.rooms import room_key
from test_support.save_docs import get_map, read_save, require_map

NATIVE_XML = """<SaveData>
<CurrentSession InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Celeste/Map-B" Mode="BSide" /></CurrentSession>
<Areas><AreaStats SID="Celeste/Map-B"><Modes>
<AreaModeStats TimePlayed="10000" Deaths="2" Completed="true" />
<AreaModeStats TimePlayed="0" Deaths="0" />
<AreaModeStats TimePlayed="0" Deaths="0" />
</Modes></AreaStats></Areas>
<LevelSetRecycleBin><LevelSetStats Name="Removed"><Areas><AreaStats SID="Removed/Map"><Modes>
<AreaModeStats TimePlayed="20000" Deaths="3" />
<AreaModeStats TimePlayed="0" Deaths="0" />
<AreaModeStats TimePlayed="0" Deaths="0" />
</Modes></AreaStats></Areas></LevelSetStats></LevelSetRecycleBin>
</SaveData>"""

BLENDER_YAML = """mapDict_roomStat_firstClear_roomOrder:
  Pack/Map_B: [start, end]
mapDict_roomStat_firstClear_death:
  Pack/Map_B: {start: 2}
  Only/Deaths: {room: 3}
mapDict_roomStat_firstClear_timer:
  Only/Timers: {room: 10000}
"""


@pytest.mark.parametrize('as_bytes', (False, True))
def test_native_xml_input_exposes_all_stats_and_session_without_maps(as_bytes: bool) -> None:
    result = read_save(MainSave, NATIVE_XML.encode() if as_bytes else NATIVE_XML)
    entries = {
        (level.sid, side): progress
        for campaign in (
            result.vanilla,
            *result.mods.campaigns.values(),
            *result.mods.recycled_campaigns.values(),
        )
        for level in campaign.levels.values()
        for side, progress in zip(LevelSide, level.maps, strict=False)
    }
    assert set(entries) == {
        ('Celeste/Map-B', LevelSide.A),
        ('Celeste/Map-B', LevelSide.B),
        ('Celeste/Map-B', LevelSide.C),
        ('Removed/Map', LevelSide.A),
        ('Removed/Map', LevelSide.B),
        ('Removed/Map', LevelSide.C),
    }
    assert require_map(entries['Celeste/Map-B', LevelSide.A]).deaths == 2
    assert require_map(entries['Celeste/Map-B', LevelSide.B]).time_played == Duration()
    assert result.current_map == ('Celeste/Map-B', LevelSide.B)


def test_directory_does_not_merge_and_single_backup_does_not_read_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('EVEREST_SAVEPATH', str(tmp_path / 'unrelated'))
    native = tmp_path / '0.celeste'
    native.write_text(NATIVE_XML, encoding='utf-8')
    supplemental = tmp_path / '0-modsavedata.celeste'
    supplemental.write_text(
        '<ModSaveData><CurrentSession_Safe InArea="true" Level="room" Time="0" '
        'Deaths="0" DeathsInCurrentLevel="0">'
        '<Area SID="Mod/Extra" Mode="CSide" /></CurrentSession_Safe>'
        '<LevelSets><LevelSetStats Name="Mod"><Areas>'
        '<AreaStats SID="Mod/Extra"><Modes>'
        '<AreaModeStats TimePlayed="30000" Deaths="4" /></Modes></AreaStats>'
        '</Areas></LevelSetStats></LevelSets></ModSaveData>',
        encoding='utf-8',
    )
    (tmp_path / '0-modsave-CollabUtils2.celeste').write_text(
        'SessionsPerLevel:\n  Kept/Map: <Session InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Kept/Map" Mode="BSide" /></Session>\n',
        encoding='utf-8',
    )
    reader = SavesDir(tmp_path)
    assert [slot.number for slot in reader.slots.iter(filter_empty=True)] == [0]
    combined = reader.slots[0]
    assert combined.number == 0
    main = combined.main(strict=False)
    assert main.current_map == ('Celeste/Map-B', LevelSide.B)
    collab = combined.mod(CollabUtils2Save)
    assert collab.sessions['Kept/Map'].session.side is LevelSide.B
    stats = get_map(main, 'Celeste/Map-B', LevelSide.A)
    assert stats is not None and stats.deaths == 2
    assert sum(len(level.maps) for level in main.flat_levels.values()) == 3
    assert sum(len(level.maps) for level in main.recycled_flat_levels.values()) == 3
    assert len(main.mods.recycled_campaigns['Removed'].levels) == 1

    backup = MainSave.read(native, strict=False)
    assert get_map(backup, 'Mod/Extra', LevelSide.A) is None
    assert backup.current_map == ('Celeste/Map-B', LevelSide.B)
    supplement_only = ModBackupSave.read(supplemental, strict=False)
    assert supplement_only.mods.session is not None
    assert (supplement_only.mods.session.sid, supplement_only.mods.session.side) == (
        'Mod/Extra',
        LevelSide.C,
    )
    assert supplement_only.mods.campaigns.get('Removed') is None


@pytest.mark.parametrize('as_bytes', (False, True))
def test_blender_yaml_enumerates_all_combined_records(as_bytes: bool) -> None:
    result = read_save(EndersBlenderSave, BLENDER_YAML.encode() if as_bytes else BLENDER_YAML)
    records = dict(result.first_clear.items())
    assert set(records) == {'Pack/Map_B', 'Only/Deaths', 'Only/Timers'}
    assert records['Pack/Map_B'] == result.first_clear.get(room_key('Pack/Map', LevelSide.B))
    assert records['Pack/Map_B'].room_order == ['start', 'end']
    assert records['Only/Deaths'].room_deaths == {'room': 3}
    assert records['Only/Timers'].room_times == {'room': Duration.from_dotnet_ticks(10000)}


def test_blender_backup_file_and_directory_ignore_active_save_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('EVEREST_SAVEPATH', str(tmp_path / 'unrelated'))
    arbitrary = tmp_path / 'backup.yaml'
    arbitrary.write_text(BLENDER_YAML, encoding='utf-8')
    standalone = EndersBlenderSave.read(arbitrary, strict=False)
    (tmp_path / '2-modsave-EndersBlender.celeste').write_text(BLENDER_YAML, encoding='utf-8')
    (tmp_path / '2.celeste').write_text('<SaveData/>', encoding='utf-8')
    reader = SavesDir(tmp_path)
    assert [slot.number for slot in reader.slots.iter(filter_empty=True)] == [2]
    slot = reader.slots[2]
    assert slot.number == 2
    loaded = slot.mod(EndersBlenderSave, strict=False)
    assert dict(loaded.first_clear.items()) == dict(standalone.first_clear.items())


@pytest.mark.parametrize('data', ('<broken', '<Other />'))
def test_native_xml_input_rejects_invalid_save_data(data: str) -> None:
    with pytest.raises(ValueError):
        read_save(MainSave, data)


@pytest.mark.parametrize('ticks', (1, -1, -170_001))
def test_native_xml_preserves_signed_submillisecond_time(ticks: int) -> None:
    result = read_save(MainSave, NATIVE_XML.replace('TimePlayed="10000"', f'TimePlayed="{ticks}"'))
    stats = get_map(result, 'Celeste/Map-B', LevelSide.A)
    assert stats is not None
    assert stats.time_played.to_dotnet_ticks() == ticks
    assert stats.deaths == 2


@pytest.mark.parametrize('ticks', (10_001, -10_001))
def test_blender_yaml_preserves_signed_submillisecond_time(ticks: int) -> None:
    result = read_save(EndersBlenderSave, BLENDER_YAML.replace('10000', str(ticks)))
    stats = result.first_clear.get('Only/Timers')
    assert stats is not None
    assert stats.room_times['room'].to_dotnet_ticks() == ticks


def test_backup_read_errors_include_the_selected_file(tmp_path: Path) -> None:
    path = tmp_path / 'missing.celeste'
    with pytest.raises(FileNotFoundError, match=r'missing\.celeste'):
        MainSave.read(path)
    with pytest.raises(FileNotFoundError, match=r'missing\.celeste'):
        EndersBlenderSave.read(path)
