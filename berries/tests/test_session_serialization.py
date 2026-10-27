"""Canonical save diffs and explicit unknown-field handling, not lexical preservation."""

from io import BytesIO

import pytest
from lxml import etree

from berries.game.saves.codecs.xml import tree
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.progress.session import Session
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from test_support.save_docs import normal_save_xml, require_map


def test_session_output_is_canonical_for_both_unchanged_and_edited_values() -> None:
    source = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n<?before keep?><!--before-->'
        '<Session InArea="true" Level="room" Time="000170000" Deaths="02" '
        'DeathsInCurrentLevel="0">\r\n<Area SID="Pack/Map" Mode="Normal"/>\r\n'
        '<OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats>'
        '</Session><!--after--><?after keep?>'
    )
    original = etree.fromstring(source.encode()).getroottree()
    session = Session.from_xml(tree.parse(source).getroot())
    baseline = session.tostring()
    output = etree.fromstring(baseline)
    assert int(original.getroot().get('Time', '-1')) == int(output.get('Time', '-2')) == 170000
    assert int(original.getroot().get('Deaths', '-1')) == int(output.get('Deaths', '-2')) == 2
    assert output.getprevious() is None and output.getnext() is None
    assert session.tostring() == baseline and not any(session.iter_changed_fields())
    session.deaths = 3
    assert session.tostring() == baseline.replace('Deaths="2"', 'Deaths="3"')
    assert tuple(session.iter_changed_fields()) == ('deaths',)
    assert original.getroot().get('Deaths') == '02'


def test_respawn_edit_encodes_binary32_values_not_source_spelling() -> None:
    session = Session.from_xml(
        tree.parse(
            '<Session InArea="true" Level="r" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
            '<Area SID="Pack/Map" Mode="Normal"/>'
            '<RespawnPoint><X>8.000</X><Y>1.6E1</Y></RespawnPoint>'
            '<OldStats SID="Pack/Map"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></Session>'
        ).getroot()
    )
    session.respawn = (9, 16)
    root = etree.fromstring(session.tostring())
    assert root.findtext('RespawnPoint/X') == '9'
    assert root.findtext('RespawnPoint/Y') == '16'
    restored = Session.from_xml(root)
    assert restored.respawn == (9, 16)


@pytest.mark.parametrize('save_type', (MainSave, ModBackupSave))
def test_document_diff_uses_canonical_before_after_and_unknown_input_is_explicit(
    save_type: type[MainSave | ModBackupSave],
) -> None:
    tag = 'SaveData' if save_type is MainSave else 'ModSaveData'
    source = normal_save_xml(
        f'<{tag} xmlns:ext="urn:extension">'
        '<LevelSets><LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map">'
        '<Modes>'
        + '<AreaModeStats TimePlayed="170000" Deaths="2"/>'
        * 3
        + '</Modes><Unknown type="ext:Kind"/></AreaStats></Areas></LevelSetStats></LevelSets>'
        f'</{tag}>'
    )
    original = etree.fromstring(source)
    with pytest.raises(UnknownSaveFieldError):
        save_type.fromstring(source)
    with pytest.warns(UnknownSaveFieldWarning):
        document = save_type.read(BytesIO(source.encode()), unknown='warn')
    before = document.tostring()
    assert '<Unknown' not in before
    assert original.find('LevelSets/LevelSetStats/Areas/AreaStats/Unknown') is not None
    progress = require_map(document.mods.campaigns['Pack'].levels['Pack/Map'].maps[0])
    progress.deaths = 3
    after = document.tostring()
    before_root, after_root = etree.fromstring(before), etree.fromstring(after)
    path = 'LevelSets/LevelSetStats/Areas/AreaStats/Modes/AreaModeStats'
    first, second = before_root.find(path), after_root.find(path)
    assert first is not None and second is not None
    assert (first.get('Deaths'), second.get('Deaths')) == ('2', '3')
    assert first.get('TimePlayed') == second.get('TimePlayed') == '170000'
    assert document.tostring() == after
    assert save_type.fromstring(after).tostring() == after
