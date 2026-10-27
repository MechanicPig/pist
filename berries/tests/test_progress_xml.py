"""Pure progress codec contracts before connecting them to file lifecycles."""

from copy import copy, deepcopy
from fractions import Fraction

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree
from berries.game.saves.errors import (
    BadProgressSave,
    UnresolvedLevelIdentityError,
    UnsupportedProgressError,
)
from berries.game.saves.partition import ProgressPartition
from berries.game.saves.progress import CampaignProgress, LevelProgress, MapProgress, PersonalBests
from berries.map_entity_id import MapEntityID
from test_support.save_docs import normal_save_xml, require_map

_CAMPAIGN = b"""<LevelSetStats xmlns:e="urn:source" Name="Pack">
<UnlockedAreas>02</UnlockedAreas><TotalStrawberries>5</TotalStrawberries>
<Poem><string>z</string><string>a</string><string>z</string></Poem>
<Areas>before<!--before--><AreaStats SID="Pack/Map" ID="17" Cassette="true">
<Modes>text<AreaModeStats TimePlayed="00170000" Deaths="02" TotalStrawberries="5"
SingleRunCompleted="true" BestTime="170000" BestDeaths="0" BestDashes="0">
<Strawberries><EntityID Key="room:1"/>tail</Strawberries>
<Checkpoints><string>cp</string></Checkpoints>
</AreaModeStats><!--between--><AreaModeStats TimePlayed="-1" Deaths="0" BestTime="-1" BestDashes="7"/>
<AreaModeStats TimePlayed="0" Deaths="0"/>
</Modes></AreaStats>after<?keep marker?></Areas></LevelSetStats>"""


def _decode(data: bytes = _CAMPAIGN) -> CampaignProgress:
    return CampaignProgress.from_xml(tree.parse(data).getroot(), strict=False)


@pytest.mark.parametrize('name', ('Pack', 'Other/Nested', '', None))
def test_campaign_name_comes_from_xml(name: str | None) -> None:
    root = etree.Element('LevelSetStats')
    if name is not None:
        root.set('Name', name)
    campaign = CampaignProgress.from_xml(root, strict=False)
    assert campaign.name == ('' if name is None else name)


@pytest.mark.parametrize('strict', (False, True))
def test_main_campaign_fields_match_level_set_decode_without_changing_tree(strict: bool) -> None:
    fields = (
        '<UnlockedAreas>7</UnlockedAreas><TotalStrawberries>2</TotalStrawberries>'
        '<Poem><string>poem</string></Poem><Areas><AreaStats ID="1"><Modes>'
        '<AreaModeStats/><AreaModeStats/><AreaModeStats/>'
        '</Modes></AreaStats></Areas>'
    )
    root = etree.fromstring(
        normal_save_xml(
            f'<SaveData><Name>Player</Name>{fields}<TotalDeaths>9</TotalDeaths></SaveData>',
            complete=strict,
        )
    )
    before = etree.tostring(root)
    areas = root.find('Areas')
    campaign = CampaignProgress.from_main_xml(root, strict=strict)
    wrapped_root = etree.Element('LevelSetStats', Name='Celeste')
    for child in root:
        if child.tag in ('Areas', 'Poem', 'UnlockedAreas', 'TotalStrawberries'):
            wrapped_root.append(deepcopy(child))
    wrapped = CampaignProgress.from_xml(
        wrapped_root,
        strict=strict,
        partition=ProgressPartition.VANILLA,
    )
    assert campaign == wrapped
    assert campaign.name == 'Celeste'
    assert 'Celeste/1-ForsakenCity' in campaign.levels
    assert root.find('Areas') is areas
    assert areas is not None and areas.getparent() is root
    assert etree.tostring(root) == before


def test_main_campaign_fields_validate_nested_unknowns_at_original_path() -> None:
    from berries.game.saves.unknown import UnknownSaveFieldError

    root = etree.fromstring(b'<SaveData><Areas><Future/></Areas></SaveData>')
    with pytest.raises(UnknownSaveFieldError) as error:
        CampaignProgress.from_main_xml(root)
    assert error.value.path == 'SaveData.Areas.Future'


