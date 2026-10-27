"""Complete main/backup models, schema policies and fresh XML encoding."""

import pytest
from lxml import etree
from pydantic import ValidationError

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.codecs.yaml import nodes
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.save import Save
from berries.game.saves.progress.assists import Assists
from berries.game.saves.progress.map_ref import MapRef
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from test_support.save_docs import normal_save_xml

_MODS = """<LevelSets><LevelSetStats Name="Celeste"><Areas/><Poem/>
<UnlockedAreas>7</UnlockedAreas><TotalStrawberries>8</TotalStrawberries></LevelSetStats>
<LevelSetStats Name="Pack"><Areas><AreaStats ID="25" SID="Pack/Map"><Modes>
<AreaModeStats/><AreaModeStats/><AreaModeStats/>
</Modes></AreaStats></Areas></LevelSetStats></LevelSets>
<LevelSetRecycleBin/><LastArea_Safe ID="25" SID="Pack/Map" Mode="BSide"/>"""


def test_main_roundtrips_every_root_field_as_values() -> None:
    data = normal_save_xml("""<SaveData><Version>1.4.0.0</Version><Name>Player</Name>
    <Time>1190000</Time><LastSave>2026-10-22T12:34:56.1234567</LastSave>
    <CheatMode>true</CheatMode><AssistMode>false</AssistMode><VariantMode>true</VariantMode>
    <Assists><GameSpeed>8</GameSpeed><Invincible>true</Invincible><DashMode>Two</DashMode>
    <DashAssist>true</DashAssist><InfiniteStamina>true</InfiniteStamina><MirrorMode>true</MirrorMode>
    <ThreeSixtyDashing>true</ThreeSixtyDashing><InvisibleMotion>true</InvisibleMotion>
    <NoGrabbing>true</NoGrabbing><LowFriction>true</LowFriction><SuperDashing>true</SuperDashing>
    <Hiccups>true</Hiccups><PlayAsBadeline>true</PlayAsBadeline></Assists>
    <TheoSisterName>Alex</TheoSisterName><TotalDeaths>12</TotalDeaths>
    <TotalGoldenStrawberries>4</TotalGoldenStrawberries><TotalJumps>23</TotalJumps>
    <TotalWallJumps>45</TotalWallJumps><TotalDashes>67</TotalDashes>
    <Flags><string>flag</string></Flags><RevealedChapter9>true</RevealedChapter9>
    <LastArea ID="7" Mode="CSide"/></SaveData>""")
    save = MainSave.fromstring(data)
    assert save.version == '1.4.0.0' and save.name == 'Player'
    assert save.time_played == Duration.from_timer_frames(7)
    assert save.cheat_mode and not save.assist_mode and save.variant_mode and save.revealed_chapter9
    assert save.theo_sister_name == 'Alex' and save.flags == {'flag'}
    assert (save.total_jumps, save.total_wall_jumps, save.total_dashes) == (23, 45, 67)
    assert save.last_save_text == '2026-10-22T12:34:56.1234567'
    assert save.last_map_ref == MapRef(level_id=7, side=LevelSide.C)
    assert save.assists == Assists(
        game_speed=8,
        invincible=True,
        dash_mode='Two',
        dash_assist=True,
        infinite_stamina=True,
        mirror_mode=True,
        three_sixty_dashing=True,
        invisible_motion=True,
        no_grabbing=True,
        low_friction=True,
        super_dashing=True,
        hiccups=True,
        play_as_badeline=True,
    )
    restored = MainSave.fromstring(save.tostring())
    assert restored.tostring() == save.tostring()
    assert restored.last_save_text == save.last_save_text and restored.assists == save.assists


def test_backup_has_structured_vanilla_entry_and_raw_level_id() -> None:
    save = ModBackupSave.fromstring(normal_save_xml(f'<ModSaveData>{_MODS}</ModSaveData>'))
    assert 'Celeste' not in save.mods.campaigns
    assert save.mods.vanilla_entry is not None
    assert save.mods.vanilla_entry.total_strawberries == 8
    assert save.mods.partition_size() == 2
    assert save.mods.last_map_ref == MapRef(level_id=25, side=LevelSide.B, level_sid='Pack/Map')
    assert save.mods.last_map == ('Pack/Map', LevelSide.B)
    save.mods.last_map = ('Pack/Other', LevelSide.C)
    restored = ModBackupSave.fromstring(save.tostring())
    assert restored.mods.last_map_ref == MapRef(
        level_id=25, side=LevelSide.C, level_sid='Pack/Other'
    )
    assert restored.mods.vanilla_entry is not None
    assert restored.mods.vanilla_entry.unlocked_levels == 7


