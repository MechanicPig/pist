import pytest

from pist.entities.classification import (
    CollectedEntityRuleIssue,
    CollectedEntityRuleIssueStatus,
    MapEntityStats,
    classify_map_entities,
    collected_entity_rule_issues,
    map_entity_stats,
)
from pist.entities.map_entity_id import MapEntityID
from pist.entities.rules import (
    SHARED_ENTITIES_PATH,
    EntityKind,
    EntityRule,
    EntityRules,
    EntityRulesForId,
    EntityStat,
    EntityTableField,
    load_entity_rules,
)
from pist.game.binmap import BinElement, BinMap

SHARED_RULES = load_entity_rules(SHARED_ENTITIES_PATH)


def test_entity_record_values_include_absent_count_and_existence_kinds() -> None:
    stats = MapEntityStats(
        counts={},
        existing_kinds=frozenset(),
        table_fields={
            'strawberry': EntityTableField(table='主表', field='红草莓数'),
            'cassette': EntityTableField(table='主表', field='磁带'),
        },
        stat_types={'strawberry': EntityStat.COUNT, 'cassette': EntityStat.EXIST},
    )

    assert stats.record_values == {'主表': {'红草莓数': 0, '磁带': False}}


def test_map_entities_classifies_configured_entities_with_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        'pist.entities.classification._classified_entity',
        lambda *_args: pytest.fail('Statistics must not construct preview entities.'),
    )
    monkeypatch.setattr(
        'pist.entities.classification.rules.kind_sprite',
        lambda *_args, **_kwargs: pytest.fail('Statistics must not resolve preview sprites.'),
    )
    stats = map_entity_stats(map_data, rule_set=SHARED_RULES)

    assert stats.count('strawberry') == 1
    assert stats.count('moonberry') == 1
    assert [entity.entity_id for entity in entities if entity.kind == 'goldenberry'] == [3]
    assert [entity.room for entity in entities if entity.kind == 'silverberry'] == ['first_room']
    assert stats.exists('cassette')
    assert stats.has_stat_kind('heart')
    assert stats.record_values == {
        '主表': {'红草莓数': 1, '月莓数': 1, '磁带': True, '水晶之心': '通关收集'}
    }


def test_collected_entity_rule_issues_distinguish_unmatched_excluded_and_missing() -> None:
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
                                        BinElement(name='Known', attrs={'id': 1}, children=()),
                                        BinElement(name='Excluded', attrs={'id': 2}, children=()),
                                        BinElement(
                                            name='Unknown',
                                            attrs={'id': 3, 'moon': True},
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
    rules = EntityRules(
        kinds={'berry': EntityKind(label='Berry')},
        entities={
            'Known': EntityRulesForId(rules=(EntityRule(kind='berry'),)),
            'Excluded': EntityRulesForId(rules=(EntityRule(kind=None),)),
        },
    )

    issues = collected_entity_rule_issues(
        map_data,
        frozenset(
            {
                MapEntityID('room', 1),
                MapEntityID('room', 2),
                MapEntityID('room', 3),
                MapEntityID('removed-room', 4),
            }
        ),
        rule_set=rules,
    )

    assert [(issue.collected_id, issue.status, issue.entity_name) for issue in issues] == [
        (MapEntityID('removed-room', 4), CollectedEntityRuleIssueStatus.NOT_FOUND, None),
        (MapEntityID('room', 2), CollectedEntityRuleIssueStatus.EXCLUDED, 'Excluded'),
        (MapEntityID('room', 3), CollectedEntityRuleIssueStatus.UNMATCHED, 'Unknown'),
    ]
    assert issues[-1].attrs == {'id': 3, 'moon': True}


def test_collected_entity_rule_issues_surface_unreviewed_variants_before_matched_rules() -> None:
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
                                            name='Known', attrs={'id': 1, 'moon': True}, children=()
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
    rules = EntityRules(
        kinds={'berry': EntityKind(label='Berry')},
        entities={'Known': EntityRulesForId(rules=(EntityRule(kind='berry'),))},
    )

    issues = collected_entity_rule_issues(
        map_data,
        frozenset({MapEntityID('room', 1)}),
        rule_set=rules,
        needs_variant_review=lambda name, _attrs, _meta: name == 'Known',
    )

    assert issues == (
        CollectedEntityRuleIssue(
            MapEntityID('room', 1),
            CollectedEntityRuleIssueStatus.UNREVIEWED_VARIANT,
            'Known',
            {'id': 1, 'moon': True},
        ),
    )


def test_collected_entity_rule_issues_loads_reviews_only_for_found_collected_entities() -> None:
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
                                        BinElement(name='Known', attrs={'id': 1}, children=()),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    rules = EntityRules(
        kinds={'berry': EntityKind(label='Berry')},
        entities={'Known': EntityRulesForId(rules=(EntityRule(kind='berry'),))},
    )
    loaded_names: list[frozenset[str]] = []

    issues = collected_entity_rule_issues(
        map_data,
        frozenset({MapEntityID('missing-room', 2), MapEntityID('room', 1)}),
        rule_set=rules,
        variant_review_loader=lambda names: loaded_names.append(names) or None,
    )

    assert loaded_names == [frozenset({'Known'})]
    assert issues == (
        CollectedEntityRuleIssue(
            MapEntityID('missing-room', 2), CollectedEntityRuleIssueStatus.NOT_FOUND
        ),
    )


def test_collected_entity_rule_issues_rejects_conflicting_review_inputs() -> None:
    with pytest.raises(ValueError):
        collected_entity_rule_issues(
            BinMap(package='Test/Map', root=BinElement(name='Map', attrs={}, children=())),
            frozenset(),
            rule_set=EntityRules(),
            needs_variant_review=lambda *_args: False,
            variant_review_loader=lambda _names: None,
        )


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
    stats = map_entity_stats(map_data, rule_set=rules)

    assert 'heart' in stats.selected_kinds
    assert '水晶之心' not in stats.record_values['主表']
    assert stats.select_conflicts[0].table_field.field == '水晶之心'
    assert stats.select_conflicts[0].values == frozenset({'通关收集', '额外收集'})


def test_map_entities_passes_map_metadata_to_rules() -> None:
    rules = EntityRules(
        kinds={'heart': EntityKind(label='Heart', stat=EntityStat.EXIST)},
        entities={
            'Test/Heart': EntityRulesForId(
                rules=(EntityRule(kind='heart', meta={'HeartIsEnd': True}),)
            )
        },
    )
    map_data = BinMap(
        package='Test/Map',
        root=BinElement(
            name='Map',
            attrs={},
            children=(
                BinElement(
                    name='meta',
                    attrs={'TitleBaseColor': 'ffffff'},
                    children=(BinElement(name='mode', attrs={'HeartIsEnd': True}, children=()),),
                ),
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
                                        BinElement(name='Test/Heart', attrs={}, children=()),
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

    assert [entity.name for entity in entities] == ['Test/Heart']


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
    stats = map_entity_stats(map_data, rule_set=rules)
    assert stats.count('strawberry') == 0
    assert stats.count('moonberry') == 0