def test_campaign_values_are_authoritative_and_canonical_output_preserves_values() -> None:
    campaign = _decode()
    assert campaign.poem == ['z', 'a', 'z']
    assert campaign.unlocked_levels == 2 and campaign.total_strawberries == 5
    level = campaign.levels['Pack/Map']
    assert level.cassette and len(level.maps) == 3
    a_side, b_side, _ = level.maps
    assert a_side is not None and b_side is not None
    assert a_side.bests == PersonalBests(Duration.from_timer_frames(1), Duration(), 0, 0)
    assert b_side.bests == PersonalBests(Duration.from_dotnet_ticks(-1), Duration(), 0, 7)
    assert b_side.time_played == Duration.from_dotnet_ticks(-1)
    encoded = campaign.tostring()
    assert b'berries-' not in encoded
    assert b'TimePlayed="170000"' in encoded and b'Deaths="2"' in encoded
    assert b'BestTime="-1"' in encoded and b'BestDashes="7"' in encoded
    assert b'<UnlockedAreas>2</UnlockedAreas>' in encoded
    assert b'<!--before-->' not in encoded and b'<?keep marker?>' not in encoded
    node = tree.parse(encoded).getroot().find('Areas/AreaStats')
    assert node is not None and node.get('ID') == '17'
    restored = _decode(encoded)
    assert restored.poem == campaign.poem
    assert require_map(restored.levels['Pack/Map'].maps[LevelSide.A]).deaths == 2


def test_edit_fields_sets_bests_and_stored_count_offset() -> None:
    campaign = _decode()
    progress = require_map(campaign.levels['Pack/Map'].maps[LevelSide.A])
    progress.deaths = 3
    progress.time_played = Duration.from_seconds(2)
    progress.heart = True
    progress.completed = True
    progress.bests.time = Duration.from_seconds(1)
    progress.strawberries.add(MapEntityID('room', 2))
    progress.checkpoints.add('new')
    campaign.levels['Pack/Map'].cassette = False
    campaign.total_strawberries = 6
    encoded = campaign.tostring()
    root = tree.parse(encoded).getroot()
    assert root.findtext('TotalStrawberries') == '6'
    assert 'TotalStrawberries' not in root.attrib
    mode = root.find('Areas/AreaStats/Modes/AreaModeStats')
    assert mode is not None and mode.get('TotalStrawberries') == '6'
    entity = mode.find('Strawberries/EntityID')
    assert entity is not None and entity.get('Key') == 'room:1'
    restored = _decode(encoded)
    result = require_map(restored.levels['Pack/Map'].maps[LevelSide.A])
    assert result.deaths == 3 and result.time_played == Duration.from_seconds(2)
    assert result.heart and result.completed and not restored.levels['Pack/Map'].cassette
    assert result.bests.time == Duration.from_seconds(1)
    assert result.strawberries == progress.strawberries and result.checkpoints == {'cp', 'new'}
    assert restored.total_strawberries == 6


def test_clear_retains_level_structure_and_counter_offset() -> None:
    campaign = _decode()
    campaign.reset()
    encoded = campaign.tostring()
    restored = _decode(encoded)
    assert len(restored.levels) == 1 and len(restored.levels['Pack/Map'].maps) == 3
    assert restored.poem == [] and restored.total_strawberries == 0
    progress = require_map(restored.levels['Pack/Map'].maps[LevelSide.A])
    assert progress.time_played == Duration() and progress.deaths == 0
    assert not progress.strawberries and progress.bests == PersonalBests()
    root = tree.parse(encoded).getroot()
    node = root.find('Areas/AreaStats/Modes/AreaModeStats')
    assert node is not None and node.get('TotalStrawberries') == '4'
    area = root.find('Areas/AreaStats')
    assert area is not None and area.get('ID') == '17'


def test_import_copied_level_keeps_independent_values_and_numeric_identity() -> None:
    source = _decode()
    target = CampaignProgress('Pack', [deepcopy(source.levels['Pack/Map'])])
    require_map(target.levels['Pack/Map'].maps[LevelSide.A]).deaths += 1
    require_map(source.levels['Pack/Map'].maps[LevelSide.A]).deaths = 0
    encoded = target.tostring()
    root = tree.parse(encoded).getroot()
    node = root.find('Areas/AreaStats')
    assert node is not None and node.get('ID') == '17'
    assert require_map(_decode(encoded).levels['Pack/Map'].maps[LevelSide.A]).deaths == 3


def test_collection_removal_and_insertion_do_not_leak_old_members_or_placeholders() -> None:
    campaign = _decode()
    previous = campaign.levels['Pack/Map']
    del campaign.levels[previous.sid]
    campaign.levels.add(LevelProgress('Pack/Other'))
    encoded = campaign.tostring()
    restored = _decode(encoded)
    assert [level.sid for level in restored.levels.values()] == ['Pack/Other']
    assert b'berries-' not in encoded
    assert b'Pack/Map' not in encoded
    assert previous.sid == 'Pack/Map' and require_map(previous.maps[LevelSide.A]).deaths == 2


