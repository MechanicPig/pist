import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from berries.entities import config, serialization
from berries.entities import rules as entity_rules
from berries.game.binmap import AttrValue

SHARED_RULES = config.load_default_entity_rules()


def test_entity_rules_can_be_parsed_from_explicit_texts() -> None:
    rules = config.parse_entity_rules(
        '[entities.Test]\nrules = [{kind="berry", when={moon=false}}]\n',
        kinds='[kinds.berry]\nlabel="Berry"\n',
    )
    assert rules.entity_kind('Test', {'moon': False}) == 'berry'
    assert rules.entity_kind('Test', {'moon': True}) is None
    with pytest.raises(ValueError, match='Unknown kind'):
        config.parse_entity_rules('[entities.Test]\nrules = [{kind="missing"}]', kinds='')


def test_rule_layer_kind_replacement_keeps_conditions_and_original() -> None:
    rules = entity_rules.EntityRuleLayer.model_validate(
        {'entities': {'Test': {'rules': [{'kind': 'old', 'missing': ['moon']}, {'kind': None}]}}}
    )
    replaced = rules.with_replaced_kind('old', 'new')
    assert replaced.entities['Test'].rules[0].kind == 'new'
    assert (
        replaced.entities['Test'].rules[0].conditions == rules.entities['Test'].rules[0].conditions
    )
    assert replaced.entities['Test'].rules[1].kind is None
    assert rules.entities['Test'].rules[0].kind == 'old'


@pytest.mark.parametrize('names', (('child', 'parent'), ('parent', 'child')))
def test_unknown_ancestor_is_always_a_validation_error(names: tuple[str, str]) -> None:
    definitions = {
        'child': {'label': 'Child', 'parent': 'parent'},
        'parent': {'label': 'Parent', 'parent': 'missing'},
    }
    with pytest.raises(ValidationError, match='Unknown parent kind'):
        entity_rules.EntityRules.model_validate(
            {'kinds': {name: definitions[name] for name in names}}
        )


@pytest.mark.parametrize('first,second', ((False, 0), (True, 1), (1, 1.0)))
@pytest.mark.parametrize('metadata', (False, True))
def test_rule_overrides_preserve_condition_types(
    first: AttrValue, second: AttrValue, metadata: bool
) -> None:
    field = 'meta' if metadata else 'when'
    shared = entity_rules.EntityRule.model_validate({'kind': 'strawberry', field: {'flag': first}})
    local = entity_rules.EntityRule.model_validate({'kind': 'moonberry', field: {'flag': second}})
    assert shared.conditions != local.conditions
    assert len({shared.conditions, local.conditions}) == 2
    baseline = entity_rules.EntityRulesForId(rules=(shared,))
    merged = baseline.merged(entity_rules.EntityRulesForId(rules=(local,)))
    assert merged.rules == (local, shared)


@pytest.mark.parametrize(
    'model',
    (
        lambda: entity_rules.EntityRule(missing=('',)),
        lambda: entity_rules.EntityRule(missing_meta=('',)),
    ),
)
def test_entity_rule_configuration_rejects_blank_required_names(
    model: Callable[[], entity_rules.EntityRule],
) -> None:
    with pytest.raises(ValueError):
        model()


def _split_config(path: Path) -> Path:
    """Move a compact test fixture's kinds table into its required sibling file."""
    config = tomllib.loads(path.read_text(encoding='utf-8'))
    kinds = config.pop('kinds', {})
    kinds_path = path.with_name('kinds.toml')
    kinds_path.write_text(tomlkit.dumps({'kinds': kinds}), encoding='utf-8')
    path.write_text(tomlkit.dumps(config), encoding='utf-8')
    return kinds_path


@pytest.mark.parametrize(
    ('name', 'attrs', 'expected'),
    [
        ('strawberry', {}, 'strawberry'),
        ('strawberry', {'moon': True}, 'moonberry'),
        ('strawberry', {'golden': True, 'moon': True}, 'moonberry'),
        ('DSModHelper/ReskinnableStrawberry', {'moon': True}, 'secretberry'),
        ('goldenBerry', {}, 'goldenberry'),
        ('JungleHelper/TreeDepthController', {}, 'grabless_goldenberry'),
        ('CollabUtils2/SilverBerry', {}, 'silverberry'),
        ('CollabUtils2/RainbowBerry', {}, 'rainbowberry'),
        ('cassette', {}, 'cassette'),
        ('heartGem', {}, None),
        ('ArphimigonHelper/ShieldedGoldenBerry', {}, 'goldenberry'),
        ('cassetteBlock', {}, None),
    ],
)
def test_entity_kind_uses_exact_entity_and_attribute_rules(
    name: str,
    attrs: dict[str, bool],
    expected: str | None,
) -> None:
    assert SHARED_RULES.entity_kind(name, attrs) == expected


