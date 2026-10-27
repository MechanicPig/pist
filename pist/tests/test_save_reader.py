"""Application save policy composes independent files and keeps deferred failures visible."""

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress import CampaignProgress, LevelProgress
from pist.saves import SaveProgressReader
from test_support.save_docs import normal_save_xml


def test_browse_uses_active_history_before_same_sid_recycled_history(tmp_path: Path) -> None:
    main = MainSave()
    for group, deaths in ((main.mods.campaigns, 2), (main.mods.recycled_campaigns, 9)):
        level = LevelProgress('Pack/Map')
        progress = level.maps[0]
        assert progress is not None
        progress.deaths = deaths
        group.add(CampaignProgress('Pack', [level]))
    main.write(tmp_path / '0.celeste')
    stats = SaveProgressReader(tmp_path).load(0).get_map_stats('Pack/Map', LevelSide.A)
    assert stats is not None and stats.deaths == 2


def test_directory_io_failure_reaches_application_error_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path: Path) -> Iterator[Path]:
        raise PermissionError('Denied')

    monkeypatch.setattr(Path, 'iterdir', denied)
    with pytest.raises(ValueError, match='无法枚举') as caught:
        SaveProgressReader(tmp_path).available_numbers()
    assert isinstance(caught.value.__cause__, PermissionError)


@pytest.mark.parametrize('first_clear', (False, True))
def test_file_io_failure_reaches_application_error_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_clear: bool
) -> None:
    def denied(file: str | Path, mode: str) -> BytesIO:
        raise PermissionError('Denied')

    monkeypatch.setattr('berries.text_doc.open', denied, raising=False)
    reader = SaveProgressReader(tmp_path)
    load = reader.load_first_clear if first_clear else reader.load
    with pytest.raises(ValueError, match='无法读取') as caught:
        load(0)
    assert isinstance(caught.value.__cause__, PermissionError)


def _area(sid: str, deaths: int) -> str:
    return (
        f'<AreaStats SID="{sid}"><Modes><AreaModeStats TimePlayed="170000" Deaths="{deaths}"/>'
        '<AreaModeStats TimePlayed="0" Deaths="0"/>'
        '<AreaModeStats TimePlayed="0" Deaths="0"/></Modes></AreaStats>'
    )


def _session(sid: str, mode: str = 'Normal') -> str:
    return f'<Session InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="{sid}" Mode="{mode}"/><OldStats SID="{sid}"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'


def _files(tmp_path: Path, sessions: str = '') -> Path:
    (tmp_path / '0.celeste').write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    path = tmp_path / '0-modsave-CollabUtils2.celeste'
    path.write_text(sessions or 'SessionsPerLevel: {}', encoding='utf-8')
    return path


@pytest.mark.parametrize('marked', (True, False))
def test_reading_explicitly_restores_a_whole_backup_never_individual_missing_maps(
    tmp_path: Path, marked: bool
) -> None:
    main = tmp_path / '0.celeste'
    main.write_text(
        normal_save_xml(
            f'<SaveData><HasModdedSaveData>{str(marked).lower()}</HasModdedSaveData>'
            f'<LevelSets><LevelSetStats><Areas>{_area("Map", 1)}</Areas></LevelSetStats></LevelSets>'
            '</SaveData>'
        ),
        encoding='utf-8',
    )
    backup = tmp_path / '0-modsavedata.celeste'
    backup.write_text(
        normal_save_xml(
            f'<ModSaveData><LevelSets><LevelSetStats><Areas>{_area("Map", 99)}'
            f'{_area("OnlyBackup", 4)}</Areas></LevelSetStats></LevelSets>'
            '<CurrentSession_Safe InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Map" Mode="BSide"/>'
            '<OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession_Safe></ModSaveData>'
        ),
        encoding='utf-8',
    )
    before = main.read_bytes(), backup.read_bytes()
    saved = SaveProgressReader(tmp_path).load(0)
    stats = saved.get_map_stats('Map', LevelSide.A)
    assert stats is not None and stats.deaths == (1 if marked else 99)
    assert (saved.get_map_stats('OnlyBackup', LevelSide.A) is not None) is not marked
    assert saved.current_map == (None if marked else ('Map', LevelSide.B))
    assert (main.read_bytes(), backup.read_bytes()) == before


