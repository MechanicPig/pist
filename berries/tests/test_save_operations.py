"""Explicit recovery, identity queries and preflighted counter correction."""

from copy import copy, deepcopy
from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest
import yaml

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves import operations
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.map import MapProgress
from test_support.save_docs import complete_vanilla, get_map, read_save, require_map, save_bytes


def _area(sid: str, ticks: int = 10) -> str:
    mode = f'<AreaModeStats TimePlayed="{ticks}" Deaths="2" BestTime="5"/>'
    return f'<AreaStats SID="{sid}"><Modes>{mode * 3}</Modes></AreaStats>'


def _session(sid: str, *, room: str = 'room') -> str:
    return (
        f'<CurrentSession_Safe InArea="true" Level="{room}" Time="3" Deaths="1" '
        f'DeathsInCurrentLevel="0"><Area SID="{sid}" Mode="Normal"/>'
        f'<Flags><string>keep</string></Flags><OldStats SID="{sid}"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession_Safe>'
    )


def _main(*, marked: bool = True, n_sets: int = 2, n_recycled: int = 0) -> MainSave:
    sets = ''.join(
        f'<LevelSetStats Name="Pack{i}"><Areas>{_area(f"Pack{i}/Map")}</Areas></LevelSetStats>'
        for i in range(n_sets)
    )
    recycled = ''.join(f'<LevelSetStats Name="Old{i}"/>' for i in range(n_recycled))
    return read_save(
        MainSave,
        complete_vanilla(
            '<SaveData><Time>100</Time><TotalDeaths>20</TotalDeaths>'
            f'<HasModdedSaveData>{str(marked).lower()}</HasModdedSaveData>'
            f'<Areas>{_area("Celeste/0-Intro", 2)}</Areas><LevelSets>{sets}</LevelSets>'
            f'<LevelSetRecycleBin>{recycled}</LevelSetRecycleBin>{_session("Pack0/Map")}'
            '<Flags><string>untouched</string></Flags></SaveData>'
        ),
    )


def _backup(*, session: bool = True) -> ModBackupSave:
    current = _session('Backup/Map', room='backup') if session else ''
    return read_save(
        ModBackupSave,
        f'<ModSaveData><LevelSets><LevelSetStats Name="Backup"><Areas>{_area("Backup/Map", 5)}</Areas>'
        '<Poem><string>backup metadata</string></Poem></LevelSetStats></LevelSets>'
        '<LevelSetRecycleBin><LevelSetStats Name="Old"/></LevelSetRecycleBin>'
        f'<LastArea_Safe SID="Backup/Map"/>{current}</ModSaveData>',
    )


@pytest.mark.parametrize(
    ('marked', 'n_sets', 'n_recycled', 'restore'),
    (
        (False, 0, 0, True),
        (False, 1, 0, True),
        (False, 2, 0, False),
        (False, 0, 1, False),
        (True, 0, 0, False),
        (True, 2, 0, False),
    ),
)
def test_recovery_uses_the_whole_everest_condition_without_sharing(
    marked: bool, n_sets: int, n_recycled: int, restore: bool
) -> None:
    main, backup = _main(marked=marked, n_sets=n_sets, n_recycled=n_recycled), _backup()
    old = main.mods
    old_campaigns = main.mods.campaigns
    donor = save_bytes(backup)
    assert operations.restore_mod_progress(main, backup) is None
    assert main.mods is old
    assert (main.mods.campaigns is old_campaigns) is not restore
    assert main.mods is not backup.mods and save_bytes(backup) == donor
    assert main.current_map == (('Backup/Map' if restore else 'Pack0/Map'), LevelSide.A)
    assert main.needs_mod_recovery is False


def test_recovery_preserves_globals_vanilla_flags_and_never_fills_individual_maps() -> None:
    main, backup = _main(marked=False, n_sets=1), _backup()
    legacy = main.vanilla
    operations.restore_mod_progress(main, backup)
    assert main.time_played == Duration.from_dotnet_ticks(100) and main.deaths == 20
    assert main.vanilla is legacy
    assert get_map(main, 'Pack0/Map', LevelSide.A) is None
    assert require_map(main.flat_levels['Backup/Map'].maps[LevelSide.A]).deaths == 2
    assert len(main.mods.recycled_campaigns) == 1
    root = ET.fromstring(save_bytes(main))
    assert root.findtext('Flags/string') == 'untouched'
    assert root.findtext('LevelSets/LevelSetStats/Poem/string') == 'backup metadata'
    before = save_bytes(main)
    operations.restore_mod_progress(main, read_save(ModBackupSave, '<ModSaveData/>'))
    assert save_bytes(main) == before