def test_original_game_mapping_exposes_unresolved_values_for_explicit_repair() -> None:
    modes = b'<AreaModeStats TimePlayed="1" Deaths="0"/>' * 3
    data = (
        b'<LevelSetStats Name="Celeste"><Areas><AreaStats ID="0"><Modes>'
        + modes
        + b'</Modes></AreaStats><AreaStats ID="999"/></Areas></LevelSetStats>'
    )
    campaign = CampaignProgress.from_xml(
        tree.parse(data).getroot(),
        strict=False,
        partition=ProgressPartition.VANILLA,
    )
    assert [level.sid for level in campaign.levels.values()] == ['Celeste/0-Intro']
    require_map(campaign.levels['Celeste/0-Intro'].maps[LevelSide.A]).deaths = 2
    assert len(campaign.unresolved_levels) == 1
    unresolved = campaign.unresolved_levels[0]
    assert unresolved.id == 999 and len(unresolved.maps) == 3
    with pytest.raises(UnsupportedProgressError):
        campaign.tostring()
    repaired = unresolved.resolve('Celeste/LostLevels')
    repaired.id = 10
    campaign.levels.add(repaired)
    campaign.unresolved_levels.remove(unresolved)
    encoded = campaign.tostring()
    root = tree.parse(encoded).getroot()
    areas = root.findall('Areas/AreaStats')
    assert [area.get('ID') for area in areas] == ['0', '10']
    assert [area.get('SID') for area in areas] == ['Celeste/0-Intro', 'Celeste/LostLevels']


@pytest.mark.parametrize(
    ('attributes', 'vanilla', 'expected'),
    [
        ('SID="Pack/Map" ID="999"', False, 'Pack/Map'),
        ('SID="Pack/Map" ID="999"', True, 'Pack/Map'),
        ('ID="1"', True, 'Celeste/1-ForsakenCity'),
    ],
)
def test_level_resolves_its_own_identity(attributes: str, vanilla: bool, expected: str) -> None:
    root = etree.fromstring(f'<AreaStats {attributes}/>')
    assert LevelProgress.from_xml(root, vanilla=vanilla, strict=False).sid == expected


@pytest.mark.parametrize(('attributes', 'vanilla'), [('ID="1"', False), ('ID="999"', True)])
def test_unresolved_level_identity_has_a_distinct_failure(attributes: str, vanilla: bool) -> None:
    root = etree.fromstring(f'<AreaStats {attributes}/>')
    with pytest.raises(UnresolvedLevelIdentityError):
        LevelProgress.from_xml(root, vanilla=vanilla, strict=False)


def test_lenient_campaign_does_not_downgrade_malformed_progress_to_unresolved() -> None:
    root = etree.fromstring(
        '<LevelSetStats Name="Pack"><Areas><AreaStats SID="Pack/Map"><Modes>'
        '<AreaModeStats Deaths="invalid"/></Modes></AreaStats></Areas></LevelSetStats>'
    )
    with pytest.raises(ValueError, match=r'[Dd]eaths'):
        CampaignProgress.from_xml(root, strict=False)


def test_wrong_partition_is_inspectable_but_never_writable() -> None:
    source = _decode()
    history = require_map(source.levels['Pack/Map'].maps[LevelSide.B])
    history.time_played = history.bests.time = Duration()
    data = source.tostring().replace(b'Pack/Map', b'Other/Map')
    with pytest.raises(BadProgressSave, match=r'Other/Map.*Pack'):
        CampaignProgress.from_xml(tree.parse(data).getroot(), strict=True)
    campaign = CampaignProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert campaign.levels['Other/Map'].sid == 'Other/Map'
    wrong = campaign.levels['Other/Map']
    assert campaign.levels.adddefault(wrong) is wrong
    copied = campaign.levels.copy()
    assert copied['Other/Map'] is campaign.levels['Other/Map']
    copied.clear()
    assert 'Other/Map' in campaign.levels
    with pytest.raises(BadProgressSave):
        campaign.tostring()
    target = CampaignProgress('Other')
    target.levels.add(campaign.levels.pop(wrong.sid))
    assert _decode(campaign.tostring()).levels == {}
    restored = CampaignProgress.from_xml(tree.parse(target.tostring()).getroot(), strict=False)
    assert list(restored.levels) == ['Other/Map']
    assert require_map(restored.levels['Other/Map'].maps[LevelSide.A]).deaths == 2


