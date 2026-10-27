"""Original-game array indexing and legacy/Everest session identity protocols."""

from copy import deepcopy
from io import BytesIO

import pytest
from lxml import etree
from pydantic import ValidationError

from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.errors import BadProgressSave, UnsupportedProgressError
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress import CampaignProgress, LevelProgress, Session
from berries.game.vanilla import VANILLA_SIDS
from test_support.save_docs import parsed_session

_MODES = {LevelSide.A: 'Normal', LevelSide.B: 'BSide', LevelSide.C: 'CSide'}


def _required(root: etree.Element, path: str) -> etree.Element:
    node = root.find(path)
    assert node is not None, path
    return node


def test_numeric_identity_fallback_is_limited_to_vanilla_current_session() -> None:
    root = _legacy(1, LevelSide.A).to_xml()
    root.tag = 'CurrentSession'
    area = _required(root, 'Area')
    del area.attrib['SID']
    assert Session.from_xml(root).sid == VANILLA_SIDS[1]
    root.tag = 'EmbeddedSession'
    with pytest.raises(ValueError, match='Missing session Area/SID'):
        Session.from_xml(root)


@pytest.mark.parametrize('tag', ['CurrentSession', 'CurrentSession_Safe'])
@pytest.mark.parametrize('strict', [False, True])
def test_vanilla_old_stats_own_id_must_match_session_identity(tag: str, strict: bool) -> None:
    root = _legacy(1, LevelSide.A).to_xml()
    root.tag = tag
    old = _required(root, 'OldStats')
    del old.attrib['SID']
    old.set('ID', '2')
    with pytest.raises(ValidationError, match=r'OldStats SID.*differs from session SID'):
        Session.from_xml(root, strict=strict)


@pytest.mark.parametrize('strict', [False, True])
def test_vanilla_old_stats_unknown_id_is_not_replaced_with_session_identity(strict: bool) -> None:
    root = _legacy(1, LevelSide.A).to_xml()
    old = _required(root, 'OldStats')
    del old.attrib['SID']
    old.set('ID', '999')
    with pytest.raises(BadProgressSave, match='OldStats'):
        Session.from_xml(root, strict=strict)


@pytest.mark.parametrize('strict', [False, True])
def test_mod_old_stats_requires_its_own_sid_even_with_a_known_vanilla_id(strict: bool) -> None:
    root = parsed_session('Pack/Map', LevelSide.A).to_xml()
    root.tag = 'CurrentSession_Safe'
    old = _required(root, 'OldStats')
    del old.attrib['SID']
    old.set('ID', '1')
    with pytest.raises(ValidationError, match=r'OldStats SID.*differs from session SID'):
        Session.from_xml(root, strict=strict)


def _legacy(level_id: int, side: LevelSide) -> Session:
    return Session.from_xml(
        tree.parse(
            '<CurrentSession xmlns:e="urn:future" InArea="true" Level="room" Time="00170000" '
            'Deaths="2" DeathsInCurrentLevel="1">'
            f'<Area ID="{level_id}" Mode="{_MODES[side]}"/>'
            f'<OldStats ID="{level_id}"><Modes>'
            + '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            * 3
            + '</Modes></OldStats><Flags><string>keep</string></Flags>'
            '</CurrentSession>'
        ).getroot()
    )


def test_new_main_save_has_all_vanilla_chapters_and_numeric_ids() -> None:
    save = MainSave()
    assert tuple(save.vanilla.levels) == VANILLA_SIDS
    root = etree.fromstring(save.tostring())
    nodes = root.findall('Areas/AreaStats')
    assert [node.get('ID') for node in nodes] == [str(i) for i in range(len(VANILLA_SIDS))]
    assert all(len(node.findall('Modes/AreaModeStats')) == 3 for node in nodes)
    assert tuple(MainSave.fromstring(save.tostring()).vanilla.levels) == VANILLA_SIDS


@pytest.mark.parametrize('edit', ['delete', 'reorder', 'clear', 'unknown_sid'])
@pytest.mark.parametrize('strict', [False, True])
def test_malformed_vanilla_layout_cannot_overwrite_a_destination(edit: str, strict: bool) -> None:
    save = MainSave.fromstring(MainSave().tostring(), strict=strict)
    original = save.tostring()
    levels = save.vanilla.levels
    if edit == 'delete':
        del levels[VANILLA_SIDS[1]]
    elif edit == 'reorder':
        level = levels.pop(VANILLA_SIDS[1])
        levels.add(level)
    elif edit == 'clear':
        levels.clear()
    else:
        levels.add(LevelProgress('Celeste/NotAnOriginalChapter'))
    before = tuple(levels.items())
    output = BytesIO(b'original destination')
    with pytest.raises(BadProgressSave, match='VANILLA_SIDS order'):
        save.write(output)
    assert output.getvalue() == b'original destination'
    assert tuple(levels.items()) == before
    # Repair is explicit, not an implicit side effect of writing.
    save.install_campaigns((save, MainSave.fromstring(original).vanilla))
    assert save.tostring() == original