def test_snapshot_recovery_can_clear_current_without_altering_the_backup() -> None:
    main, backup = _main(marked=False, n_sets=0), _backup(session=False)
    donor = save_bytes(backup)
    operations.restore_mod_progress(main, backup)
    assert main.current_map is None and main.session is None
    assert save_bytes(backup) == donor
    with pytest.raises(ValueError, match='Duplicate'):
        read_save(ModBackupSave, '<ModSaveData><LastArea_Safe/><LastArea_Safe/></ModSaveData>')


def test_recovery_edit_and_explicit_backup_refresh_are_separate() -> None:
    main, backup = _main(marked=False, n_sets=0), _backup()
    operations.restore_mod_progress(main, backup)
    before = save_bytes(backup)
    delta = operations.correct_map_progress(
        main, 'Backup/Map', LevelSide.A, time_played=Duration.from_dotnet_ticks(17), deaths=5
    )
    assert delta == (Duration.from_dotnet_ticks(12), 3)
    assert main.time_played == Duration.from_dotnet_ticks(112) and main.deaths == 23
    assert save_bytes(backup) == before
    assert main.session is not None
    main.session.room = 'goal'
    backup.install_mod_progress(deepcopy(main).mods)
    restored = read_save(ModBackupSave, save_bytes(backup))
    assert restored.mods.session is not None and restored.mods.session.room == 'goal'
    assert (
        require_map(restored.mods.campaigns['Backup'].levels['Backup/Map'].maps[LevelSide.A]).deaths
        == 5
    )


def test_current_and_optional_retained_queries_decode_only_requested_sessions() -> None:
    main = _main()
    xml = '<Session InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Other" Mode="BSide"/><OldStats SID="Other"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    collab = read_save(CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Other': xml}}))
    assert operations.is_in_progress(main, 'Pack0/Map', LevelSide.A)
    assert operations.is_in_progress(main, 'Other', LevelSide.B, collab=collab)
    assert not operations.is_in_progress(main, 'Other', LevelSide.A, collab=collab)
    assert not operations.is_in_progress(main, 'Other', LevelSide.B)
    invalid = read_save(CollabUtils2Save, 'SessionsPerLevel: {Missing: "<broken"}')
    with pytest.raises(ValueError, match='Missing'):
        operations.is_in_progress(main, 'Missing', LevelSide.A, collab=invalid)


def test_retained_session_identity_is_independent_of_in_level() -> None:
    main = _main()
    xml = '<Session InArea="false" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Other" Mode="BSide"/><OldStats SID="Other"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
    collab = read_save(CollabUtils2Save, yaml.safe_dump({'SessionsPerLevel': {'Other': xml}}))
    session = collab.sessions['Other'].session
    assert session.sid == 'Other' and session.side is LevelSide.B
    assert not session.in_level
    assert not operations.is_in_progress(main, 'Other', LevelSide.B, collab=collab)
    assert yaml.safe_load(save_bytes(collab))['SessionsPerLevel']['Other'] == session.tostring()


@pytest.mark.parametrize(
    ('size', 'side', 'null'),
    (
        (0, LevelSide.A, False),
        (1, LevelSide.B, False),
        (1, LevelSide.A, True),
        (2, LevelSide.B, True),
    ),
)
def test_correction_reports_missing_side_without_changing_progress(
    size: int, side: LevelSide, null: bool
) -> None:
    save = MainSave(time_played=Duration.from_seconds(2), deaths=10)
    level = LevelProgress('Pack/Map', [MapProgress(deaths=2) for _ in range(size)])
    if null:
        level.maps[side] = None
    save.mods.campaigns.add(CampaignProgress('Pack', [level]))
    baseline = deepcopy(save)
    with pytest.raises(BadProgressSave, match=f'Missing {side.name} progress for .*Pack/Map'):
        operations.correct_map_progress(
            save, level.sid, side, time_played=Duration.from_seconds(3), deaths=5
        )
    assert save == baseline


