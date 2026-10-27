"""Identity context and independently readable domain progress fields."""

from copy import copy
from io import BytesIO

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves import operations
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.progress import PersonalBests
from berries.game.vanilla import VANILLA_SIDS
from test_support.save_docs import complete_vanilla, get_map, read_save, require_map, save_bytes

_ZERO = '<AreaModeStats TimePlayed="0" Deaths="0"/>'


def _main(
    *,
    area: str = '<AreaStats ID="1">',
    session: str = '',
    complete: bool = False,
) -> MainSave:
    data = (
        f'<SaveData><Areas>{area}<Modes>'
        '<AreaModeStats TimePlayed="170001" Deaths="3"/>'
        '<AreaModeStats TimePlayed="340002" Deaths="5"/>'
        f'{_ZERO}</Modes></AreaStats></Areas>{session}</SaveData>'
    )
    return MainSave.read(
        BytesIO((complete_vanilla(data) if complete else data).encode()), strict=False
    )


def _session(*, area: str = '<Area ID="1" Mode="BSide"/>', tag: str = 'CurrentSession') -> str:
    root = etree.Element(
        tag, InArea='true', Level='room', Time='170001', Deaths='3', DeathsInCurrentLevel='2'
    )
    identity = etree.fromstring(area)
    root.append(identity)
    # Keep history independently identifiable even when the session identity is deliberately bad.
    try:
        level_id = int(identity.get('ID', '1'))
    except ValueError:
        level_id = 1
    sid = identity.get('SID') or VANILLA_SIDS[level_id if 0 <= level_id < len(VANILLA_SIDS) else 1]
    old = etree.SubElement(root, 'OldStats', SID=sid)
    modes = etree.SubElement(old, 'Modes')
    for _ in LevelSide:
        etree.SubElement(modes, 'AreaModeStats')
    return etree.tostring(root, encoding='unicode')


@pytest.mark.parametrize(
    ('level_id', 'sid'),
    tuple(
        enumerate(
            (
                'Celeste/0-Intro',
                'Celeste/1-ForsakenCity',
                'Celeste/2-OldSite',
                'Celeste/3-CelestialResort',
                'Celeste/4-GoldenRidge',
                'Celeste/5-MirrorTemple',
                'Celeste/6-Reflection',
                'Celeste/7-Summit',
                'Celeste/8-Epilogue',
                'Celeste/9-Core',
                'Celeste/LostLevels',
            )
        )
    ),
)
def test_original_area_indices_resolve_levels_and_sessions(level_id: int, sid: str) -> None:
    save = _main(
        area=f'<AreaStats ID="{level_id}">',
        session=_session(area=f'<Area ID="{level_id}" Mode="BSide"/>'),
        complete=True,
    )
    assert tuple(save.vanilla.levels) == VANILLA_SIDS
    assert save.current_map == (sid, LevelSide.B)
    assert b'SID=' not in save_bytes(save)


def test_personal_bests_do_not_require_accumulated_stats() -> None:
    data = (
        '<SaveData><Areas><AreaStats ID="1"><Modes>'
        '<AreaModeStats BestTime="170000" SingleRunCompleted="true" '
        'BestDeaths="0" BestDashes="3"/>'
        f'<AreaModeStats/>{_ZERO}</Modes></AreaStats></Areas></SaveData>'
    ).encode()
    stream = BytesIO(data)
    save = MainSave.read(stream, strict=False)
    progress = require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A])
    assert progress.bests == PersonalBests(Duration.from_dotnet_ticks(170000), Duration(), 0, 3)
    assert progress.time_played == Duration()
    assert (
        require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.B]).bests
        == PersonalBests()
    )
    assert stream.getvalue() == data


def test_duplicate_identity_is_rejected_at_load() -> None:
    with pytest.raises(BadProgressSave, match=r'Duplicate.*Celeste/1-ForsakenCity'):
        read_save(
            MainSave,
            '<SaveData><Areas>'
            + f'<AreaStats SID="Celeste/1-ForsakenCity"><Modes>{_ZERO * 3}</Modes></AreaStats>' * 2
            + '</Areas></SaveData>',
        )


