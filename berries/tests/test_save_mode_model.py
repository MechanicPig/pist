"""AreaModeStats keeps all protocol values without retaining XML fragments."""

from copy import copy

import pytest
from lxml import etree

from berries.game.duration import Duration
from berries.game.saves.codecs.xml import tree
from berries.game.saves.progress.map import MapProgress
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from berries.map_entity_id import MapEntityID


def test_unestablished_raw_records_and_historical_count_are_not_discarded() -> None:
    node = etree.fromstring(
        '<AreaModeStats TimePlayed="11" Deaths="2" TotalStrawberries="8" '
        'Completed="true" SingleRunCompleted="false" FullClear="false" HeartGem="true" '
        'BestTime="-3" BestFullClearTime="0" BestDeaths="7" BestDashes="9">'
        '<Strawberries><EntityID Key="room:1"/></Strawberries>'
        '<Checkpoints><string>cp</string></Checkpoints></AreaModeStats>'
    )
    progress = MapProgress.from_xml(node)
    assert progress.bests.time == Duration.from_dotnet_ticks(-3)
    assert progress.bests.deaths == 7 and progress.bests.dashes == 9
    assert not progress.single_run_completed
    assert progress.total_strawberries == 8
    progress.strawberries.add(MapEntityID('room', 2))
    assert progress.total_strawberries == 9
    assert progress.berry_offset() == 7
    restored = MapProgress.from_xml(progress.to_xml(), strict=False)
    assert restored.bests == progress.bests
    assert restored.total_strawberries == 9
    progress.total_strawberries = 20
    assert progress.total_strawberries == 20 and restored.total_strawberries == 9


def test_mode_output_uses_current_domain_values_without_parsing_previous_xml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = MapProgress.from_xml(etree.fromstring('<AreaModeStats/>'))
    progress.deaths = 5
    progress.bests.dashes = 3

    def unexpected_parse(data: str | bytes) -> None:
        raise AssertionError('A modeled map must not reparse its input while encoding.')

    with monkeypatch.context() as patch:
        patch.setattr(tree, 'parse', unexpected_parse)
        encoded = progress.tostring()
    restored = MapProgress.from_xml(etree.fromstring(encoded), strict=True)
    assert restored.deaths == 5 and restored.bests.dashes == 3
    assert copy(progress).strawberries is progress.strawberries


def test_unknown_policy_covers_attributes_and_nested_collection_items() -> None:
    xml = '<AreaModeStats Future="1"><Strawberries><EntityID Key="r:1" Extra="x"/></Strawberries></AreaModeStats>'
    with pytest.raises(UnknownSaveFieldError) as caught:
        MapProgress.from_xml(etree.fromstring(xml), path='Save.Areas[2].Modes[0]')
    assert caught.value.path == 'Save.Areas[2].Modes[0].@Future'
    with pytest.warns(UnknownSaveFieldWarning) as recorded:
        value = MapProgress.from_xml(
            etree.fromstring(xml), unknown='warn', path='Save.Areas[2].Modes[0]'
        )
    assert [
        warning.message.path
        for warning in recorded
        if isinstance(warning.message, UnknownSaveFieldWarning)
    ] == [
        'Save.Areas[2].Modes[0].@Future',
        'Save.Areas[2].Modes[0].Strawberries[0].@Extra',
    ]
    assert 'Future' not in value.tostring().decode() and 'Extra' not in value.tostring().decode()


def test_invalid_known_input_is_not_ignored_by_warn() -> None:
    with pytest.raises(ValueError):
        MapProgress.from_xml(etree.fromstring('<AreaModeStats Deaths="bad"/>'), unknown='warn')
