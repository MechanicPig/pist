import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from berries.entities import rules as entity_rules

SHARED_RULES = entity_rules.load_entity_rules(entity_rules.SHARED_ENTITIES_PATH)


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
    rules = entity_rules.load_entity_rules(entity_rules.SHARED_ENTITIES_PATH)

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

    rules = entity_rules.load_entity_rules(config_path, kinds_path=_split_config(config_path))

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
        entity_rules.load_entity_rules(entities_path, kinds_path=kinds_path)


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

    rules = entity_rules.load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert rules.entity_kind('TestHelper/Heart', {'fake': True}) is None
    assert (
        rules.entity_kind('TestHelper/Heart', {'fake': False}, meta={'HeartIsEnd': True})
        == 'end_level_heart'
    )
    assert rules.entity_kind('TestHelper/Heart', {'fake': False}) is None
    assert rules.entity_kind('TestHelper/Heart', {}) == 'end_level_heart'
    assert rules.entity_kind('TestHelper/Heart', {'fake': False}, meta={}) is None
    assert rules.entity_kind('TestHelper/Heart', {}, meta={'HeartIsEnd': False}) is None

    serialized = entity_rules.entity_rules_toml(
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
    rules = entity_rules.load_entity_rules(
        config_path, kinds_path=_split_config(config_path)
    ).with_rule('Example/Berry', 'strawberry', {})
    rules = rules.with_exclusion('Example/Berry', {'fake': True})

    assert rules.entity_kind('Example/Berry', {'fake': True}) is None
    assert rules.entity_kind('Example/Berry', {'fake': False}) == 'strawberry'


@pytest.mark.parametrize('invalid_reference', ['parent', 'kind'])
def test_entity_rules_reject_unknown_references(tmp_path: Path, invalid_reference: str) -> None:
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
        entity_rules.load_entity_rules(config_path, kinds_path=kinds_path).entity_kind('Test', {})
        == 'test'
    )
    if invalid_reference == 'parent':
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
        entity_rules.load_entity_rules(config_path, kinds_path=kinds_path)
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

    rules = entity_rules.load_entity_rules(config_path, kinds_path=_split_config(config_path))

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

    updated = rules.with_kind('secretberry', '秘密草莓', parent='moonberry')

    assert updated.entity_kind('strawberry', {}) == 'moonberry'
    assert 'moonberry' not in updated.leaf_kind_names