@pytest.mark.parametrize('save_type,root', [(MainSave, 'SaveData'), (ModBackupSave, 'ModSaveData')])
def test_root_unknown_policy_is_explicit(
    save_type: type[MainSave | ModBackupSave], root: str
) -> None:
    data = f'<{root}><Future/></{root}>'
    with pytest.raises(UnknownSaveFieldError) as error:
        save_type.fromstring(data, strict=False)
    assert error.value.path == f'{root}.Future'
    with pytest.warns(UnknownSaveFieldWarning):
        save = save_type.fromstring(data, strict=False, unknown='warn')
    if isinstance(save, MainSave):
        # Missing chapters are independent known-schema damage, not an unknown field.
        save.install_campaigns((save, MainSave().vanilla))
    assert 'Future' not in save.tostring()


def test_root_encoding_never_reparses_serialized_domain_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = MainSave()
    backup = ModBackupSave.fromstring(normal_save_xml(f'<ModSaveData>{_MODS}</ModSaveData>'))

    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail('Writing a structured model must not parse XML.')

    monkeypatch.setattr(tree, 'parse', fail)
    assert '<SaveData>' in main.tostring()
    assert '<ModSaveData>' in backup.tostring()


@pytest.mark.parametrize(
    'save_type',
    [
        MainSave,
        ModBackupSave,
        CollabUtils2Save,
        EndersBlenderSave,
        CommunalHelperSave,
    ],
)
def test_default_construction_does_not_decode_a_synthetic_template(
    save_type: type[Save],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail('An empty model must not parse synthetic XML or YAML.')

    monkeypatch.setattr(tree, 'parse', fail)
    monkeypatch.setattr(nodes, 'compose', fail)
    save = save_type()
    assert save.tostring()


def test_assists_omitted_and_explicit_empty_follow_struct_defaults() -> None:
    omitted = MainSave.fromstring(normal_save_xml('<SaveData/>'))
    empty = MainSave.fromstring(normal_save_xml('<SaveData><Assists/></SaveData>'))
    assert omitted.assists.game_speed == 10 and empty.assists.game_speed == 0


@pytest.mark.parametrize(
    'assist,variant', [(False, False), (True, False), (False, True), (True, True)]
)
def test_mode_flags_are_exclusive_only_during_strict_read(assist: bool, variant: bool) -> None:
    data = normal_save_xml(
        f'<SaveData><AssistMode>{str(assist).lower()}</AssistMode>'
        f'<VariantMode>{str(variant).lower()}</VariantMode></SaveData>'
    )
    if assist and variant:
        with pytest.raises(ValueError, match='AssistMode and VariantMode'):
            MainSave.fromstring(data)
    else:
        strict = MainSave.fromstring(data)
        assert (strict.assist_mode, strict.variant_mode) == (assist, variant)
    save = MainSave.fromstring(data, strict=False)
    restored = MainSave.fromstring(save.tostring(), strict=False)
    assert (restored.assist_mode, restored.variant_mode) == (assist, variant)
    if assist and variant:
        restored.assist_mode = False
        repaired = MainSave.fromstring(restored.tostring())
        assert not repaired.assist_mode and repaired.variant_mode


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('last_flag', ['assist_mode', 'variant_mode'])
def test_mode_assignment_and_writing_do_not_enforce_read_exclusivity(
    strict: bool, last_flag: str
) -> None:
    save = MainSave.fromstring(MainSave().tostring(), strict=strict)
    save.assist_mode = save.variant_mode = True
    setattr(save, last_flag, True)
    assert save.assist_mode and save.variant_mode
    root = etree.fromstring(save.tostring())
    assert root.findtext('AssistMode') == root.findtext('VariantMode') == 'true'


@pytest.mark.parametrize(
    'value',
    [
        '',
        '0001-01-01T00:00:00',
        '2026-10-22T12:34:56.1234567',
        '2026-10-22T12:34:56.0000001Z',
        '2026-10-22T12:34:56.9999999+08:00',
        '2026-10-22T12:34:56.12345675',
        ' 2026-10-22T12:34:56\n',
        'not a date & <data>',
    ],
)
@pytest.mark.parametrize('strict', [False, True])
def test_last_save_text_is_not_interpreted_or_normalized(value: str, strict: bool) -> None:
    root = tree.parse(MainSave().tostring()).getroot()
    node = root.find('LastSave')
    assert node is not None
    node.text = value
    save = MainSave.fromstring(etree.tostring(root), strict=strict)
    assert save.last_save_text == value
    save.deaths = 1
    restored = MainSave.fromstring(save.tostring(), strict=strict)
    assert restored.deaths == 1 and restored.last_save_text == value


def test_last_save_text_is_read_only() -> None:
    save = MainSave()
    with pytest.raises(ValidationError, match='Field is frozen'):
        save.last_save_text = '2026-10-23T00:00:00'
    with pytest.raises(ValidationError, match='Field is frozen'):
        del save.last_save_text
    assert save.last_save_text == '0001-01-01T00:00:00'


@pytest.mark.parametrize('strict', [False, True])
def test_absent_last_save_stays_absent_instead_of_using_constructor_default(strict: bool) -> None:
    save = MainSave.fromstring(normal_save_xml('<SaveData/>'), strict=strict)
    assert save.last_save_text is None
    assert tree.parse(save.tostring()).getroot().find('LastSave') is None