def test_best_counts_preserve_zero_and_validate_output_range_without_hidden_mutations() -> None:
    progress = MapProgress(single_run_completed=True)
    progress.bests.deaths = -1
    with pytest.raises(ValueError):
        progress.tostring()
    assert progress.bests.deaths == -1 and progress.single_run_completed
    progress.bests.deaths = progress.bests.dashes = 0
    result = MapProgress.from_xml(tree.parse(progress.tostring()).getroot())
    assert result.bests.deaths == result.bests.dashes == 0


def test_failed_encoding_does_not_damage_domain_or_preservation_input() -> None:
    campaign = _decode()
    original = campaign.tostring()
    progress = require_map(campaign.levels['Pack/Map'].maps[LevelSide.A])
    progress.time_played = Duration.from_seconds(Fraction(1, 3))
    with pytest.raises(ValueError):
        campaign.tostring()
    progress.time_played = Duration.from_timer_frames(1)
    assert campaign.tostring() == original


def test_xml_text_errors_are_rejected_for_checkpoint_and_poem_edits() -> None:
    campaign = _decode()
    require_map(campaign.levels['Pack/Map'].maps[LevelSide.A]).checkpoints.add('\x00')
    with pytest.raises(ValueError):
        campaign.tostring()
    require_map(campaign.levels['Pack/Map'].maps[LevelSide.A]).checkpoints.remove('\x00')
    campaign.poem.append('\x00')
    with pytest.raises(ValueError):
        campaign.tostring()


@pytest.mark.parametrize(
    'modes',
    (
        '<Modes/>',
        '<Modes><AreaModeStats TimePlayed="0" Deaths="0"/></Modes>',
        '<Modes>' + '<AreaModeStats TimePlayed="0" Deaths="0"/>' * 4 + '</Modes>',
        (
            '<Modes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<AreaModeStats xsi:nil="true"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/>'
            '<AreaModeStats TimePlayed="0" Deaths="0"/></Modes>'
        ),
    ),
)
def test_fixed_side_shape_is_strict_on_load_and_always_checked_on_write(modes: str) -> None:
    data = f'<AreaStats SID="Pack/Map">{modes}</AreaStats>'
    with pytest.raises(BadProgressSave):
        LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert len(level.maps) == modes.count('<AreaModeStats')
    with pytest.raises(BadProgressSave):
        level.tostring()


def test_omitted_modes_are_rejected_strictly_but_keep_defaults_when_lenient() -> None:
    data = '<AreaStats SID="Pack/Map" Cassette="true"/>'
    with pytest.raises(BadProgressSave, match='Missing Modes'):
        LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert len(level.maps) == 3 and level.cassette
    assert len({id(progress) for progress in level.maps}) == 3
    for progress in level.maps:
        progress = require_map(progress)
        assert progress.time_played == Duration() and progress.deaths == 0
        assert not progress.completed and not progress.single_run_completed
        assert not progress.full_clear and not progress.heart
        assert not progress.strawberries and not progress.checkpoints
        assert progress.bests == PersonalBests()
    require_map(level.maps[0]).deaths = 2
    require_map(level.maps[0]).checkpoints.add('cp')
    assert require_map(level.maps[1]).deaths == require_map(level.maps[2]).deaths == 0
    assert not require_map(level.maps[1]).checkpoints and not require_map(level.maps[2]).checkpoints
    encoded = level.tostring()
    root = tree.parse(encoded).getroot()
    assert len(root.findall('Modes/AreaModeStats')) == 3
    assert root.get('Cassette') == 'true'
    restored = LevelProgress.from_xml(root)
    assert require_map(restored.maps[0]).deaths == 2 and restored.cassette
    assert require_map(restored.maps[0]).checkpoints == {'cp'}


def test_explicit_empty_modes_are_not_constructor_default_progress() -> None:
    data = '<AreaStats SID="Pack/Map"><Modes/></AreaStats>'
    with pytest.raises(BadProgressSave):
        LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert level.maps == []
    with pytest.raises(BadProgressSave, match='three'):
        level.tostring()


