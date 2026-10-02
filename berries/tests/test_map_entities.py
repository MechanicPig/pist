import pytest

from berries.entities.classification import (
    CollectedEntityRuleIssue,
    CollectedEntityRuleIssueStatus,
    classify_map_entities,
    collected_entity_rule_issues,
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
from berries.map_entity_id import MapEntityID

SHARED_RULES = load_entity_rules(SHARED_ENTITIES_PATH)


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


def test_map_entities_passes_map_metadata_to_rules() -> None:
    rules = EntityRules(
        kinds={'heart': EntityKind(label='Heart')},
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
