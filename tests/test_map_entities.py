from pist.game.binmap import BinElement, BinMap
from pist.game.entities import (
    SHARED_ENTITIES_PATH,
    EntityKind,
    EntityRule,
    EntityRules,
    EntityRulesForId,
    EntityStat,
    EntityTableField,
    HeartStatus,
    MapEntityStats,
    analyze_map_entities,
    load_entity_rules,
)

SHARED_RULES = load_entity_rules(SHARED_ENTITIES_PATH)


def test_entity_table_values_include_absent_count_and_existence_kinds() -> None:
    stats = MapEntityStats(
        counted={},
        existing={},
        table_fields={
            'strawberry': EntityTableField(table='主表', field='红草莓数'),
            'cassette': EntityTableField(table='主表', field='磁带'),
        },
        stat_types={'strawberry': EntityStat.COUNT, 'cassette': EntityStat.EXIST},
    )

    assert stats.table_values == {'主表': {'红草莓数': 0, '磁带': False}}


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

    entities = analyze_map_entities(map_data, rules=SHARED_RULES)

    assert [entity.kind for entity in entities.strawberries] == ['strawberry']
    assert [entity.kind for entity in entities.moonberries] == ['moonberry']
    assert set(entities.special) == {'goldenberry', 'silverberry'}
    assert [entity.entity_id for entity in entities.special['goldenberry']] == [3]
    assert [entity.room for entity in entities.special['silverberry']] == ['first_room']
    assert entities.stats.count('strawberry') == 1
    assert entities.stats.count('moonberry') == 1
    assert entities.stats.exists('cassette')
    assert entities.stats.table_values == {
        '主表': {'红草莓数': 1, '月莓数': 1, '磁带': True, '水晶之心': '通关收集'}
    }
    assert entities.has_cassette
    assert entities.heart_status is HeartStatus.COMPLETES_LEVEL


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

    rules = (
        SHARED_RULES.with_rule('heartGem', 'end_level_heart', {'endLevel': True})
        .with_rule('heartGem', 'keep_going_heart', {'endLevel': False})
    )
    entities = analyze_map_entities(map_data, rules=rules)

    assert len(entities.hearts) == 2
    assert entities.heart_status is HeartStatus.NEEDS_REVIEW
    assert '水晶之心' not in entities.stats.table_values['主表']


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

    entities = analyze_map_entities(map_data, rules=rules)

    assert [heart.name for heart in entities.hearts] == ['Test/Heart']


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

    entities = analyze_map_entities(map_data, rules=rules)

    assert [(entity.kind, entity.sprite) for entity in entities.entities] == [
        ('strawberry_seed', 'strawberry-seed.png')
    ]
    assert not entities.strawberries
    assert not entities.moonberries