@pytest.mark.parametrize('nil', ['true', '1'])
def test_nil_modes_keep_defaults_leniently_and_write_real_progress(nil: str) -> None:
    data = (
        '<AreaStats SID="Pack/Map" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<Modes xsi:nil="{nil}"><!--keep--><?keep marker?></Modes>'
        '</AreaStats>'
    )
    with pytest.raises(BadProgressSave, match='Nil Modes'):
        LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert len(level.maps) == 3 and len({id(progress) for progress in level.maps}) == 3
    assert all(require_map(progress).deaths == 0 for progress in level.maps)
    assert all(require_map(progress).time_played == Duration() for progress in level.maps)
    require_map(level.maps[0]).deaths = 7
    require_map(level.maps[0]).checkpoints.add('cp')
    assert not require_map(level.maps[1]).checkpoints and not require_map(level.maps[2]).checkpoints
    encoded = level.tostring()
    root = tree.parse(encoded).getroot()
    modes = root.find('Modes')
    assert modes is not None and modes.get(f'{{{tree.XSI}}}nil') is None
    assert len(modes.findall('AreaModeStats')) == 3
    assert b'<!--keep-->' not in encoded and b'<?keep marker?>' not in encoded
    restored = LevelProgress.from_xml(root)
    assert require_map(restored.maps[0]).deaths == 7
    assert require_map(restored.maps[0]).checkpoints == {'cp'}


@pytest.mark.parametrize('nil', ['false', '0'])
def test_false_nil_marker_does_not_turn_empty_modes_into_default_progress(nil: str) -> None:
    data = (
        '<AreaStats SID="Pack/Map" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<Modes xsi:nil="{nil}"/></AreaStats>'
    )
    with pytest.raises(BadProgressSave):
        LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert level.maps == []


@pytest.mark.parametrize('nil', ['true', '1'])
@pytest.mark.parametrize('deaths', ['9', 'invalid'])
@pytest.mark.parametrize('size', [1, 4])
def test_nil_modes_ignore_children_even_when_their_fields_are_invalid(
    nil: str, deaths: str, size: int
) -> None:
    data = (
        '<AreaStats SID="Pack/Map" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<Modes xsi:nil="{nil}">'
        + f'<AreaModeStats Deaths="{deaths}" TimePlayed="invalid"><Ignored/></AreaModeStats>' * size
        + '</Modes></AreaStats>'
    )
    with pytest.raises(BadProgressSave, match='Nil Modes'):
        LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert len(level.maps) == 3 and len({id(progress) for progress in level.maps}) == 3
    assert all(require_map(progress).deaths == 0 for progress in level.maps)
    assert all(require_map(progress).time_played == Duration() for progress in level.maps)
    require_map(level.maps[LevelSide.B]).deaths = 2
    encoded = level.tostring()
    root = tree.parse(encoded).getroot()
    modes = root.find('Modes')
    assert modes is not None and not tree.is_nil(modes)
    assert len(modes.findall('AreaModeStats')) == 3
    assert b'Ignored' not in encoded and b'berries-' not in encoded
    restored = LevelProgress.from_xml(root)
    assert [require_map(progress).deaths for progress in restored.maps] == [0, 2, 0]


def test_lenient_missing_position_can_be_inspected_and_explicitly_repaired() -> None:
    data = '<AreaStats SID="Pack/Map"><Modes><AreaModeStats TimePlayed="1" Deaths="2"/></Modes></AreaStats>'
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert require_map(level.maps[0]).deaths == 2
    assert level.maps[1:] == [] and level.maps == [level.maps[0]]
    with pytest.raises(IndexError):
        level.maps[LevelSide.B]
    level.maps.extend([MapProgress(), MapProgress()])
    restored = LevelProgress.from_xml(tree.parse(level.tostring()).getroot(), strict=False)
    assert len(restored.maps) == 3 and require_map(restored.maps[0]).deaths == 2


def test_lenient_nil_position_does_not_shift_later_progress() -> None:
    data = (
        '<AreaStats SID="Pack/Map" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Modes>'
        '<AreaModeStats TimePlayed="0" Deaths="1"/>'
        '<AreaModeStats xsi:nil="true"/>'
        '<AreaModeStats TimePlayed="0" Deaths="3"/>'
        '</Modes></AreaStats>'
    )
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    a, b, c = level.maps
    assert a is not None and a.deaths == 1
    assert b is None
    assert c is not None and level.maps[LevelSide.C] is c and c.deaths == 3
    assert level.maps[1:] == [None, c] and level.maps[::-1] == [c, None, a]
    with pytest.raises(BadProgressSave, match='Missing B'):
        level.tostring()