def test_correction_allows_an_existing_side_in_a_short_list() -> None:
    save = MainSave(time_played=Duration.from_seconds(2), deaths=10)
    progress = MapProgress(time_played=Duration.from_seconds(1), deaths=2)
    level = LevelProgress('Pack/Map', [progress])
    save.mods.campaigns.add(CampaignProgress('Pack', [level]))
    assert operations.correct_map_progress(
        save, level.sid, LevelSide.A, time_played=Duration.from_seconds(3), deaths=5
    ) == (Duration.from_seconds(2), 3)
    assert save.time_played == Duration.from_seconds(4) and save.deaths == 13
    assert progress.time_played == Duration.from_seconds(3) and progress.deaths == 5
    assert level.maps == [progress]


def test_correction_updates_only_requested_map_and_global_counters() -> None:
    main = _main()
    progress = require_map(main.flat_levels['Pack0/Map'].maps[LevelSide.A])
    other = deepcopy(require_map(main.flat_levels['Pack0/Map'].maps[LevelSide.B]))
    assert main.session is not None
    session = copy(main.session)
    bests = copy(progress.bests)
    result = operations.correct_map_progress(
        main, 'Pack0/Map', LevelSide.A, time_played=Duration.from_dotnet_ticks(34), deaths=5
    )
    assert result == (Duration.from_dotnet_ticks(24), 3)
    assert main.time_played == Duration.from_dotnet_ticks(124) and main.deaths == 23
    assert progress.time_played == Duration.from_dotnet_ticks(34) and progress.deaths == 5
    assert require_map(main.flat_levels['Pack0/Map'].maps[LevelSide.B]).deaths == other.deaths
    assert main.session.deaths == session.deaths and progress.bests == bests
    before = save_bytes(main)
    assert operations.correct_map_progress(main, 'Pack0/Map', LevelSide.A) == (Duration(), 0)
    assert operations.correct_map_progress(
        main, 'Pack0/Map', LevelSide.A, time_played=progress.time_played, deaths=progress.deaths
    ) == (Duration(), 0)
    assert save_bytes(main) == before


def test_noop_and_death_only_correction_do_not_require_unaffected_time_fields() -> None:
    main = read_save(
        MainSave,
        complete_vanilla(
            save_bytes(_main()).replace(b'<Time>100</Time>', b'').replace(b'TimePlayed="10"', b'')
        ),
    )
    assert operations.correct_map_progress(main, 'Pack0/Map', LevelSide.A) == (Duration(), 0)
    assert operations.correct_map_progress(main, 'Pack0/Map', LevelSide.A, deaths=4) == (
        Duration(),
        2,
    )
    assert (
        main.deaths == 22
        and require_map(main.flat_levels['Pack0/Map'].maps[LevelSide.A]).deaths == 4
    )
    with pytest.raises(KeyError):
        operations.correct_map_progress(main, 'pack0/Map', LevelSide.A, deaths=5)


@pytest.mark.parametrize('bad', (-1, True, 2**31))
def test_invalid_input_counts_are_rejected_before_any_change(bad: int) -> None:
    main = _main()
    before = save_bytes(main)
    with pytest.raises(ValueError):
        operations.correct_map_progress(
            main, 'Pack0/Map', LevelSide.A, time_played=Duration.from_dotnet_ticks(20), deaths=bad
        )
    assert save_bytes(main) == before


@pytest.mark.parametrize(
    'bad', (Duration.from_seconds(Fraction(1, 3)), Duration.from_dotnet_ticks(2**63))
)
def test_unwritable_time_is_rejected_before_other_counters_change(bad: Duration) -> None:
    main = _main()
    before = save_bytes(main)
    with pytest.raises(ValueError):
        operations.correct_map_progress(main, 'Pack0/Map', LevelSide.A, time_played=bad, deaths=4)
    assert save_bytes(main) == before


@pytest.mark.parametrize('failure', ('underflow', 'time_overflow', 'death_overflow'))
def test_invalid_global_delta_is_atomic(failure: str) -> None:
    main = _main()
    if failure == 'underflow':
        main.deaths = 0
    elif failure == 'time_overflow':
        main.time_played = Duration.from_dotnet_ticks(2**63 - 1)
    else:
        main.deaths = 2**31 - 1
    before = save_bytes(main)
    with pytest.raises(ValueError):
        operations.correct_map_progress(
            main,
            'Pack0/Map',
            LevelSide.A,
            time_played=Duration.from_dotnet_ticks(11),
            deaths=0 if failure == 'underflow' else 3,
        )
    assert save_bytes(main) == before