def test_entity_rules_can_add_a_kind_under_an_existing_parent(tmp_path: Path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'
""",
        encoding='utf-8',
    )
    store = entity_rules.EntityConfigStore(config_path, kinds_path=_split_config(config_path))

    rules = store.load().with_kind(
        'customberry', 'Custom Berry', parent='berry', sprite='customberry.png'
    )
    store.save(rules)

    loaded = store.load()
    assert loaded.kinds['customberry'].label == 'Custom Berry'
    assert loaded.kinds['customberry'].parent == 'berry'
    assert loaded.kinds['customberry'].sprite == 'customberry.png'
    assert 'customberry' in loaded.leaf_kind_names


def test_entity_rules_can_update_a_kind_without_renaming_it() -> None:
    rules = entity_rules.EntityRules(
        kinds={
            'berry': entity_rules.EntityKind(label='Berry'),
            'customberry': entity_rules.EntityKind(label='Custom Berry', parent='berry'),
        }
    )

    updated = rules.with_updated_kind(
        'customberry', 'Custom Strawberry', parent='berry', sprite='customberry.png'
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
        rules.with_updated_kind('berry', 'Berry', parent='strawberry')


def test_entity_rules_can_rename_a_kind_and_its_references() -> None:
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

    renamed = rules.with_renamed_kind('strawberry', 'redberry', 'Red Berry', parent='berry')

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


def test_entity_config_store_renames_kinds_in_all_rule_layers(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    shared_path = tmp_path / 'shared.toml'
    local_path = tmp_path / 'local.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.goldenberry]
label = 'Golden Berry'
parent = 'strawberry'
""",
        encoding='utf-8',
    )
    shared_path.write_text(
        """[entities.strawberry]
rules = [{ kind = 'strawberry' }]

[entities."Example/Collectible"]
rules = [{ kind = 'strawberry', when = { moon = false } }]
""",
        encoding='utf-8',
    )
    local_path.write_text(
        """[entities."Local/Collectible"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    store = entity_rules.EntityConfigStore(
        shared_path=shared_path,
        kinds_path=kinds_path,
        local_path=local_path,
    )

    renamed = store.load().with_renamed_kind('strawberry', 'redberry', 'Red Berry', parent='berry')
    store.rename_kind('strawberry', 'redberry', renamed)

    loaded = store.load()
    assert loaded.kinds['goldenberry'].parent == 'redberry'
    assert loaded.entity_kind('strawberry', {}) == 'redberry'
    assert loaded.entity_kind('Example/Collectible', {'moon': False}) == 'redberry'
    assert loaded.entity_kind('Local/Collectible', {}) == 'redberry'

    deleted = loaded.with_deleted_kind('redberry')
    store.delete_kind('redberry', 'berry', deleted)

    loaded = store.load()
    assert 'redberry' not in loaded.kinds
    assert loaded.kinds['goldenberry'].parent == 'berry'
    assert loaded.entity_kind('strawberry', {}) == 'berry'
    assert loaded.entity_kind('Example/Collectible', {'moon': False}) == 'berry'
    assert loaded.entity_kind('Local/Collectible', {}) == 'berry'


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
        entity_rules.load_entity_rules(config_path, kinds_path=kinds_path)
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
        entity_rules.load_entity_rules(config_path, kinds_path=kinds_path)
    assert isinstance(caught.value.__cause__, ValidationError)
    assert [(error['loc'], error['type']) for error in caught.value.__cause__.errors()] == [
        (('kinds', 'berry', field), 'extra_forbidden')
    ]


def test_rule_store_updates_a_template_condition_without_duplicate(tmp_path: Path) -> None:
    source_path = tmp_path / 'entities.toml'
    source_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    store = entity_rules.EntityConfigStore(source_path, kinds_path=_split_config(source_path))

    rules = store.load().with_rule('TestHelper/Berry', 'strawberry', {'moon': True})
    store.save(rules)
    reloaded = store.load()

    assert [rule.when for rule in reloaded.entities['TestHelper/Berry'].rules] == [
        {'moon': True},
        {},
    ]
    updated = reloaded.with_rule('TestHelper/Berry', 'strawberry', {'moon': True})
    assert len(updated.entities['TestHelper/Berry'].rules) == 2


def test_local_rule_layer_adds_conditions_without_copying_shared_rules(tmp_path: Path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.moonberry]
label = 'Moonberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[entities."TestHelper/Berry"]
rules = [{ kind = 'moonberry', when = { moon = true } }]
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(shared_path)
    store = entity_rules.EntityConfigStore(
        shared_path=shared_path, kinds_path=kinds_path, local_path=local_path
    )
    rules = store.load().with_rule('TestHelper/Berry', 'moonberry', {'golden': True})
    store.save(rules)

    reloaded = entity_rules.load_entity_rule_layers(shared_path, local_path, kinds_path=kinds_path)
    assert reloaded.entity_kind('TestHelper/Berry', {}) == 'strawberry'
    assert reloaded.entity_kind('TestHelper/Berry', {'moon': True}) == 'moonberry'
    assert reloaded.entity_kind('TestHelper/Berry', {'golden': True}) == 'moonberry'
    assert '[kinds.strawberry]' not in local_path.read_text(encoding='utf-8')


def test_local_entity_layer_rejects_kind_definitions(tmp_path: Path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.heart]
label = 'Heart'
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[kinds.collectible]
label = 'Collectible'

[kinds.heart]
label = 'Local heart'
parent = 'collectible'
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(shared_path)
    with pytest.raises(ValueError, match='Invalid entity rules config layers'):
        entity_rules.load_entity_rule_layers(shared_path, local_path, kinds_path=kinds_path)


def test_local_rule_layer_reports_conflicting_shared_rule_overrides(tmp_path: Path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.moonberry]
label = 'Moonberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[entities."TestHelper/Berry"]
rules = [{ kind = 'moonberry' }]
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(shared_path)
    store = entity_rules.EntityConfigStore(
        shared_path=shared_path, kinds_path=kinds_path, local_path=local_path
    )
    rules = store.load()

    assert rules.entity_kind('TestHelper/Berry', {}) == 'moonberry'
    assert len(store.conflicts) == 1
    conflict = store.conflicts[0]
    assert conflict.entity_name == 'TestHelper/Berry'
    assert conflict.conditions.when == ()
    assert conflict.conditions.meta == ()
    assert conflict.shared_kind == 'strawberry'
    assert conflict.local_kind == 'moonberry'


def test_shared_kind_library_is_not_written_into_shared_entity_rules(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'
""",
        encoding='utf-8',
    )
    entities_path = tmp_path / 'entities.toml'
    entities_path.write_text('', encoding='utf-8')
    store = entity_rules.EntityConfigStore(
        shared=True, shared_path=entities_path, kinds_path=kinds_path
    )

    store.save(store.load().with_rule('TestHelper/Berry', 'strawberry', {}))

    assert 'kinds' not in tomllib.loads(entities_path.read_text(encoding='utf-8'))
    assert tomllib.loads(kinds_path.read_text(encoding='utf-8'))['kinds']['strawberry'] == {
        'label': 'Strawberry',
        'parent': 'berry',
    }


def test_entity_config_store_publishes_generated_rules_to_shared_library(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'
""",
        encoding='utf-8',
    )
    shared_path = tmp_path / 'entities.toml'
    shared_path.write_text('', encoding='utf-8')
    store = entity_rules.EntityConfigStore(shared_path=shared_path, kinds_path=kinds_path)
    generated = entity_rules.EntityRuleLayer(
        entities={
            'TestHelper/Berry': entity_rules.EntityRulesForId(
                rules=(entity_rules.EntityRule(kind='berry'),)
            )
        }
    )

    store.save_generated_layer(generated)

    assert store.load_shared_layer() == generated
    assert store.load().entity_kind('TestHelper/Berry', {}) == 'berry'
