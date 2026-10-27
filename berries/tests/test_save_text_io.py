"""Public save text APIs, default identities and stream encoding boundaries."""

from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
from typing import Never

import pytest

from berries.game.levels import LevelSide
from berries.game.saves.conflicts import CampaignConflictContext
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.save import Save
from berries.game.saves.slot import SaveSlot
from test_support.save_docs import normal_save_xml, require_map

_MODES = '<Modes>' + '<AreaModeStats TimePlayed="170000" Deaths="2"/>' * 3 + '</Modes>'
_VANILLA = normal_save_xml(
    f'<SaveData><Name>你好</Name><Areas><AreaStats ID="1">{_MODES}</AreaStats></Areas>'
    '<CurrentSession InArea="true" Level="room" Time="0" Deaths="0" DeathsInCurrentLevel="0">'
    '<Area ID="1" Mode="BSide"/><OldStats ID="1"><Modes><AreaModeStats/><AreaModeStats/><AreaModeStats/></Modes></OldStats></CurrentSession></SaveData>'
)


@pytest.mark.parametrize(
    ('save_type', 'source'),
    (
        (
            MainSave,
            normal_save_xml('<SaveData><Name>你好</Name><TotalDeaths>2</TotalDeaths></SaveData>'),
        ),
        (
            ModBackupSave,
            '<ModSaveData><LevelSets><LevelSetStats Name="你好"/></LevelSets></ModSaveData>',
        ),
        (CollabUtils2Save, 'SpeedBerryPBs: {Map: 170000}\nLearnedTech: {Collab: [你好]}\n'),
        (EndersBlenderSave, 'mapDict_roomStat_firstClear_roomOrder: {Map: []}\n'),
    ),
)
def test_all_formats_support_string_bytes_and_text_streams(
    tmp_path: Path,
    save_type: type[Save],
    source: str,
) -> None:
    save = save_type.fromstring(source)
    before = save.tostring()
    assert isinstance(before, str)
    assert save_type.fromstring(source.encode('utf-8')).tostring() == before
    assert save_type.read(StringIO(source)).tostring() == before
    assert save_type.read(BytesIO(source.encode('utf-8'))).tostring() == before

    text_output = StringIO()
    save.write(text_output)
    assert text_output.getvalue() == before and not text_output.closed
    binary_output = BytesIO()
    save.write(binary_output)
    assert binary_output.getvalue() == before.encode('utf-8') and not binary_output.closed
    path = tmp_path / 'save'
    save.write(path)
    assert save_type.read(path).tostring() == before


def test_default_vanilla_mapping_applies_to_every_main_entry_point(tmp_path: Path) -> None:
    path = tmp_path / '0.celeste'
    path.write_text(_VANILLA, encoding='utf-8')
    for save in (
        MainSave.fromstring(_VANILLA),
        MainSave.fromstring(_VANILLA.encode('utf-8')),
        MainSave.read(path),
        MainSave.read(StringIO(_VANILLA)),
        SaveSlot(tmp_path, 0).main(),
    ):
        sid = 'Celeste/1-ForsakenCity'
        assert require_map(save.vanilla.levels[sid].maps[LevelSide.A]).deaths == 2
        assert save.current_map == (sid, LevelSide.B)
        assert f'SID="{sid}"' not in save.tostring()


def test_utf16_input_and_text_output_do_not_carry_a_wrong_encoding_declaration() -> None:
    source = '<?xml version="1.0" encoding="utf-16"?>' + _VANILLA
    save = MainSave.fromstring(source.encode('utf-16'))
    assert save.name == '你好'
    assert MainSave.fromstring(source).name == '你好'
    assert not save.tostring().startswith('<?xml')
    binary = BytesIO()
    text = TextIOWrapper(binary, encoding='utf-16')
    try:
        save.write(text)
        text.flush()
        assert MainSave.fromstring(binary.getvalue()).name == '你好'
    finally:
        text.detach()


def test_user_can_compare_before_after_without_handling_xml() -> None:
    save = MainSave.fromstring(normal_save_xml('<SaveData><TotalDeaths>2</TotalDeaths></SaveData>'))
    before = save.tostring()
    save.deaths += 1
    after = save.tostring()
    assert save.tostring() == after
    assert before != after
    assert MainSave.fromstring(before).deaths == 2
    assert MainSave.fromstring(after).deaths == 3


def test_string_entry_points_preserve_read_options_and_diagnostics() -> None:
    malformed = (
        f'<SaveData><Areas><AreaStats SID="Other/Map">{_MODES}</AreaStats></Areas></SaveData>'
    )
    malformed = normal_save_xml(malformed, complete=False)
    with pytest.raises(ValueError, match='Other/Map'):
        MainSave.fromstring(malformed)
    assert (
        MainSave.fromstring(malformed, strict=False).vanilla.levels['Other/Map'].sid == 'Other/Map'
    )
    with pytest.raises(ValueError, match='Invalid XML') as raw_error:
        MainSave.fromstring('<broken')
    assert not getattr(raw_error.value, '__notes__', ())
    stream = StringIO('<broken')
    with pytest.raises(ValueError, match='Invalid XML') as file_error:
        MainSave.read(stream)
    assert file_error.value.__notes__ == [f'While reading {stream!r}.']


@pytest.mark.parametrize('save_type', (MainSave, ModBackupSave))
def test_fromstring_forwards_duplicate_policy_without_wrapping_callback_errors(
    save_type: type[MainSave | ModBackupSave],
) -> None:
    root = 'SaveData' if save_type is MainSave else 'ModSaveData'
    source = (
        f'<{root}><LevelSets>'
        '<LevelSetStats Name="Pack"/><LevelSetStats Name="Pack"/>'
        f'</LevelSets></{root}>'
    )
    error = ValueError('caller stopped')

    def stop(
        old: CampaignConflictContext,
        new: CampaignConflictContext,
    ) -> Never:
        raise error

    with pytest.raises(ValueError) as caught:
        save_type.fromstring(source, strict=False, on_campaign_conflict=stop)
    assert caught.value is error

    # Strict mode rejects duplicates without invoking the supplied callback.
    if save_type is MainSave:
        source = normal_save_xml(source)
    with pytest.raises(ValueError, match='Duplicate') as strict_error:
        save_type.fromstring(source, on_campaign_conflict=stop)
    assert strict_error.value is not error