def test_explicit_ids_read_all_sides_and_session_without_inserting_sids() -> None:
    save = _main(session=_session(), complete=True)
    before = save_bytes(save)
    assert list(save.flat_levels) == list(VANILLA_SIDS)
    assert all(len(level.maps) == 3 for level in save.flat_levels.values())
    progress = require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.B])
    assert progress.deaths == 5
    assert progress.time_played.to_dotnet_ticks() == 340002
    assert save.current_map == ('Celeste/1-ForsakenCity', LevelSide.B)
    session = save.session
    assert session is not None
    assert (session.sid, session.side, session.room, session.room_deaths) == (
        'Celeste/1-ForsakenCity',
        LevelSide.B,
        'room',
        2,
    )
    assert session.time_played.to_dotnet_ticks() == 170001
    assert operations.is_in_progress(save, 'Celeste/1-ForsakenCity', LevelSide.B)
    assert not operations.is_in_progress(save, 'Celeste/1-ForsakenCity', LevelSide.A)
    assert save_bytes(save) == before
    assert b'SID=' not in before


@pytest.mark.parametrize('level_id', (11, 999))
def test_unmapped_original_ids_do_not_acquire_invented_sids(level_id: int) -> None:
    save = _main(
        area=f'<AreaStats ID="{level_id}">',
    )
    assert not save.flat_levels
    assert len(save.vanilla.unresolved_levels) == 1
    assert save.vanilla.unresolved_levels[0].id == level_id
    assert save.current_map is None and save.session is None
    with pytest.raises(BadProgressSave):
        save_bytes(save)
    with pytest.raises(ValueError, match='session identity'):
        _main(session=_session(area=f'<Area ID="{level_id}"/>'))
    save = _main()
    assert get_map(save, 'Celeste/Missing', LevelSide.C) is None


def test_written_sids_win_over_numeric_identity_hints() -> None:
    save = _main(
        area='<AreaStats SID="Celeste/2-OldSite" ID="1">',
        session=_session(area='<Area SID="Celeste/2-OldSite" ID="1" Mode="CSide"/>'),
        complete=True,
    )
    before = save_bytes(save)
    assert require_map(save.flat_levels['Celeste/2-OldSite'].maps[LevelSide.A]).deaths == 3
    assert save.current_map == ('Celeste/2-OldSite', LevelSide.C)
    assert require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A]).deaths == 0
    assert save_bytes(save) == before


@pytest.mark.parametrize('campaign', ('Mod', 'Celeste'))
def test_id_mapping_never_applies_to_mod_areas_or_safe_sessions(campaign: str) -> None:
    source = (
        f'<LevelSets><LevelSetStats Name="{campaign}"><Areas><AreaStats ID="1"><Modes>'
        f'{_ZERO * 3}</Modes></AreaStats></Areas></LevelSetStats></LevelSets>'
    )
    main = MainSave.read(
        BytesIO(complete_vanilla(f'<SaveData>{source}</SaveData>').encode()), strict=False
    )
    backup = read_save(ModBackupSave, f'<ModSaveData>{source}</ModSaveData>')
    for save in (main, backup):
        if campaign == 'Celeste':
            # Everest's Celeste placeholder entry is retained, not exposed as a Mod campaign.
            assert save.mods.campaigns.get(campaign) is None
            value = save.mods.vanilla_entry
            assert value is not None
        else:
            assert list(save.mods.campaigns[campaign].levels) == []
            value = save.mods.campaigns[campaign]
        assert len(value.unresolved_levels) == 1
        assert value.unresolved_levels[0].id == 1
    with pytest.raises(ValueError, match='Area/SID'):
        MainSave.read(
            BytesIO(f'<SaveData>{_session(tag="CurrentSession_Safe")}</SaveData>'.encode()),
            strict=False,
        )


@pytest.mark.parametrize('level_id', ('', '-1', 'bad', '2147483648'))
def test_invalid_original_ids_raise_when_resolving_levels_or_sessions(level_id: str) -> None:
    with pytest.raises(ValueError):
        _main(area=f'<AreaStats ID="{level_id}">')
    with pytest.raises(ValueError):
        _main(session=_session(area=f'<Area ID="{level_id}"/>'))


