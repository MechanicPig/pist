import tomllib
from pathlib import Path

import pytest
import tomli_w

from pist.entities.rules import (
    SHARED_ENTITIES_PATH,
    EntityConfigStore,
    EntityKind,
    EntityRule,
    EntityRuleLayer,
    EntityRules,
    EntityRulesForId,
    EntityStat,
    EntityTableField,
    entity_kind,
    entity_rules_toml,
    kind_sprite,
    load_entity_rule_layers,
    load_entity_rules,
    stat_owner,
)

SHARED_RULES = load_entity_rules(SHARED_ENTITIES_PATH)


def _split_config(path: Path) -> Path:
    """Move a compact test fixture's kinds table into its required sibling file."""
    config = tomllib.loads(path.read_text(encoding='utf-8'))
    kinds = config.pop('kinds', {})
    kinds_path = path.with_name('kinds.toml')
    kinds_path.write_text(tomli_w.dumps({'kinds': kinds}), encoding='utf-8')
    path.write_text(tomli_w.dumps(config), encoding='utf-8')
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
    assert entity_kind(name, attrs, rules=SHARED_RULES) == expected


def test_stat_owner_is_inherited_from_the_kind_tree() -> None:
    assert stat_owner('strawberry', rules=SHARED_RULES) == (
        'strawberry',
        EntityStat.COUNT,
        EntityTableField(table='主表', field='红草莓数'),
    )
    assert stat_owner('moonberry', rules=SHARED_RULES) == (
        'moonberry',
        EntityStat.COUNT,
        EntityTableField(table='主表', field='月莓数'),
    )
    assert stat_owner('goldenberry', rules=SHARED_RULES) is None
    assert stat_owner('grabless_goldenberry', rules=SHARED_RULES) is None
    assert stat_owner('cassette', rules=SHARED_RULES) == (
        'cassette',
        EntityStat.EXIST,
        EntityTableField(table='主表', field='磁带'),
    )
    assert stat_owner('heart', rules=SHARED_RULES) == (
        'heart',
        EntityStat.SELECT,
        EntityTableField(table='主表', field='水晶之心'),
    )
    assert stat_owner('end_level_heart', rules=SHARED_RULES) == (
        'heart',
        EntityStat.SELECT,
        EntityTableField(table='主表', field='水晶之心'),
    )
    assert stat_owner('keep_going_heart', rules=SHARED_RULES) == (
        'heart',
        EntityStat.SELECT,
        EntityTableField(table='主表', field='水晶之心'),
    )


def test_kind_sprite_is_inherited_without_implying_statistics() -> None:
    rules = load_entity_rules(Path('src/pist/data/entities.toml'))

    assert kind_sprite('strawberry', rules=rules) == 'strawberry.png'
    assert kind_sprite('goldenberry', rules=rules) is None


def test_heart_kind_uses_configured_attribute_rules_and_default() -> None:
    rules = (
        SHARED_RULES.with_rule('heartGem', 'end_level_heart', {'endLevel': True})
        .with_rule('heartGem', 'keep_going_heart', {'endLevel': False})
        .with_rule('heartGem', 'end_level_heart', {})
    )
    assert entity_kind('heartGem', {'endLevel': True}, rules=rules) == 'end_level_heart'
    assert entity_kind('heartGem', {'endLevel': False}, rules=rules) == 'keep_going_heart'
    assert entity_kind('heartGem', {}, rules=rules) == 'end_level_heart'
    assert (
        entity_kind('ArphimigonHelper/HeartGem', {'endLevel': 'true'}, rules=rules)
        == 'end_level_heart'
    )
    assert entity_kind('fakeHeart', {'endLevel': True}, rules=rules) is None


def test_entity_rules_can_be_loaded_from_custom_config(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.testberry]
label = 'Test Berry'
parent = 'berry'
stat = 'count'
table_field = { table = 'maps', field = 'test berries' }

