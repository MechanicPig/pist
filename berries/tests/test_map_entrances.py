from collections.abc import Callable
from pathlib import Path

import pytest

from berries.game.binmap import AttrValue
from berries.map_entrances import (
    EntranceValue,
    MapEntranceRegion,
    MapEntranceRule,
    MapEntranceRules,
    MapEntranceSource,
    load_map_entrance_rules,
    parse_map_entrance_rules,
)


def _region() -> MapEntranceRegion:
    return MapEntranceRegion(x=EntranceValue(value=0), y=EntranceValue(value=0))


def test_parse_map_entrance_rules_from_text() -> None:
    assert parse_map_entrance_rules('rules = []') == MapEntranceRules()


@pytest.mark.parametrize('invalid', ('[broken', 'rules = 42'))
def test_parse_map_entrance_rules_rejects_invalid_input(invalid: str) -> None:
    with pytest.raises(ValueError, match='Invalid map entrance rules'):
        parse_map_entrance_rules(invalid)


def test_explicit_map_entrance_file_must_exist(tmp_path: Path) -> None:
    missing = tmp_path / 'missing.toml'
    with pytest.raises(ValueError) as caught:
        load_map_entrance_rules(missing)
    assert repr(missing) in str(caught.value)
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize('expected', (True, 1, 1.0))
@pytest.mark.parametrize('actual', (True, 1, 1.0))
def test_entrance_conditions_compare_exact_scalar_types(
    expected: AttrValue, actual: AttrValue
) -> None:
    rule = MapEntranceRule(
        source=MapEntranceSource.ENTITY,
        name='Example/Entrance',
        when={'enabled': expected},
        region=_region(),
    )
    assert rule.matches(rule.source, rule.name, {'enabled': actual}) == (
        type(expected) is type(actual)
    )


def test_entrance_override_keys_preserve_scalar_types() -> None:
    rules = tuple(
        MapEntranceRule(
            source=MapEntranceSource.ENTITY,
            name='Example/Entrance',
            when={'enabled': value},
            region=_region(),
        )
        for value in (True, 1, 1.0)
    )
    assert len({rule.key for rule in rules}) == 3
    shared = MapEntranceRules(rules=rules)
    replacement = rules[1].model_copy(update={'target_attr': 'other'})
    merged = shared.merged(MapEntranceRules(rules=(replacement,)))
    assert merged.rules[0].target_attr == 'other'
    assert {type(rule.when['enabled']) for rule in merged.rules} == {bool, int, float}


def test_entrance_rule_keys_merge_local_overrides_without_changing_layers() -> None:
    original = MapEntranceRule(
        source=MapEntranceSource.ENTITY,
        name='Example/Entrance',
        when={'active': True},
        region=_region(),
    )
    replacement = original.model_copy(update={'target_attr': 'target'})
    other = original.model_copy(update={'source': MapEntranceSource.TRIGGER})
    assert original.key == replacement.key
    assert original.key != other.key
    shared = MapEntranceRules(rules=(original, other))
    local = MapEntranceRules(rules=(replacement,))
    assert shared.merged(local).rules == (replacement, other)
    assert shared.rules == (original, other) and local.rules == (replacement,)


@pytest.mark.parametrize(
    'model',
    (
        lambda: EntranceValue(attr=''),
        lambda: MapEntranceRule(
            source=MapEntranceSource.ENTITY,
            name='',
            region=_region(),
        ),
        lambda: MapEntranceRule(
            source=MapEntranceSource.ENTITY,
            name='Example/Entrance',
            target_attr='',
            region=_region(),
        ),
    ),
)
def test_map_entrance_rules_reject_blank_attribute_and_entity_names(
    model: Callable[[], EntranceValue | MapEntranceRule],
) -> None:
    with pytest.raises(ValueError):
        model()