def test_ordinary_slot_discovery_requires_main_and_excludes_debug(tmp_path: Path) -> None:
    for name in ('0.celeste', 'debug.celeste', '2-modsavedata.celeste'):
        (tmp_path / name).write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    reader = SaveProgressReader(tmp_path)
    assert reader.available_numbers() == [0]
    for number in (2, 99):
        with pytest.raises(ValueError, match=rf'{number}\.celeste'):
            SaveProgressReader(tmp_path).load(number)
    with pytest.raises(ValueError, match='ordinary'):
        SaveProgressReader(tmp_path).load(-1)
    (tmp_path / '8.celeste').write_bytes(b'corrupt but occupied')
    assert reader.available_numbers() == [0, 8]


def test_inactive_retained_session_does_not_count_as_in_progress(tmp_path: Path) -> None:
    path = _files(
        tmp_path,
        'SessionsPerLevel:\n  Map: <Session InArea="false" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Map"/><OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>\n',
    )
    saved = SaveProgressReader(tmp_path).load(0)
    path.unlink()
    errors: list[ValueError] = []
    for side in LevelSide:
        assert not saved.is_in_progress('Map', side, on_error=errors.append)
    assert not errors


def test_collab_session_xml_is_lazy_cached_and_does_not_reread_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _files(
        tmp_path, f'SessionsPerLevel:\n  Good: {_session("Good", "BSide")}\n  Bad: <broken\n'
    )
    saved = SaveProgressReader(tmp_path).load(0)
    path.unlink()
    parsed: list[str] = []
    original = tree.parse

    def parse(data: bytes | str) -> etree.ElementTree:
        parsed.append(str(data))
        return original(data)

    monkeypatch.setattr(tree, 'parse', parse)
    assert not saved.is_in_progress('Missing', LevelSide.A)
    assert not parsed
    assert saved.is_in_progress('Good', LevelSide.B)
    assert not saved.is_in_progress('Good', LevelSide.A)
    assert parsed and len(parsed) <= 2
    assert all(data == _session('Good', 'BSide') for data in parsed)
    after_good = len(parsed)
    errors: list[ValueError] = []
    assert not saved.is_in_progress('Bad', LevelSide.A, on_error=errors.append)
    assert len(errors) == 1 and 'Bad' in str(errors[0]) and str(path) in str(errors[0])
    assert errors[0].__cause__ is not None
    assert not saved.is_in_progress('Bad', LevelSide.B, on_error=errors.append)
    assert len(errors) == 1 and len(parsed) == after_good + 1


@pytest.mark.parametrize(
    'xml',
    (
        '<Session InArea="true"/>',
        '<Session InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Bad" Mode="Unknown"/><OldStats SID="Bad"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>',
    ),
)
def test_invalid_retained_identity_is_reported_only_when_queried(tmp_path: Path, xml: str) -> None:
    _files(tmp_path, f'SessionsPerLevel:\n  Bad: {xml}\n  Good: {_session("Good")}\n')
    saved = SaveProgressReader(tmp_path).load(0)
    assert saved.is_in_progress('Good', LevelSide.A)
    with pytest.raises(ValueError, match='Bad'):
        saved.is_in_progress('Bad', LevelSide.A)


def test_native_match_skips_bad_retained_session_and_reload_resets_failed_cache(
    tmp_path: Path,
) -> None:
    path = _files(tmp_path, 'SessionsPerLevel:\n  Map: <broken\n')
    (tmp_path / '0.celeste').write_text(
        normal_save_xml(
            '<SaveData><CurrentSession InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area SID="Map" Mode="Normal"/>'
            '<OldStats SID="Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession></SaveData>'
        ),
        encoding='utf-8',
    )
    reader = SaveProgressReader(tmp_path)
    saved = reader.load(0)
    assert saved.is_in_progress('Map', LevelSide.A)
    errors: list[ValueError] = []
    assert not saved.is_in_progress('Map', LevelSide.B, on_error=errors.append)
    assert len(errors) == 1
    path.write_text(f'SessionsPerLevel:\n  Map: {_session("Map", "BSide")}\n', encoding='utf-8')
    assert reader.load(0).is_in_progress('Map', LevelSide.B)
    assert not saved.is_in_progress('Map', LevelSide.B, on_error=errors.append)
    assert len(errors) == 1