[entities."TestHelper/Berry"]
rules = [{ kind = 'testberry', when = { moon = true } }]
""",
        encoding='utf-8',
    )

    rules = load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert entity_kind('TestHelper/Berry', {'moon': True}, rules=rules) == 'testberry'
    assert entity_kind('TestHelper/Berry', {'moon': False}, rules=rules) is None
    assert stat_owner('testberry', rules=rules) == (
        'testberry',
        EntityStat.COUNT,
        EntityTableField(table='maps', field='test berries'),
    )


def test_entity_rules_rejects_kind_definitions_in_an_entity_rule_file(tmp_path) -> None:
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
        load_entity_rules(entities_path, kinds_path=kinds_path)


def test_entity_rules_support_metadata_conditions_and_terminal_non_collectibles(tmp_path) -> None:
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
]
""",
        encoding='utf-8',
    )

    rules = load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert entity_kind('TestHelper/Heart', {'fake': True}, rules=rules) is None
    assert (
        entity_kind('TestHelper/Heart', {'fake': False}, meta={'HeartIsEnd': True}, rules=rules)
        == 'end_level_heart'
    )
    assert entity_kind('TestHelper/Heart', {'fake': False}, rules=rules) is None

    serialized = entity_rules_toml(rules.with_exclusion('TestHelper/OtherHeart', {'fake': True}))
    assert 'exclude' not in serialized
    assert (
        EntityRuleLayer.model_validate(tomllib.loads(serialized)).entities
        == rules.with_exclusion('TestHelper/OtherHeart', {'fake': True}).entities
    )


def test_more_specific_rules_precede_defaults_regardless_of_write_order(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.strawberry]
label = 'Strawberry'
""",
        encoding='utf-8',
    )
    rules = load_entity_rules(config_path, kinds_path=_split_config(config_path)).with_rule(
        'Example/Berry', 'strawberry', {}
    )
    rules = rules.with_exclusion('Example/Berry', {'fake': True})

    assert entity_kind('Example/Berry', {'fake': True}, rules=rules) is None
    assert entity_kind('Example/Berry', {'fake': False}, rules=rules) == 'strawberry'


def test_entity_rules_reject_unknown_kind_and_parent(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.test]
label = 'Test'
parent = 'missing'

[entities."Test"]
rules = [{ kind = 'also_missing' }]
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid entity rules config'):
        load_entity_rules(config_path)


def test_entity_rules_allow_non_leaf_rule_kind(tmp_path) -> None:
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

    rules = load_entity_rules(config_path, kinds_path=_split_config(config_path))

    assert entity_kind('TestHelper/Berry', {}, rules=rules) == 'berry'


def test_adding_a_child_preserves_rules_targeting_its_new_parent() -> None:
    rules = EntityRules(
        kinds={'moonberry': EntityKind(label='月莓')},
        entities={
            'strawberry': EntityRulesForId(rules=(EntityRule(kind='moonberry'),)),
        },
    )

    updated = rules.with_kind('secretberry', '秘密草莓', parent='moonberry')

    assert entity_kind('strawberry', {}, rules=updated) == 'moonberry'
    assert 'moonberry' not in updated.leaf_kind_names


def test_entity_rules_can_add_a_kind_under_an_existing_parent(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'
""",
        encoding='utf-8',
    )
    store = EntityConfigStore(config_path, kinds_path=_split_config(config_path))

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
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='Berry'),
            'customberry': EntityKind(label='Custom Berry', parent='berry'),
        }
    )

    updated = rules.with_updated_kind(
        'customberry', 'Custom Strawberry', parent='berry', sprite='customberry.png'
    )

    assert updated.kinds['customberry'] == EntityKind(
        label='Custom Strawberry', parent='berry', sprite='customberry.png'
    )


def test_updating_kind_presentation_preserves_its_statistic_target() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='Berry'),
            'strawberry': EntityKind(
                label='Strawberry',
                parent='berry',
                stat=EntityStat.COUNT,
                table_field=EntityTableField(table='主表', field='红草莓数'),
            ),
        }
    )

    updated = rules.with_updated_kind('strawberry', '红草莓', parent='berry')

    assert updated.kinds['strawberry'].stat is EntityStat.COUNT
    assert updated.kinds['strawberry'].table_field == EntityTableField(
        table='主表', field='红草莓数'
    )