def test_kind_sprite_is_inherited_without_implying_statistics() -> None:
    rules = config.load_default_entity_rules()

    assert rules.kind_sprite('strawberry') == 'strawberry.png'
    assert rules.kind_sprite('goldenberry') is None


def test_heart_kind_uses_configured_attribute_rules_and_default() -> None:
    rules = (
        SHARED_RULES.with_rule('heartGem', 'end_level_heart', {'endLevel': True})
        .with_rule('heartGem', 'keep_going_heart', {'endLevel': False})
        .with_rule('heartGem', 'end_level_heart', {})
    )
    assert rules.entity_kind('heartGem', {'endLevel': True}) == 'end_level_heart'
    assert rules.entity_kind('heartGem', {'endLevel': False}) == 'keep_going_heart'
    assert rules.entity_kind('heartGem', {}) == 'end_level_heart'
    assert rules.entity_kind('ArphimigonHelper/HeartGem', {'endLevel': 'true'}) == 'end_level_heart'
    assert rules.entity_kind('fakeHeart', {'endLevel': True}) is None


def test_entity_rules_can_be_loaded_from_custom_config(tmp_path: Path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.testberry]
label = 'Test Berry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'testberry', when = { moon = true } }]
""",
        encoding='utf-8',
    )

    rules = config.load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert rules.entity_kind('TestHelper/Berry', {'moon': True}) == 'testberry'
    assert rules.entity_kind('TestHelper/Berry', {'moon': False}) is None


def test_entity_rules_rejects_kind_definitions_in_an_entity_rule_file(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    kinds_path.write_text("[kinds.berry]\nlabel = 'Berry'\n", encoding='utf-8')
    entities_path = tmp_path / 'entities.toml'
    entities_path.write_text(
        """[kinds.unexpected]
label = 'Unexpected'

[entities."TestHelper/Berry"]
rules = [{ kind = 'berry' }]
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid entity rules config'):
        config.load_entity_rules(entities_path, kinds_path=kinds_path)


def test_entity_rules_support_metadata_conditions_and_terminal_non_collectibles(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.heart]
label = 'Heart'

[kinds.end_level_heart]
label = 'End-level heart'
parent = 'heart'

[entities."TestHelper/Heart"]
rules = [
  { when = { fake = true } },
  { kind = 'end_level_heart', meta = { HeartIsEnd = true } },
  { kind = 'end_level_heart', missing = ['fake'], missing_meta = ['HeartIsEnd'] },
]
""",
        encoding='utf-8',
    )

    rules = config.load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert rules.entity_kind('TestHelper/Heart', {'fake': True}) is None
    assert (
        rules.entity_kind('TestHelper/Heart', {'fake': False}, meta={'HeartIsEnd': True})
        == 'end_level_heart'
    )
    assert rules.entity_kind('TestHelper/Heart', {'fake': False}) is None
    assert rules.entity_kind('TestHelper/Heart', {}) == 'end_level_heart'
    assert rules.entity_kind('TestHelper/Heart', {'fake': False}, meta={}) is None
    assert rules.entity_kind('TestHelper/Heart', {}, meta={'HeartIsEnd': False}) is None

    serialized = serialization.entity_rules_toml(
        rules.with_exclusion('TestHelper/OtherHeart', {'fake': True})
    )
    assert 'exclude' not in serialized
    assert '[[' not in serialized
    assert '.rules.' not in serialized
    assert 'missing = ["fake"]' in serialized
    assert (
        entity_rules.EntityRuleLayer.model_validate(tomllib.loads(serialized)).entities
        == rules.with_exclusion('TestHelper/OtherHeart', {'fake': True}).entities
    )


def test_entity_rules_reject_overlapping_value_and_missing_conditions() -> None:
    with pytest.raises(ValueError, match='both matched and missing'):
        entity_rules.EntityRule(when={'moon': False}, missing=('moon',))


def test_more_specific_rules_precede_defaults_regardless_of_write_order(tmp_path: Path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.strawberry]
label = 'Strawberry'
""",
        encoding='utf-8',
    )
    rules = config.load_entity_rules(config_path, kinds_path=_split_config(config_path)).with_rule(
        'Example/Berry', 'strawberry', {}
    )
    rules = rules.with_exclusion('Example/Berry', {'fake': True})

    assert rules.entity_kind('Example/Berry', {'fake': True}) is None
    assert rules.entity_kind('Example/Berry', {'fake': False}) == 'strawberry'


