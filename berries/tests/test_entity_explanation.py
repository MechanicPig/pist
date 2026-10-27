import pytest

from berries.entities.classification import (
    EntityRuleStatus,
    classify_map_entities,
    explain_map_entities,
)
from berries.entities.rules import EntityKind, EntityRule, EntityRules, EntityRulesForId
from berries.game.binmap import AttrValue, BinElement, BinMap


@pytest.mark.parametrize('metadata', ({}, {'HeartIsEnd': False}, {'HeartIsEnd': True}))
def test_explanation_preserves_sources_rules_and_all_outcomes(
    metadata: dict[str, AttrValue],
) -> None:
    elements = (
        BinElement('Berry', {'id': 1, 'moon': True}, ()),
        BinElement('Berry', {'id': 1, 'moon': False}, ()),
        BinElement('Berry', {}, ()),
        BinElement('Berry', {'moon': 1}, ()),
        BinElement('Unknown', {'id': 2}, ()),
        BinElement('Heart', {}, ()),
    )
    room = BinElement(
        'level',
        {'name': 'first'},
        (
            BinElement('entities', {}, elements),
            BinElement('triggers', {}, (BinElement('Berry', {'moon': True}, ()),)),
        ),
    )
    other = BinElement(
        'level',
        {'name': 'second'},
        (BinElement('entities', {}, (BinElement('Unknown', {}, ()),)),),
    )
    data = BinMap(
        'Test',
        BinElement(
            'Map',
            {},
            (
                BinElement('meta', {}, (BinElement('mode', metadata, ()),)),
                BinElement('levels', {}, (room, other, BinElement('level', {}, ()))),
            ),
        ),
    )
    matched = EntityRule(kind='berry', when={'moon': True})
    excluded = EntityRule(kind=None, when={'moon': False})
    absent = EntityRule(kind='berry', missing=('moon',))
    heart = EntityRule(kind='heart', meta={'HeartIsEnd': True})
    rule_set = EntityRules(
        kinds={'berry': EntityKind(label='Berry'), 'heart': EntityKind(label='Heart')},
        entities={
            'Berry': EntityRulesForId(rules=(matched, excluded, absent)),
            'Heart': EntityRulesForId(rules=(heart,)),
        },
    )

    results = tuple(explain_map_entities(data, rule_set=rule_set))
    assert [item.room for item in results] == ['first'] * 6 + ['second']
    assert all(item.element is original for item, original in zip(results, elements))
    assert [item.status for item in results] == [
        EntityRuleStatus.MATCHED,
        EntityRuleStatus.EXCLUDED,
        EntityRuleStatus.MATCHED,
        EntityRuleStatus.UNMATCHED,
        EntityRuleStatus.UNMATCHED,
        EntityRuleStatus.MATCHED if metadata.get('HeartIsEnd') else EntityRuleStatus.UNMATCHED,
        EntityRuleStatus.UNMATCHED,
    ]
    assert results[0].rule is matched
    assert results[1].rule is excluded
    assert results[2].rule is absent
    assert results[3].rule is None
    assert results[4].rule is None
    classified = tuple(classify_map_entities(data, rule_set=rule_set))
    assert [(item.room, item.name, item.kind) for item in classified] == [
        (item.room, item.element.name, item.rule.kind)
        for item in results
        if item.status is EntityRuleStatus.MATCHED and item.rule is not None
    ]


def test_empty_map_has_no_explanations() -> None:
    data = BinMap('Empty', BinElement('Map', {}, ()))
    assert tuple(explain_map_entities(data, rule_set=EntityRules())) == ()