def test_entity_rules_validate_kind_hierarchy_after_an_update() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='Berry'),
            'strawberry': EntityKind(label='Strawberry', parent='berry'),
        }
    )

    with pytest.raises(ValueError, match='Circular kind parent'):
        rules.with_updated_kind('berry', 'Berry', parent='strawberry')


def test_entity_rules_can_rename_a_kind_and_its_references() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='Berry'),
            'strawberry': EntityKind(label='Strawberry', parent='berry'),
            'goldenberry': EntityKind(label='Golden Berry', parent='strawberry'),
        },
        entities={
            'strawberry': EntityRulesForId(rules=(EntityRule(kind='strawberry'),)),
            'goldenBerry': EntityRulesForId(rules=(EntityRule(kind='goldenberry'),)),
        },
    )

    renamed = rules.with_renamed_kind('strawberry', 'redberry', 'Red Berry', parent='berry')

    assert 'strawberry' not in renamed.kinds
    assert renamed.kinds['redberry'].label == 'Red Berry'
    assert renamed.kinds['goldenberry'].parent == 'redberry'
    assert renamed.entities['strawberry'].rules[0].kind == 'redberry'
    assert renamed.entities['goldenBerry'].rules[0].kind == 'goldenberry'


def test_entity_rules_can_delete_a_kind_and_fall_back_to_its_parent() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='Berry'),
            'strawberry': EntityKind(label='Strawberry', parent='berry'),
            'wingedberry': EntityKind(label='Winged Berry', parent='strawberry'),
        },
        entities={
            'strawberry': EntityRulesForId(rules=(EntityRule(kind='strawberry'),)),
        },
    )

    deleted = rules.with_deleted_kind('strawberry')

    assert deleted.direct_rule_count('strawberry') == 0
    assert deleted.entities['strawberry'].rules[0].kind == 'berry'
    assert deleted.kinds['wingedberry'].parent == 'berry'
    with pytest.raises(ValueError, match='Cannot delete root'):
        deleted.with_deleted_kind('berry')


def test_entity_config_store_renames_kinds_in_all_rule_layers(tmp_path) -> None:
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
    store = EntityConfigStore(
        shared_path=shared_path,
        kinds_path=kinds_path,
        local_path=local_path,
    )

    renamed = store.load().with_renamed_kind('strawberry', 'redberry', 'Red Berry', parent='berry')
    store.rename_kind('strawberry', 'redberry', renamed)

    loaded = store.load()
    assert loaded.kinds['goldenberry'].parent == 'redberry'
    assert entity_kind('strawberry', {}, rules=loaded) == 'redberry'
    assert entity_kind('Example/Collectible', {'moon': False}, rules=loaded) == 'redberry'
    assert entity_kind('Local/Collectible', {}, rules=loaded) == 'redberry'

    deleted = loaded.with_deleted_kind('redberry')
    store.delete_kind('redberry', 'berry', deleted)

    loaded = store.load()
    assert 'redberry' not in loaded.kinds
    assert loaded.kinds['goldenberry'].parent == 'berry'
    assert entity_kind('strawberry', {}, rules=loaded) == 'berry'
    assert entity_kind('Example/Collectible', {'moon': False}, rules=loaded) == 'berry'
    assert entity_kind('Local/Collectible', {}, rules=loaded) == 'berry'


