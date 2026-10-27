import pytest

from berries.entities.classification import CollectedEntityRuleIssue, CollectedEntityRuleIssueStatus
from berries.entities.rules import EntityKind, EntityRule, EntityRules, EntityRulesForId
from berries.game.binmap import BinElement, BinMap
from berries.map_entity_id import MapEntityID
from pist.entities.collected import (
    UnreviewedCollectedEntity,
    collected_entity_review_issues,
)


@pytest.mark.parametrize('outcome', ('matched', 'excluded', 'unmatched'))
@pytest.mark.parametrize('needs_review', (False, True))
def test_collected_entity_review_issues_prioritize_unreviewed_variants(
    outcome: str, needs_review: bool
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
        entities={}
        if outcome == 'unmatched'
        else {
            'Known': EntityRulesForId(
                rules=(EntityRule(kind='berry' if outcome == 'matched' else None),)
            )
        },
    )

    issues = collected_entity_review_issues(
        map_data,
        frozenset({MapEntityID('room', 1)}),
        rule_set=rules,
        needs_variant_review=lambda name, _attrs, _meta: needs_review and name == 'Known',
    )

    if not needs_review and outcome == 'matched':
        assert issues == ()
        return
    if needs_review:
        assert issues == (
            UnreviewedCollectedEntity(MapEntityID('room', 1), 'Known', {'id': 1, 'moon': True}),
        )
    else:
        assert issues == (
            CollectedEntityRuleIssue(
                MapEntityID('room', 1),
                CollectedEntityRuleIssueStatus(outcome),
                'Known',
                {'id': 1, 'moon': True},
            ),
        )


def test_collected_entity_review_issues_loads_reviews_only_for_found_collected_entities() -> None:
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

    issues = collected_entity_review_issues(
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


def test_collected_entity_review_issues_rejects_conflicting_review_inputs() -> None:
    with pytest.raises(ValueError):
        collected_entity_review_issues(
            BinMap(package='Test/Map', root=BinElement(name='Map', attrs={}, children=())),
            frozenset(),
            rule_set=EntityRules(),
            needs_variant_review=lambda *_args: False,
            variant_review_loader=lambda _names: None,
        )
