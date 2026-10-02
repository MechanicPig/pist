import pytest

from berries.entities.classification import (
    classify_map_entities,
)
from berries.entities.rules import (
    SHARED_ENTITIES_PATH,
    EntityKind,
    EntityRule,
    EntityRules,
    EntityRulesForId,
    load_entity_rules,
)
from berries.game.binmap import BinElement, BinMap
from pist.entity_stats import (
    EntityStatAggregation,
    EntityStatRule,
    EntityStatRules,
    MapEntityStats,
    load_entity_stat_rules,
    map_entity_stats,
)

SHARED_RULES = load_entity_rules(SHARED_ENTITIES_PATH)
STAT_RULES = load_entity_stat_rules(SHARED_RULES)


def test_entity_record_values_include_absent_count_and_existence_kinds() -> None:
    stats = MapEntityStats(
        rules=EntityStatRules(
            stats={
                'strawberry': EntityStatRule(
                    kind='strawberry',
                    aggregation=EntityStatAggregation.COUNT,
                    table='主表',
                    field='红草莓数',
                ),
                'cassette': EntityStatRule(
                    kind='cassette',
                    aggregation=EntityStatAggregation.EXIST,
                    table='主表',
                    field='磁带',
                ),
            }
        )
    )

    assert stats.record_values == {'主表': {'红草莓数': 0, '磁带': False}}


def test_personal_stat_rules_reject_removed_public_kind(tmp_path) -> None:
    path = tmp_path / 'stats.toml'
    path.write_text(
        """
[stats.missing]
kind = 'removed_kind'
aggregation = 'count'
table = 'main'
field = 'count'
""".strip(),
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='Invalid personal entity statistic rules'):
        load_entity_stat_rules(SHARED_RULES, path)


def test_personal_stat_rules_reject_duplicate_record_target() -> None:
    with pytest.raises(ValueError, match='configured by both'):
        EntityStatRules(
            stats={
                'first': EntityStatRule(
                    kind='strawberry',
                    aggregation=EntityStatAggregation.COUNT,
                    table='main',
                    field='value',
                ),
                'second': EntityStatRule(
                    kind='cassette',
                    aggregation=EntityStatAggregation.EXIST,
                    table='main',
                    field='value',
                ),
            }
        )


def test_personal_select_values_must_remain_inside_stat_kind_subtree() -> None:
    rules = EntityStatRules(
        stats={
            'heart': EntityStatRule(
                kind='heart',
                aggregation=EntityStatAggregation.SELECT,
                table='main',
                field='heart',
                values={'cassette': 'invalid'},
            )
        }
    )

    with pytest.raises(ValueError, match='outside the .* subtree'):
        rules.validate_kinds(SHARED_RULES)


def test_map_entities_classifies_configured_entities_with_sources() -> None:
    map_data = BinMap(
        package='Test/Map',
        root=BinElement(
            name='Map',
            attrs={},
            children=(
                BinElement(
                    name='levels',
                    attrs={},
                    children=(
                        BinElement(
                            name='level',
                            attrs={'name': 'first_room'},
                            children=(
                                BinElement(
                                    name='entities',
                                    attrs={},
                                    children=(
                                        BinElement(
                                            name='strawberry',
                                            attrs={'id': 1, 'x': 8, 'y': 16},
                                            children=(),
                                        ),
                                        BinElement(
                                            name='strawberry',
                                            attrs={'id': 2, 'x': 16, 'y': 24, 'moon': True},
                                            children=(),
                                        ),
                                        BinElement(
                                            name='goldenBerry',
                                            attrs={'id': 3, 'x': 24, 'y': 32},
                                            children=(),
                                        ),
                                        BinElement(
                                            name='CollabUtils2/SilverBerry',
                                            attrs={'id': 4, 'x': 32, 'y': 40},
                                            children=(),
                                        ),
                                        BinElement(
                                            name='cassette',
                                            attrs={'id': 5, 'x': 40, 'y': 48},
                                            children=(),
                                        ),
                                        BinElement(
                                            name='ArphimigonHelper/HeartGem',
                                            attrs={'id': 6, 'x': 48, 'y': 56, 'endLevel': True},
                                            children=(),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    entities = tuple(classify_map_entities(map_data, rule_set=SHARED_RULES))
    stats = map_entity_stats(
        map_data,
        entity_rules=SHARED_RULES,
        stat_rules=STAT_RULES,
    )

    assert stats.count('strawberry') == 1
    assert stats.count('moonberry') == 1
    assert [entity.entity_id for entity in entities if entity.kind == 'goldenberry'] == [3]
    assert [entity.room for entity in entities if entity.kind == 'silverberry'] == ['first_room']
    assert stats.exists('cassette')
    assert stats.has_stat('heart')
    assert stats.record_values == {
        '主表': {'红草莓数': 1, '月莓数': 1, '磁带': True, '水晶之心': '通关收集'}
    }


def test_map_entities_marks_ambiguous_heart_outcomes_for_review() -> None:
    map_data = BinMap(
        package='Test/Map',
        root=BinElement(
            name='Map',
            attrs={},
            children=(
                BinElement(
                    name='levels',
                    attrs={},
                    children=(
                        BinElement(
                            name='level',
                            attrs={'name': 'room'},
                            children=(
                                BinElement(
                                    name='entities',
                                    attrs={},
                                    children=(
                                        BinElement(
                                            name='heartGem',
                                            attrs={'endLevel': True},
                                            children=(),
                                        ),
                                        BinElement(
                                            name='heartGem',
                                            attrs={'endLevel': False},
                                            children=(),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    rules = SHARED_RULES.with_rule('heartGem', 'end_level_heart', {'endLevel': True}).with_rule(
        'heartGem', 'keep_going_heart', {'endLevel': False}
    )
    stats = map_entity_stats(map_data, entity_rules=rules, stat_rules=STAT_RULES)

    assert 'heart' in stats.selected_stats
    assert '水晶之心' not in stats.record_values['主表']
    assert stats.select_conflicts[0].rule.field == '水晶之心'
    assert stats.select_conflicts[0].values == frozenset({'通关收集', '额外收集'})


def test_unstatistical_entity_remains_available_to_map_preview() -> None:
    rules = EntityRules(
        kinds={'strawberry_seed': EntityKind(label='草莓籽', sprite='strawberry-seed.png')},
        entities={'strawberrySeed': EntityRulesForId(rules=(EntityRule(kind='strawberry_seed'),))},
    )
    map_data = BinMap(
        package='Test/Map',
        root=BinElement(
            name='Map',
            attrs={},
            children=(
                BinElement(
                    name='levels',
                    attrs={},
                    children=(
                        BinElement(
                            name='level',
                            attrs={'name': 'room'},
                            children=(
                                BinElement(
                                    name='entities',
                                    attrs={},
                                    children=(
                                        BinElement(
                                            name='strawberrySeed',
                                            attrs={'x': 8, 'y': 16},
                                            children=(),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    entities = classify_map_entities(map_data, rule_set=rules)

    assert [(entity.kind, entity.sprite) for entity in entities] == [
        ('strawberry_seed', 'strawberry-seed.png')
    ]
    stats = map_entity_stats(
        map_data,
        entity_rules=rules,
        stat_rules=EntityStatRules(),
    )
    assert stats.count('strawberry') == 0
    assert stats.count('moonberry') == 0
