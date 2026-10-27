from berries.entities.classification import classify_map_entities
from berries.entities.rules import (
    EntityKind,
    EntityRule,
    EntityRules,
    EntityRulesForId,
)
from berries.game.binmap import BinElement, BinMap


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