def test_entity_rules_reject_sprite_paths_outside_sprite_directories(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'
sprite = '../outside.png'
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid entity rules config'):
        load_entity_rules(config_path)


@pytest.mark.parametrize(
    'kind_config',
    [
        "table_field = { table = 'maps', field = 'berries' }",
        "stat = 'count'\ntable_field = { table = 'maps', field = 'berries' }",
    ],
)
def test_entity_rules_reject_invalid_table_field_config(tmp_path, kind_config: str) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        f"""[kinds.berry]
label = 'Berry'
{kind_config}

[kinds.other]
label = 'Other'
stat = 'count'
table_field = {{ table = 'maps', field = 'berries' }}
""",
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid entity rules config'):
        load_entity_rules(config_path)


def test_rule_store_updates_a_template_condition_without_duplicate(tmp_path) -> None:
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
    store = EntityConfigStore(source_path, kinds_path=_split_config(source_path))

    rules = store.load().with_rule('TestHelper/Berry', 'strawberry', {'moon': True})
    store.save(rules)
    reloaded = store.load()

    assert [rule.when for rule in reloaded.entities['TestHelper/Berry'].rules] == [
        {'moon': True},
        {},
    ]
    updated = reloaded.with_rule('TestHelper/Berry', 'strawberry', {'moon': True})
    assert len(updated.entities['TestHelper/Berry'].rules) == 2


def test_local_rule_layer_adds_conditions_without_copying_shared_rules(tmp_path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'
stat = 'count'

[kinds.moonberry]
label = 'Moonberry'
parent = 'berry'
stat = 'count'

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
    store = EntityConfigStore(shared_path=shared_path, kinds_path=kinds_path, local_path=local_path)
    rules = store.load().with_rule('TestHelper/Berry', 'moonberry', {'golden': True})
    store.save(rules)

    reloaded = load_entity_rule_layers(shared_path, local_path, kinds_path=kinds_path)
    assert entity_kind('TestHelper/Berry', {}, rules=reloaded) == 'strawberry'
    assert entity_kind('TestHelper/Berry', {'moon': True}, rules=reloaded) == 'moonberry'
    assert entity_kind('TestHelper/Berry', {'golden': True}, rules=reloaded) == 'moonberry'
    assert '[kinds.strawberry]' not in local_path.read_text(encoding='utf-8')


def test_local_entity_layer_rejects_kind_definitions(tmp_path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.heart]
label = 'Heart'
stat = 'select'
table_field = { table = 'maps', field = 'heart' }
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
        load_entity_rule_layers(shared_path, local_path, kinds_path=kinds_path)


def test_local_rule_layer_reports_conflicting_shared_rule_overrides(tmp_path) -> None:
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
    store = EntityConfigStore(shared_path=shared_path, kinds_path=kinds_path, local_path=local_path)
    rules = store.load()

    assert entity_kind('TestHelper/Berry', {}, rules=rules) == 'moonberry'
    assert len(store.conflicts) == 1
    conflict = store.conflicts[0]
    assert conflict.entity_name == 'TestHelper/Berry'
    assert conflict.when == ()
    assert conflict.meta == ()
    assert conflict.shared_kind == 'strawberry'
    assert conflict.local_kind == 'moonberry'


def test_shared_kind_library_is_not_written_into_shared_entity_rules(tmp_path) -> None:
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
    store = EntityConfigStore(shared=True, shared_path=entities_path, kinds_path=kinds_path)

    store.save(store.load().with_rule('TestHelper/Berry', 'strawberry', {}))

    assert 'kinds' not in tomllib.loads(entities_path.read_text(encoding='utf-8'))
    assert tomllib.loads(kinds_path.read_text(encoding='utf-8'))['kinds']['strawberry'] == {
        'label': 'Strawberry',
        'parent': 'berry',
    }


def test_entity_config_store_publishes_generated_rules_to_shared_library(tmp_path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'
""",
        encoding='utf-8',
    )
    shared_path = tmp_path / 'entities.toml'
    shared_path.write_text('', encoding='utf-8')
    store = EntityConfigStore(shared_path=shared_path, kinds_path=kinds_path)
    generated = EntityRuleLayer(
        entities={'TestHelper/Berry': EntityRulesForId(rules=(EntityRule(kind='berry'),))}
    )

    store.save_generated_layer(generated)

    assert store.load_shared_layer() == generated
    assert entity_kind('TestHelper/Berry', {}, rules=store.load()) == 'berry'
