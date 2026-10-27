"""Local map entrance discovery and override policy belongs to the application."""

from pathlib import Path

import pytest

from berries.map_entrances import load_map_entrance_rules
from pist.map_entrances import load_map_entrance_rule_layers

RULE = """[[rules]]
source = 'entity'
name = 'Example/Entrance'
[rules.region]
x = { value = 0 }
y = { value = 0 }
"""


def test_missing_local_entrances_use_shared_rules(tmp_path: Path) -> None:
    shared = tmp_path / 'shared.toml'
    shared.write_text(RULE, encoding='utf-8')
    assert load_map_entrance_rule_layers(shared, tmp_path / 'missing.toml') == (
        load_map_entrance_rules(shared)
    )


def test_local_entrances_override_matching_shared_rules(tmp_path: Path) -> None:
    shared = tmp_path / 'shared.toml'
    local = tmp_path / 'local.toml'
    shared.write_text(RULE, encoding='utf-8')
    local.write_text(RULE.replace('value = 0', 'value = 8'), encoding='utf-8')
    merged = load_map_entrance_rule_layers(shared, local)
    assert len(merged.rules) == 1
    assert merged.rules[0].region.x.resolve({}) == 8
    assert load_map_entrance_rules(shared).rules[0].region.x.resolve({}) == 0


@pytest.mark.parametrize('invalid', ('[broken', 'rules = 42'))
def test_invalid_local_entrances_are_not_silently_ignored(tmp_path: Path, invalid: str) -> None:
    shared = tmp_path / 'shared.toml'
    local = tmp_path / 'local.toml'
    shared.write_text(RULE, encoding='utf-8')
    local.write_text(invalid, encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid map entrance rules') as caught:
        load_map_entrance_rule_layers(shared, local)
    assert repr(local) in str(caught.value)