def test_new_vanilla_level_is_encoded_with_derived_id_without_mutating_it() -> None:
    save = MainSave()
    level = LevelProgress(VANILLA_SIDS[3])
    progress = level.maps[0]
    assert progress is not None
    progress.deaths = 7
    before = level.tostring()
    save.vanilla.levels.replace(level)
    node = etree.fromstring(save.tostring()).findall('Areas/AreaStats')[3]
    assert node.get('ID') == '3' and node.get('SID') is None
    assert _required(node, 'Modes/AreaModeStats').get('Deaths') == '7'
    assert level.tostring() == before


def test_complete_chapter_replacement_and_mod_collection_are_not_restricted() -> None:
    save = MainSave()
    save.install_campaigns(
        (save, CampaignProgress('Celeste', [LevelProgress(sid) for sid in VANILLA_SIDS]))
    )
    save.mods.campaigns.add(CampaignProgress('Pack', [LevelProgress('Pack/Map')]))
    node = etree.fromstring(save.tostring())
    assert node.findall('Areas/AreaStats')[1].get('ID') == '1'
    assert _required(node, 'LevelSets/LevelSetStats/Areas/AreaStats').get('ID') == '0'


def test_unresolved_vanilla_record_must_be_repaired_before_writing() -> None:
    original = MainSave().tostring()
    malformed = original.replace('</Areas>', '<AreaStats ID="999"/></Areas>')
    save = MainSave.fromstring(malformed, strict=False)
    assert tuple(save.vanilla.levels) == VANILLA_SIDS
    assert len(save.vanilla.unresolved_levels) == 1
    with pytest.raises(UnsupportedProgressError, match='Resolve'):
        save.tostring()


@pytest.mark.parametrize('side', list(LevelSide))
@pytest.mark.parametrize('level_id', range(len(VANILLA_SIDS)))
def test_transferred_numeric_session_has_safe_sid_and_vanilla_id(
    level_id: int, side: LevelSide
) -> None:
    source = _legacy(level_id, side)
    before = source.tostring()
    target = MainSave()
    target.session = source
    encoded = target.tostring()
    root = etree.fromstring(encoded)
    legacy = root.find('CurrentSession')
    safe = root.find('CurrentSession_Safe')
    assert legacy is not None and safe is not None
    assert _required(legacy, 'Area').get('ID') == str(level_id)
    assert _required(safe, 'Area').get('SID') == VANILLA_SIDS[level_id]
    assert list(_required(safe, 'Area').attrib)[-1] == 'SID'
    for node in (legacy, safe):
        assert _required(node, 'Area').get('Mode') == _MODES[side]
        assert node.findtext('Flags/string') == 'keep'
        assert node.find('OldStats') is not None
        assert node.get('Time') == '170000'
    restored = MainSave.fromstring(encoded)
    assert restored.current_map == (source.sid, side)
    assert source.tostring() == before


@pytest.mark.parametrize('side', list(LevelSide))
def test_new_sid_session_gets_numeric_id_only_when_it_is_a_known_original(side: LevelSide) -> None:
    for sid in (VANILLA_SIDS[1], 'Pack/Map', 'Celeste/NotAnOriginalChapter'):
        save = MainSave()
        save.session = parsed_session(sid, side, room='room')
        root = etree.fromstring(save.tostring())
        safe = root.find('CurrentSession_Safe/Area')
        assert safe is not None and safe.get('SID') == sid
        legacy = root.find('CurrentSession/Area')
        if sid in VANILLA_SIDS:
            assert legacy is not None and legacy.get('ID') == '1'
        else:
            assert legacy is None


def test_backup_and_snapshot_convert_numeric_session_without_mutating_source() -> None:
    session = _legacy(1, LevelSide.B)
    before = session.tostring()
    snapshot = ModSaveData(safe_session=session)
    backup = ModBackupSave()
    backup.install_mod_progress(snapshot)
    restored = ModBackupSave.fromstring(backup.tostring())
    assert restored.mods.current_map == (VANILLA_SIDS[1], LevelSide.B)
    target = MainSave()
    target.install_mod_progress(deepcopy(snapshot))
    assert MainSave.fromstring(target.tostring()).current_map == (VANILLA_SIDS[1], LevelSide.B)
    assert session.tostring() == before and session.level_id == 1


def test_legacy_only_edit_keeps_its_protocol_without_creating_safe_session() -> None:
    legacy = _legacy(2, LevelSide.C).to_xml()
    legacy.tag = 'CurrentSession'
    xml = (
        MainSave()
        .tostring()
        .replace('</SaveData>', etree.tostring(legacy, encoding='unicode') + '</SaveData>')
    )
    save = MainSave.fromstring(xml)
    session = save.session
    assert session is not None
    session.deaths += 1
    root = etree.fromstring(save.tostring())
    assert _required(root, 'CurrentSession/Area').get('ID') == '2'
    assert root.find('CurrentSession_Safe') is None