@pytest.mark.parametrize(
    ('attribute', 'message'),
    (('', 'Missing original-game'), (' ID=""', 'decimal integer')),
)
def test_missing_original_id_and_empty_id_keep_distinct_errors(
    attribute: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _main(area=f'<AreaStats{attribute}>')
    with pytest.raises(ValueError, match=message):
        _main(session=_session(area=f'<Area{attribute}/>'))


def test_written_session_sid_defaults_side_only_when_mode_is_missing() -> None:
    save = _main(session=_session(area='<Area SID="Celeste/1-ForsakenCity"/>'))
    assert save.current_map == ('Celeste/1-ForsakenCity', LevelSide.A)


@pytest.mark.parametrize('tag', ('CurrentSession', 'CurrentSession_Safe'))
def test_written_session_sid_rejects_empty_mode(tag: str) -> None:
    with pytest.raises(ValueError, match='Mode'):
        _main(
            session=_session(area='<Area SID="Celeste/1-ForsakenCity" Mode=""/>', tag=tag),
        )


@pytest.mark.parametrize('tag', ('CurrentSession', 'CurrentSession_Safe'))
@pytest.mark.parametrize('attribute', (' InArea="false"', ' InArea="0"'))
def test_false_session_in_area_means_not_in_progress(tag: str, attribute: str) -> None:
    session = _session(area='<Area SID="Celeste/1-ForsakenCity"/>', tag=tag).replace(
        ' InArea="true"', attribute
    )
    save = _main(session=session)
    assert save.current_map is None
    value = save.session
    assert value is not None and value.sid == 'Celeste/1-ForsakenCity' and value.side is LevelSide.A
    assert not value.in_level


@pytest.mark.parametrize('tag', ('CurrentSession', 'CurrentSession_Safe'))
def test_missing_in_area_keeps_constructor_true_default(tag: str) -> None:
    session = _session(area='<Area SID="Celeste/1-ForsakenCity"/>', tag=tag).replace(
        ' InArea="true"', ''
    )
    save = _main(session=session)
    assert save.current_map == ('Celeste/1-ForsakenCity', LevelSide.A)
    value = save.session
    assert value is not None and value.in_level


@pytest.mark.parametrize('tag', ('CurrentSession', 'CurrentSession_Safe'))
@pytest.mark.parametrize('attribute', ['', 'null'])
def test_invalid_session_in_area_is_not_inactive(tag: str, attribute: str) -> None:
    session = _session(area='<Area SID="Celeste/1-ForsakenCity"/>', tag=tag).replace(
        'InArea="true"', f'InArea="{attribute}"'
    )
    with pytest.raises(ValueError, match='Expected an XML boolean'):
        _main(session=session)


@pytest.mark.parametrize('area', ('<Area/>', '<Area ID="1" Mode="Unknown"/>'))
def test_invalid_original_session_identity_is_not_treated_as_absent(area: str) -> None:
    with pytest.raises(ValueError):
        _main(session=_session(area=area))


def test_mapping_collisions_with_stored_sids_are_ambiguous() -> None:
    with pytest.raises(BadProgressSave, match=r'Duplicate.*Celeste/1-ForsakenCity'):
        MainSave.read(
            BytesIO(
                (
                    f'<SaveData><Areas><AreaStats ID="1"><Modes>{_ZERO * 3}</Modes></AreaStats>'
                    f'<AreaStats SID="Celeste/1-ForsakenCity"><Modes>{_ZERO * 3}</Modes></AreaStats>'
                    '</Areas></SaveData>'
                ).encode()
            ),
            strict=False,
        )


def _records(attrs: str, checkpoints: str = '') -> MainSave:
    return read_save(
        MainSave,
        complete_vanilla(
            '<SaveData><Areas><AreaStats SID="Celeste/1-ForsakenCity"><Modes>'
            f'<AreaModeStats TimePlayed="0" Deaths="0" {attrs}>{checkpoints}</AreaModeStats>'
            f'{_ZERO * 2}</Modes></AreaStats></Areas></SaveData>'
        ),
    )


@pytest.mark.parametrize('ticks', (0, -1, 170001))
@pytest.mark.parametrize('completed', (False, True))
def test_raw_records_are_preserved_independently_of_single_run_count_flags(
    ticks: int, completed: bool
) -> None:
    save = _records(
        f'BestTime="{ticks}" BestFullClearTime="{ticks}" BestDeaths="0" BestDashes="4" '
        f'SingleRunCompleted="{str(completed).lower()}"'
    )
    before = save_bytes(save)
    time = Duration.from_dotnet_ticks(ticks)
    assert require_map(
        save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A]
    ).bests == PersonalBests(time, time, 0, 4)
    assert save_bytes(save) == before


