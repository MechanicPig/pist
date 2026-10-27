"""Complete domain XML entry points agree with file adapters and stage updates."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from lxml import etree
from pydantic import field_validator

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import session as session_codec
from berries.game.saves.codecs.xml.model import XmlModel
from berries.game.saves.conflicts import CampaignConflictContext
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.mod_save_data import ModSaveData
from berries.game.saves.progress.campaign import CampaignProgress
from berries.game.saves.progress.level import LevelProgress
from berries.game.saves.progress.members import CampaignMembers
from berries.game.saves.progress.session import Session
from test_support.save_docs import normal_save_xml, parsed_session


def test_domain_roots_can_be_embedded_under_different_names() -> None:
    root = _root()
    expected = MainSaveData.from_xml(root)
    root.tag = 'EmbeddedMain'
    assert MainSaveData.from_xml(root) == expected
    root = etree.fromstring('<EmbeddedBackup><LastArea_Safe SID="Pack/Map"/></EmbeddedBackup>')
    assert ModSaveData.from_xml(root).safe_last_map_ref is not None


@pytest.mark.parametrize('strict', [False, True])
def test_main_file_root_check_is_independent_of_read_strictness(strict: bool) -> None:
    with pytest.raises(ValueError, match='Expected SaveData XML root'):
        MainSave.fromstring('<ModSaveData/>', strict=strict)


def test_annotated_vanilla_session_preserves_nested_strict_policy() -> None:
    root = _root()
    node = root.find('CurrentSession')
    assert node is not None
    node.set('Time', '1')
    with pytest.raises(ValueError, match='17 ms'):
        MainSaveData.from_xml(root, strict=True)
    data = MainSaveData.from_xml(root, strict=False)
    assert isinstance(data.vanilla_session, XmlModel)
    assert isinstance(data.vanilla_session, Session)
    assert data.vanilla_session.time_played == Duration.from_dotnet_ticks(1)


def test_session_coordination_precedes_bound_output_validation() -> None:
    data = MainSaveData()
    data.session = parsed_session('Celeste/1-ForsakenCity', LevelSide.A, deaths=3)
    vanilla, safe = data.vanilla_session, data.safe_session
    assert vanilla is not None and safe is not None
    vanilla.deaths = -1
    safe.deaths = 4
    root = data.to_xml()
    current = root.find('CurrentSession')
    assert current is not None and current.get('Deaths') == '4'
    assert vanilla.deaths == -1 and safe.deaths == 4


def _root() -> etree.Element:
    root = etree.fromstring(
        normal_save_xml("""<SaveData>
    <Name>Player</Name><Version>1.4.0.0</Version><Time>170000</Time><TotalDeaths>8</TotalDeaths>
    <TotalGoldenStrawberries>2</TotalGoldenStrawberries><AssistMode>false</AssistMode>
    <VariantMode>true</VariantMode><CheatMode>true</CheatMode><RevealedChapter9>true</RevealedChapter9>
    <TotalJumps>3</TotalJumps><TotalWallJumps>4</TotalWallJumps><TotalDashes>5</TotalDashes>
    <TheoSisterName>Alex</TheoSisterName><LastSave>2026-10-22T12:34:56.1234567</LastSave>
    <Flags><string>flag</string></Flags><Assists><GameSpeed>8</GameSpeed></Assists>
    <LastArea ID="1" Mode="BSide"/><HasModdedSaveData>true</HasModdedSaveData>
    <UnlockedAreas>7</UnlockedAreas><TotalStrawberries>3</TotalStrawberries>
    <Poem><string>vanilla</string></Poem>
    <SummitGems><boolean>true</boolean><boolean>false</boolean><boolean>false</boolean>
    <boolean>false</boolean><boolean>false</boolean><boolean>false</boolean></SummitGems>
    <LevelSets><LevelSetStats Name="Celeste"><Areas/><Poem/>
    <UnlockedAreas>7</UnlockedAreas><TotalStrawberries>3</TotalStrawberries></LevelSetStats>
    <LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map"><Modes>
    <AreaModeStats Deaths="2"/><AreaModeStats/><AreaModeStats/>
    </Modes></AreaStats></Areas><Poem><string>pack</string></Poem></LevelSetStats></LevelSets>
    <LevelSetRecycleBin><LevelSetStats Name="Old"><Areas><AreaStats SID="Old/Map"><Modes>
    <AreaModeStats/><AreaModeStats/><AreaModeStats/>
    </Modes></AreaStats></Areas></LevelSetStats></LevelSetRecycleBin>
    <LastArea_Safe SID="Pack/Map" ID="25" Mode="BSide"/>
    </SaveData>""")
    )
    legacy = parsed_session('Celeste/1-ForsakenCity', LevelSide.A, deaths=3)
    safe = parsed_session('Pack/Map', LevelSide.B, deaths=4)
    root.append(session_codec.current_element(legacy, vanilla=True))
    root.append(session_codec.current_element(safe, vanilla=False))
    return root


def test_complete_data_and_file_entries_decode_every_declared_and_structured_value() -> None:
    root = _root()
    before = etree.tostring(root)
    data = MainSaveData.from_xml(root)
    save = MainSave.from_xml(root)
    for name in MainSaveData.model_fields:
        assert getattr(data, name) == getattr(save, name), name
    assert data.vanilla.snapshot() == save.vanilla.snapshot()
    assert dict(data.iter_comparable_items(model=ModSaveData)) == dict(
        save.iter_comparable_items(model=ModSaveData)
    )
    assert data.name == 'Player' and data.has_modded_save_data
    assert data.last_map == ('Celeste/1-ForsakenCity', LevelSide.B)
    assert data.current_map == ('Pack/Map', LevelSide.B)
    assert data.vanilla.total_strawberries == 3 and data.vanilla.poem == ['vanilla']
    assert data.mods.campaigns['Pack'].poem == ['pack']
    assert 'Old/Map' in data.recycled_flat_levels
    for value in (data, save):
        output = value.to_xml()
        assert output.find('CurrentSession/Area') is not None
        legacy = output.find('CurrentSession')
        assert legacy is not None and legacy.get('Deaths') == '3'
        assert output.find('CurrentSession_Safe') is not None
        assert MainSaveData.from_xml(output) == data
    assert etree.tostring(root) == before


@pytest.mark.parametrize('model', (MainSaveData, MainSave))
@pytest.mark.parametrize(
    'bad', ('<CurrentSession xsi:nil="true"/>', '<CurrentSession><Area/></CurrentSession>')
)
def test_late_decode_failure_preserves_input(model: type[MainSaveData], bad: str) -> None:
    root = etree.fromstring(
        normal_save_xml(
            f'<SaveData xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Name>changed</Name>{bad}</SaveData>'
        )
    )
    before = etree.tostring(root)
    with pytest.raises(ValueError):
        model.from_xml(root)
    assert etree.tostring(root) == before


def test_main_data_entry_enforces_strict_structure_and_allows_explicit_repair() -> None:
    with pytest.raises(BadProgressSave, match='Missing'):
        MainSaveData.from_xml(etree.Element('SaveData'))
    data = MainSaveData.from_xml(etree.Element('SaveData'), strict=False)
    assert not data.vanilla.levels
    with pytest.raises(BadProgressSave):
        data.to_xml()
    data.install_campaigns((data, MainSaveData().vanilla))
    assert MainSaveData.from_xml(data.to_xml()).vanilla.snapshot() == data.vanilla.snapshot()


def test_entry_totals_and_session_mirroring_do_not_edit_domain_values() -> None:
    root = _root()
    data = MainSaveData.from_xml(root)
    entry = data.mods.vanilla_entry
    assert entry is not None
    entry.total_strawberries = 999
    entry.unlocked_levels = 999
    data.session = parsed_session('Celeste/1-ForsakenCity', LevelSide.A, deaths=6)
    # Reparse to establish independent legacy/safe baselines, then edit only safe.
    data = MainSaveData.from_xml(data.to_xml())
    assert data.session is not None
    data.session.deaths = 9
    original = deepcopy(data)
    output = data.to_xml()
    assert output.findtext('LevelSets/LevelSetStats/TotalStrawberries') == '3'
    legacy = output.find('CurrentSession')
    safe = output.find('CurrentSession_Safe')
    assert legacy is not None and safe is not None
    assert legacy.get('Deaths') == safe.get('Deaths') == '9'
    assert data == original
    assert entry.total_strawberries == 999 and entry.unlocked_levels == 999


def test_mod_baseline_uses_base_model_scope_and_preserves_campaign_order() -> None:
    value = MainSave(
        campaigns=CampaignMembers(
            [
                CampaignProgress('One', [LevelProgress('One/Map')]),
                CampaignProgress('Two', [LevelProgress('Two/Map')]),
            ]
        )
    )
    value.name = 'Changed vanilla name'
    assert value.to_xml().findtext('HasModdedSaveData') == 'false'
    first = value.campaigns.pop('One')
    value.campaigns.add(first)
    assert value.to_xml().findtext('HasModdedSaveData') == 'true'


def test_file_baseline_is_established_from_decoded_progress() -> None:
    root = _root()
    marker = root.find('HasModdedSaveData')
    assert marker is not None
    marker.text = 'false'
    value = MainSave.from_xml(root)
    assert value.to_xml().findtext('HasModdedSaveData') == 'false'
    value.mods.campaigns.add(CampaignProgress('New'))
    assert value.to_xml().findtext('HasModdedSaveData') == 'true'
    assert not value.has_modded_save_data


def test_output_marker_uses_validated_boolean_and_detects_nested_edits() -> None:
    value = MainSave()
    value.has_modded_save_data = '0'  # pyright: ignore[reportAttributeAccessIssue]
    value.mods.campaigns.add(CampaignProgress('New'))
    assert value.to_xml().findtext('HasModdedSaveData') == 'true'
    assert value.to_xml(exclude_unset=True).findtext('HasModdedSaveData') == 'true'


@pytest.mark.parametrize('model', (MainSaveData, MainSave))
def test_conflict_callback_failure_propagates_without_changing_input(
    model: type[MainSaveData],
) -> None:
    root = etree.fromstring(
        normal_save_xml("""<SaveData><Name>changed</Name><LevelSets>
    <LevelSetStats Name="Pack"/><LevelSetStats Name="Pack"/>
    </LevelSets></SaveData>""")
    )
    before = etree.tostring(root)
    failure = RuntimeError('caller cancelled conflict resolution')

    def fail(old: CampaignConflictContext, new: CampaignConflictContext) -> CampaignProgress:
        assert old.progress.name == new.progress.name == 'Pack'
        raise failure

    with pytest.raises(RuntimeError) as caught:
        model.from_xml(root, strict=False, on_campaign_conflict=fail)
    assert caught.value is failure
    assert etree.tostring(root) == before


@pytest.mark.parametrize('model', (MainSaveData, MainSave))
def test_decode_does_not_construct_discarded_default_campaigns(model: type[MainSaveData]) -> None:
    root = etree.fromstring(normal_save_xml('<SaveData/>'))
    with (
        patch.object(
            CampaignProgress, '__init__', autospec=True, side_effect=CampaignProgress.__init__
        ) as initialize,
        patch.object(
            ModSaveData, '__init__', autospec=True, side_effect=ModSaveData.__init__
        ) as initialize_mods,
    ):
        value = model.from_xml(root)
    # Only the actual vanilla campaign; no blank root to overwrite afterward.
    assert initialize.call_count == 1
    assert initialize_mods.call_count == 0
    assert len(value.flat_levels) == len(value.vanilla.levels) == 11


def test_main_field_validators_run_once_during_decoding() -> None:
    class Named(MainSaveData):
        @field_validator('name')
        @classmethod
        def suffix(cls, value: str) -> str:
            return value + '!'

    value = Named.from_xml(
        etree.fromstring(normal_save_xml('<SaveData><Name>Player</Name></SaveData>'))
    )
    assert value.name == 'Player!'