@pytest.mark.parametrize('invalid_ref', ['parent', 'kind'])
def test_entity_rules_reject_unknown_refs(tmp_path: Path, invalid_ref: str) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.test]
label = 'Test'

[entities."Test"]
rules = [{ kind = 'test' }]
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(config_path)
    assert (
        config.load_entity_rules(config_path, kinds_path=kinds_path).entity_kind('Test', {})
        == 'test'
    )
    if invalid_ref == 'parent':
        kinds_path.write_text(
            "[kinds.test]\nlabel = 'Test'\nparent = 'missing'\n", encoding='utf-8'
        )
        message = "Unknown parent kind 'missing'"
    else:
        config_path.write_text(
            "[entities.Test]\nrules = [{ kind = 'missing' }]\n", encoding='utf-8'
        )
        message = "Unknown kind 'missing'"

    with pytest.raises(ValueError, match='Invalid entity rules config') as caught:
        config.load_entity_rules(config_path, kinds_path=kinds_path)
    assert isinstance(caught.value.__cause__, ValidationError)
    assert message in str(caught.value.__cause__)


def test_entity_rules_allow_non_leaf_rule_kind(tmp_path: Path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'berry' }]
""",
        encoding='utf-8',
    )

    rules = config.load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert rules.entity_kind('TestHelper/Berry', {}) == 'berry'


def test_adding_a_child_preserves_rules_targeting_its_new_parent() -> None:
    rules = entity_rules.EntityRules(
        kinds={'moonberry': entity_rules.EntityKind(label='月莓')},
        entities={
            'strawberry': entity_rules.EntityRulesForId(
                rules=(entity_rules.EntityRule(kind='moonberry'),)
            ),
        },
    )

    updated = rules.with_kind(
        'secretberry', entity_rules.EntityKind(label='秘密草莓', parent='moonberry')
    )

    assert updated.entity_kind('strawberry', {}) == 'moonberry'
    assert 'moonberry' not in updated.leaf_kind_names


@pytest.mark.parametrize('operation', ('add', 'update', 'rename'))
def test_kind_changes_accept_definitions_and_keep_empty_label_fallback(operation: str) -> None:
    rules = entity_rules.EntityRules(kinds={'old': entity_rules.EntityKind(label='Old')})
    definition = entity_rules.EntityKind(label='', sprite='custom.png')
    updated = (
        rules.with_kind('custom', definition)
        if operation == 'add'
        else rules.with_updated_kind('old', definition)
        if operation == 'update'
        else rules.with_renamed_kind('old', 'custom', definition)
    )
    name = 'old' if operation == 'update' else 'custom'
    assert updated.kinds[name] == entity_rules.EntityKind(label=name, sprite='custom.png')
    assert definition.label == ''
    assert rules.kinds == {'old': entity_rules.EntityKind(label='Old')}


def test_entity_rules_can_update_a_kind_without_renaming_it() -> None:
    rules = entity_rules.EntityRules(
        kinds={
            'berry': entity_rules.EntityKind(label='Berry'),
            'customberry': entity_rules.EntityKind(label='Custom Berry', parent='berry'),
        }
    )

    updated = rules.with_updated_kind(
        'customberry',
        entity_rules.EntityKind(
            label='Custom Strawberry', parent='berry', sprite='customberry.png'
        ),
    )

    assert updated.kinds['customberry'] == entity_rules.EntityKind(
        label='Custom Strawberry', parent='berry', sprite='customberry.png'
    )


def test_entity_rules_validate_kind_hierarchy_after_an_update() -> None:
    rules = entity_rules.EntityRules(
        kinds={
            'berry': entity_rules.EntityKind(label='Berry'),
            'strawberry': entity_rules.EntityKind(label='Strawberry', parent='berry'),
        }
    )

    with pytest.raises(ValueError, match='Circular kind parent'):
        rules.with_updated_kind(
            'berry', entity_rules.EntityKind(label='Berry', parent='strawberry')
        )


def test_entity_rules_can_rename_a_kind_and_its_refs() -> None:
    rules = entity_rules.EntityRules(
        kinds={
            'berry': entity_rules.EntityKind(label='Berry'),
            'strawberry': entity_rules.EntityKind(label='Strawberry', parent='berry'),
            'goldenberry': entity_rules.EntityKind(label='Golden Berry', parent='strawberry'),
        },
        entities={
            'strawberry': entity_rules.EntityRulesForId(
                rules=(entity_rules.EntityRule(kind='strawberry'),)
            ),
            'goldenBerry': entity_rules.EntityRulesForId(
                rules=(entity_rules.EntityRule(kind='goldenberry'),)
            ),
        },
    )

    renamed = rules.with_renamed_kind(
        'strawberry', 'redberry', entity_rules.EntityKind(label='Red Berry', parent='berry')
    )

    assert 'strawberry' not in renamed.kinds
    assert renamed.kinds['redberry'].label == 'Red Berry'
    assert renamed.kinds['goldenberry'].parent == 'redberry'
    assert renamed.entities['strawberry'].rules[0].kind == 'redberry'
    assert renamed.entities['goldenBerry'].rules[0].kind == 'goldenberry'


def test_entity_rules_can_delete_a_kind_and_fall_back_to_its_parent() -> None:
    rules = entity_rules.EntityRules(
        kinds={
            'berry': entity_rules.EntityKind(label='Berry'),
            'strawberry': entity_rules.EntityKind(label='Strawberry', parent='berry'),
            'wingedberry': entity_rules.EntityKind(label='Winged Berry', parent='strawberry'),
        },
        entities={
            'strawberry': entity_rules.EntityRulesForId(
                rules=(entity_rules.EntityRule(kind='strawberry'),)
            ),
        },
    )

    deleted = rules.with_deleted_kind('strawberry')

    assert deleted.direct_rule_count('strawberry') == 0
    assert deleted.entities['strawberry'].rules[0].kind == 'berry'
    assert deleted.kinds['wingedberry'].parent == 'berry'
    with pytest.raises(ValueError, match='Cannot delete root'):
        deleted.with_deleted_kind('berry')


def test_entity_rules_reject_sprite_paths_outside_sprite_directories(tmp_path: Path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'
sprite = '../outside.png'
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(config_path)
    with pytest.raises(ValueError, match='Invalid entity rules config') as caught:
        config.load_entity_rules(config_path, kinds_path=kinds_path)
    assert isinstance(caught.value.__cause__, ValidationError)
    errors = caught.value.__cause__.errors()
    assert len(errors) == 1
    assert errors[0]['loc'] == ('kinds', 'berry', 'sprite')
    assert 'relative path' in errors[0]['msg']


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('table_field', "{ table = 'maps', field = 'berries' }"),
        ('stat', "'count'"),
    ],
)
def test_entity_rules_reject_application_stat_config(
    tmp_path: Path, field: str, value: str
) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        f"""[kinds.berry]
label = 'Berry'
{field} = {value}
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(config_path)
    with pytest.raises(ValueError, match='Invalid entity rules config') as caught:
        config.load_entity_rules(config_path, kinds_path=kinds_path)
    assert isinstance(caught.value.__cause__, ValidationError)
    assert [(error['loc'], error['type']) for error in caught.value.__cause__.errors()] == [
        (('kinds', 'berry', field), 'extra_forbidden')
    ]


def test_scalar_typed_rule_conditions_survive_overrides_and_serialization() -> None:
    rules = entity_rules.EntityRules.model_validate(
        {
            'kinds': {'berry': {'label': 'Berry'}},
            'entities': {},
        }
    )
    rules = rules.with_rule('Example/Berry', 'berry', {'flag': True})
    rules = rules.with_exclusion('Example/Berry', {'flag': 1})
    rules = rules.with_rule('Example/Berry', 'berry', {'flag': 1.0})
    assert len(rules.entities['Example/Berry'].rules) == 3
    assert rules.entity_kind('Example/Berry', {'flag': True}) == 'berry'
    assert rules.entity_kind('Example/Berry', {'flag': 1}) is None
    assert rules.entity_kind('Example/Berry', {'flag': 1.0}) == 'berry'
    layer = entity_rules.EntityRuleLayer(entities=rules.entities)
    text = serialization.entity_rules_toml(layer)
    restored = entity_rules.EntityRuleLayer.model_validate(tomllib.loads(text))
    assert {rule.conditions for rule in restored.entities['Example/Berry'].rules} == {
        rule.conditions for rule in rules.entities['Example/Berry'].rules
    }
