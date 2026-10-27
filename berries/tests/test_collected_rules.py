"""Public pickup-rule checks do not depend on application review evidence."""

import pytest

from berries.entities.classification import (
    CollectedEntityRuleIssue,
    CollectedEntityRuleIssueStatus,
    check_collected_entity_rules,
    collected_entity_rule_issues,
)
from berries.entities.rules import EntityKind, EntityRule, EntityRules, EntityRulesForId
from berries.game.binmap import AttrValue, BinElement, BinMap
from berries.map_entity_id import MapEntityID


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


@pytest.mark.parametrize('outcome', ('matched', 'excluded', 'unmatched'))
def test_collected_checks_keep_facts_and_return_detached_issues(outcome: str) -> None:
    entity = BinElement('Berry', {'id': 1, 'moon': True}, ())
    room = BinElement('level', {'name': 'room'}, (BinElement('entities', {}, (entity,)),))
    metadata: dict[str, AttrValue] = {'HeartIsEnd': True}
    map_data = BinMap(
        'Test',
        BinElement(
            'Map',
            {},
            (
                BinElement('levels', {}, (room,)),
                BinElement('meta', {}, (BinElement('mode', metadata, ()),)),
            ),
        ),
    )
    rule = EntityRule(kind='berry' if outcome == 'matched' else None, meta=metadata)
    rules = EntityRules(
        kinds={'berry': EntityKind(label='Berry')},
        entities={} if outcome == 'unmatched' else {'Berry': EntityRulesForId(rules=(rule,))},
    )
    collected = frozenset({MapEntityID('room', 1)})
    (check,) = check_collected_entity_rules(map_data, collected, rule_set=rules)
    assert check.entity is entity
    assert check.meta is metadata
    assert check.rule is (None if outcome == 'unmatched' else rule)
    if outcome == 'matched':
        assert check.issue is None
        assert collected_entity_rule_issues(map_data, collected, rule_set=rules) == ()
    else:
        issue = check.issue
        assert issue == CollectedEntityRuleIssue(
            MapEntityID('room', 1),
            CollectedEntityRuleIssueStatus(outcome),
            'Berry',
            {'id': 1, 'moon': True},
            metadata,
        )
        assert collected_entity_rule_issues(map_data, collected, rule_set=rules) == (issue,)
        assert issue is not None and issue.attrs is not None and issue.meta is not None
        issue.attrs['moon'] = False
        issue.meta['HeartIsEnd'] = False
        assert entity.attrs['moon'] is True
        assert metadata['HeartIsEnd'] is True