@pytest.mark.parametrize('size', [0, 1, 2, 3, 4, 6])
def test_lenient_modes_expose_all_positions_and_require_explicit_repair(size: int) -> None:
    modes = ''.join(f'<AreaModeStats TimePlayed="0" Deaths="{i}"/>' for i in range(size))
    data = f'<AreaStats SID="Pack/Map"><Modes>{modes}</Modes></AreaStats>'
    if size != 3:
        with pytest.raises(BadProgressSave):
            LevelProgress.from_xml(tree.parse(data).getroot())
    level = LevelProgress.from_xml(tree.parse(data).getroot(), strict=False)
    assert len(level.maps) == size
    assert [require_map(level.maps[i]).deaths for i in range(size)] == list(range(size))
    assert level.maps[size:] == []
    if size != 3:
        with pytest.raises(BadProgressSave):
            level.tostring()
    original = copy(level)
    original.maps = level.maps.copy()
    retained = level.maps[:3]
    extras = level.maps[3:]
    del level.maps[3:]
    del level.maps[3:]  # Already truncated is a no-op.
    assert len(level.maps) == min(3, size) and level.maps == retained
    assert len(original.maps) == size and original.maps[3:] == extras
    if size < 3:
        with pytest.raises(BadProgressSave, match='three'):
            level.tostring()
        level.maps.extend(MapProgress() for _ in range(3 - size))
    restored = LevelProgress.from_xml(tree.parse(level.tostring()).getroot())
    assert len(restored.maps) == 3
    assert [require_map(progress).deaths for progress in restored.maps] == [
        i if i < size else 0 for i in range(3)
    ]


def test_truncate_discards_extra_progress_without_adjusting_totals() -> None:
    extra = (
        b'<!--extra--><AreaModeStats TimePlayed="9" Deaths="9"/>'
        b'<AreaModeStats xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>'
        b'<?after extra?>'
    )
    data = _CAMPAIGN.replace(b'</Modes>', extra + b'</Modes>')
    campaign = CampaignProgress.from_xml(tree.parse(data).getroot(), strict=False)
    level = campaign.levels['Pack/Map']
    assert len(level.maps) == 5 and level.maps[-1] is None
    extra_progress = require_map(level.maps[3])
    assert extra_progress.deaths == 9
    with pytest.raises(BadProgressSave, match='three'):
        campaign.tostring()
    del level.maps[3:]
    encoded = campaign.tostring()
    root = tree.parse(encoded).getroot()
    assert len(root.findall('Areas/AreaStats/Modes/AreaModeStats')) == 3
    assert root.findtext('TotalStrawberries') == '5'
    assert root.findtext('UnlockedAreas') == '2'
    assert b'<!--extra-->' not in encoded and b'<?after extra?>' not in encoded
    assert b'<Extra' not in encoded and b'berries-' not in encoded
    assert extra_progress.deaths == 9  # Detached references stay usable.
    assert len(_decode(encoded).levels['Pack/Map'].maps) == 3


def test_nil_known_containers_can_be_populated_without_nil_hiding_new_values() -> None:
    data = _CAMPAIGN.replace(
        b'<Checkpoints><string>cp</string></Checkpoints>',
        b'<Checkpoints xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>',
    )
    campaign = _decode(data)
    require_map(campaign.levels['Pack/Map'].maps[LevelSide.A]).checkpoints.add('new')
    root = tree.parse(campaign.tostring()).getroot()
    node = root.find('Areas/AreaStats/Modes/AreaModeStats/Checkpoints')
    assert node is not None and not tree.is_nil(node)
    assert node.findtext('string') == 'new'


def test_reset_includes_unresolved_progress_without_changing_modes_structure() -> None:
    root = tree.parse(
        '<LevelSetStats Name="Pack"><Areas><AreaStats ID="42" Cassette="true"><Modes>'
        '<AreaModeStats Deaths="5"/><AreaModeStats xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>'
        '</Modes></AreaStats></Areas></LevelSetStats>'
    ).getroot()
    campaign = CampaignProgress.from_xml(root, strict=False)
    unresolved = campaign.unresolved_levels[0]
    first = unresolved.maps[0]
    assert first is not None and first.deaths == 5 and unresolved.cassette
    campaign.reset()
    assert unresolved.id == 42 and not unresolved.cassette
    assert unresolved.maps == [first, None] and first.deaths == 0
    repaired = unresolved.resolve('Pack/Map')
    assert not repaired.cassette and repaired.maps[0] is first