def test_raising_error_callback_does_not_opt_into_skipping_the_bad_session(tmp_path: Path) -> None:
    _files(tmp_path, 'SessionsPerLevel:\n  Map: <broken\n')
    saved = SaveProgressReader(tmp_path).load(0)

    def fail(error: ValueError) -> None:
        raise RuntimeError('Stop') from error

    with pytest.raises(RuntimeError, match='Stop'):
        saved.is_in_progress('Map', LevelSide.A, on_error=fail)
    with pytest.raises(ValueError, match='Map'):
        saved.is_in_progress('Map', LevelSide.A)


def test_scalar_like_retained_sids_stay_text(tmp_path: Path) -> None:
    sessions = '\n'.join(f'  {sid}: {_session(sid)}' for sid in ('01', 'true', '2026-10-16'))
    _files(tmp_path, f'SessionsPerLevel:\n{sessions}\n')
    saved = SaveProgressReader(tmp_path).load(0)
    for sid in ('01', 'true', '2026-10-16'):
        assert saved.is_in_progress(sid, LevelSide.A)


def test_invalid_collab_document_remains_a_source_qualified_load_error(tmp_path: Path) -> None:
    path = _files(tmp_path, 'SessionsPerLevel: null\n')
    with pytest.raises(ValueError) as caught:
        SaveProgressReader(tmp_path).load(0)
    assert path.name in str(caught.value)


def test_browsing_statistics_are_indexed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / '0.celeste').write_text(
        normal_save_xml(
            f'<SaveData><Areas>{_area("Celeste/1-ForsakenCity", 2)}</Areas></SaveData>'
        ),
        encoding='utf-8',
    )
    saved = SaveProgressReader(tmp_path).load(0)

    def fail(data: bytes | str) -> etree.ElementTree:
        pytest.fail('Queries must not scan XML after snapshot construction.')

    monkeypatch.setattr(tree, 'parse', fail)
    for _ in range(3):
        stats = saved.get_map_stats('Celeste/1-ForsakenCity', LevelSide.A)
        assert stats is not None and stats.deaths == 2


def test_first_clear_file_is_optional_and_read_only_when_requested(tmp_path: Path) -> None:
    (tmp_path / '0.celeste').write_text(normal_save_xml('<SaveData/>'), encoding='utf-8')
    reader = SaveProgressReader(tmp_path)
    assert reader.load_first_clear(0) is None
    assert reader.load_first_clear(99) is None
    path = tmp_path / '0-modsave-EndersBlender.celeste'
    path.write_text('[invalid', encoding='utf-8')
    assert all(stats.time_played == Duration() for stats in reader.load(0).map_stats.values())
    with pytest.raises(ValueError, match='EndersBlender'):
        reader.load_first_clear(0)


def test_browsing_pure_vanilla_progress_uses_explicit_original_identity_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / '0.celeste'
    path.write_text(
        normal_save_xml(
            '<SaveData><Areas><AreaStats ID="9"><Modes>'
            '<AreaModeStats TimePlayed="170000" Deaths="4"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '</Modes></AreaStats></Areas>'
            '<CurrentSession InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0"><Area ID="9" Mode="Normal"/><OldStats ID="9"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession>'
            '</SaveData>'
        ),
        encoding='utf-8',
    )
    before = path.read_bytes()
    saved = SaveProgressReader(tmp_path).load(0)
    stats = saved.get_map_stats('Celeste/9-Core', LevelSide.A)
    assert stats is not None and stats.deaths == 4
    assert saved.is_in_progress('Celeste/9-Core', LevelSide.A)
    assert path.read_bytes() == before