def test_missing_map_differs_from_present_map_without_established_records() -> None:
    save = _records('')
    assert (
        require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A]).bests
        == PersonalBests()
    )
    assert (
        require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A]).checkpoints
        == set()
    )
    assert get_map(save, 'Celeste/Missing', LevelSide.A) is None


def test_checkpoint_room_names_are_unordered_exact_stored_strings() -> None:
    save = _records(
        '',
        '<Checkpoints><string>Room</string><string>room</string>'
        '<string>Room</string><string>01</string><string/></Checkpoints>',
    )
    before = save_bytes(save)
    assert require_map(
        save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A]
    ).checkpoints == {
        'Room',
        'room',
        '01',
        '',
    }
    assert save_bytes(save) == before


@pytest.mark.parametrize(
    'attrs',
    (
        'BestTime="bad"',
        'BestFullClearTime="9223372036854775808"',
        'BestDeaths="-1"',
        'BestDashes="true"',
    ),
)
def test_invalid_known_best_fields_fail_during_loading(attrs: str) -> None:
    with pytest.raises(ValueError):
        _records(attrs)


def test_invalid_completion_flag_is_rejected_at_load() -> None:
    with pytest.raises(ValueError):
        _records('SingleRunCompleted="bad"')


def test_live_stats_edits_preserve_records_and_checkpoints_and_copy_is_detached() -> None:
    save = _records(
        'BestTime="170001" SingleRunCompleted="true"',
        '<Checkpoints><string>start</string></Checkpoints>',
    )
    progress = require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A])
    original = copy(progress)
    progress.deaths = 3
    assert require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A]) is progress
    assert progress.bests == original.bests
    assert progress.checkpoints == original.checkpoints == {'start'}
    assert original.deaths == 0


def test_backup_records_are_independent_of_accumulated_statistics() -> None:
    save = read_save(
        ModBackupSave,
        '<ModSaveData><LevelSets><LevelSetStats Name="Mod"><Areas>'
        '<AreaStats SID="Mod/Map"><Modes><AreaModeStats BestTime="170001">'
        '<Checkpoints><string>training</string></Checkpoints></AreaModeStats>'
        f'{_ZERO * 2}</Modes></AreaStats></Areas></LevelSetStats></LevelSets></ModSaveData>',
    )
    progress = require_map(save.mods.campaigns['Mod'].levels['Mod/Map'].maps[LevelSide.A])
    assert progress.bests == PersonalBests(Duration.from_dotnet_ticks(170001))
    assert progress.checkpoints == {'training'}
    assert progress.time_played == Duration()


def test_nil_checkpoint_values_and_duplicate_containers_are_invalid() -> None:
    for checkpoints in (
        (
            '<Checkpoints xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<string xsi:nil="true"/></Checkpoints>'
        ),
        '<Checkpoints/><Checkpoints/>',
    ):
        with pytest.raises(ValueError):
            _records('', checkpoints)


def test_original_mapping_applies_to_records_and_checkpoints_without_writing_sid() -> None:
    data = save_bytes(
        _records('BestTime="170001"', '<Checkpoints><string>room</string></Checkpoints>')
    ).replace(b'SID="Celeste/1-ForsakenCity" ', b'')
    save = MainSave.read(BytesIO(data), strict=False)
    progress = require_map(save.flat_levels['Celeste/1-ForsakenCity'].maps[LevelSide.A])
    assert progress.bests == PersonalBests(Duration.from_dotnet_ticks(170001))
    assert progress.checkpoints == {'room'}
    assert b'SID=' not in save_bytes(save)
