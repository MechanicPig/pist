import json
import sqlite3

import pytest

from pist.game.entities import entity_rules_toml
from pist.game.entity_audit import (
    AttributeAuditStatus,
    EntityAuditStatus,
    EntityAuditStore,
    EntityAuditSummary,
    ObservationQuestion,
    ObservationStatus,
    RuleCandidate,
)


def test_audit_store_preserves_raw_attributes_and_attribute_missingness(tmp_path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'entities': [
                    {
                        'entity_name': 'Example/Berry',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'entity_id': 1,
                                'attrs': {'moon': True, 'x': 8, 'y': 16},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'entity_id': 2,
                                'attrs': {'moon': False, 'tempo': 1, 'x': 24, 'y': 16},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')

    report_id = store.import_report(report_path)

    assert store.entity_summaries(report_id) == (
        EntityAuditSummary('Example/Berry', 2, 2, EntityAuditStatus.UNKNOWN, ('Maps/Test.bin',)),
    )
    detail = store.entity_detail('Example/Berry', report_id)
    assert detail.occurrences[0].attrs == {'moon': True, 'x': 8, 'y': 16}
    assert [(attribute.name, attribute.value_counts) for attribute in detail.attributes] == [
        ('moon', ((False, 1), (True, 1))),
        ('tempo', ((None, 1), (1, 1))),
    ]
    store.save_observation(
        'Example/Berry',
        {'moon': True},
        ObservationQuestion.PAUSE_MENU_COUNT,
        ObservationStatus.CONFIRMED,
        kind='moonberry',
        evidence='游戏内测试',
    )
    store.save_observation(
        'Example/Berry',
        {'moon': False, 'tempo': 1},
        ObservationQuestion.PAUSE_MENU_COUNT,
        ObservationStatus.CONFIRMED,
        kind='strawberry',
        evidence='游戏内测试',
    )
    store.save_attribute_knowledge(
        'Example/Berry',
        'moon',
        AttributeAuditStatus.AFFECTS_KIND,
        evidence='游戏内测试',
    )

    detail = store.entity_detail('Example/Berry', report_id)

    assert detail.variants[0].attrs == {'moon': False, 'tempo': 1}
    assert detail.variants[1].observations[0].kind == 'moonberry'
    assert detail.variants[1].observations[0].evidence == '游戏内测试'
    assert store.rule_candidates('Example/Berry', report_id) == (
        RuleCandidate('moonberry', {'moon': True}, {}, 1),
        RuleCandidate('strawberry', {'moon': False}, {}, 1),
    )
    store.save_attribute_knowledge(
        'Example/Berry', 'tempo', AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND
    )
    assert store.rule_candidates('Example/Berry', report_id) == (
        RuleCandidate('moonberry', {'moon': True}, {}, 1, provisional=True),
        RuleCandidate('strawberry', {'moon': False}, {}, 1, provisional=True),
    )


def test_audit_store_scopes_knowledge_to_one_entity_and_attribute(tmp_path) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.save_entity_knowledge(
        'Example/Berry',
        EntityAuditStatus.ENTITY_CANDIDATE,
        reason='需要验证暂停菜单。',
        evidence='人工审查',
    )
    store.save_attribute_knowledge(
        'Example/Berry',
        'moon',
        AttributeAuditStatus.AFFECTS_KIND,
        reason='月莓属性。',
        evidence='游戏内测试',
    )

    detail = store.entity_detail('Example/Berry')

    assert detail.status is EntityAuditStatus.ENTITY_CANDIDATE
    assert detail.reason == '需要验证暂停菜单。'
    assert detail.evidence == '人工审查'
    assert detail.attributes == ()


def test_generated_rule_layer_contains_only_complete_candidate_entities(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Berry',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {'moon': True},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {'moon': False},
                            },
                        ],
                    },
                    {
                        'entity_name': 'Example/Incomplete',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'c',
                                'attrs': {},
                            }
                        ],
                    },
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.save_entity_knowledge('Example/Berry', EntityAuditStatus.ENTITY_CANDIDATE)
    store.save_entity_knowledge('Example/Incomplete', EntityAuditStatus.ENTITY_CANDIDATE)
    store.save_attribute_knowledge('Example/Berry', 'moon', AttributeAuditStatus.AFFECTS_KIND)
    store.save_observation(
        'Example/Berry',
        {'moon': True},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='moonberry',
    )
    store.save_observation(
        'Example/Berry',
        {'moon': False},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='strawberry',
    )
    monkeypatch.setattr(
        store,
        'entity_summaries',
        lambda _: pytest.fail('Rule refresh must not aggregate navigation summaries.'),
    )

    layer = store.generated_rule_layer(report_id)

    assert [rule.model_dump() for rule in layer.entities['Example/Berry'].rules] == [
        {'kind': 'moonberry', 'when': {'moon': True}, 'meta': {}},
        {'kind': 'strawberry', 'when': {'moon': False}, 'meta': {}},
    ]
    assert 'Example/Incomplete' not in layer.entities


@pytest.mark.parametrize('legacy_status', ['collectible_candidate', 'rule_complete'])
def test_audit_store_migrates_legacy_candidate_statuses(tmp_path, legacy_status: str) -> None:
    path = tmp_path / 'audit.sqlite3'
    store = EntityAuditStore(path)
    store.save_entity_knowledge('Example/Berry', EntityAuditStatus.ENTITY_CANDIDATE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE entity_knowledge SET status = ? WHERE entity_name = 'Example/Berry'",
            (legacy_status,),
        )

    migrated = EntityAuditStore(path)

    assert migrated.entity_detail('Example/Berry').status is EntityAuditStatus.ENTITY_CANDIDATE


def test_confirm_entity_kind_preserves_raw_variants_and_suggests_default_rule(tmp_path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Collectible',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {'mode': 'one'},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {'mode': 'two', 'speed': 2},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)

    assert (
        store.confirm_entity_kind(
            'Example/Collectible',
            report_id,
            'strawberry',
            evidence='名称与位置推断',
        )
        == 2
    )

    detail = store.entity_detail('Example/Collectible', report_id)
    assert [variant.attrs for variant in detail.variants] == [
        {'mode': 'one'},
        {'mode': 'two', 'speed': 2},
    ]
    assert all(
        variant.observations[0].question is ObservationQuestion.ENTITY_CLASSIFICATION
        and variant.observations[0].kind == 'strawberry'
        and variant.observations[0].evidence == '名称与位置推断'
        for variant in detail.variants
    )
    assert store.rule_candidates('Example/Collectible', report_id) == (
        RuleCandidate('strawberry', {}, {}, 2),
    )
    confirmation = store.entity_detail('Example/Collectible', report_id).kind_confirmation
    assert confirmation is not None
    assert confirmation.kind == 'strawberry'
    assert confirmation.evidence == '名称与位置推断'

    assert store.revoke_entity_kind('Example/Collectible') == 2
    detail = store.entity_detail('Example/Collectible', report_id)
    assert detail.kind_confirmation is None
    assert all(not variant.observations for variant in detail.variants)
    assert store.rule_candidates('Example/Collectible', report_id) == ()

    store.save_attribute_knowledge(
        'Example/Collectible',
        'mode',
        AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND,
        evidence='名称与位置推断',
    )
    assert store.rule_candidates('Example/Collectible', report_id) == ()


def test_audit_store_renames_saved_kind_references(tmp_path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Collectible',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.confirm_entity_kind('Example/Collectible', report_id, 'strawberry')

    store.rename_kind('strawberry', 'redberry')

    detail = store.entity_detail('Example/Collectible', report_id)
    assert detail.kind_confirmation is not None
    assert detail.kind_confirmation.kind == 'redberry'
    assert detail.variants[0].observations[0].kind == 'redberry'


def test_legacy_whole_entity_confirmation_is_shown_without_collapsing_review(tmp_path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Heart',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {'variant': 'one'},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {'variant': 'two'},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    variants = store.entity_detail('Example/Heart', report_id).variants
    store.save_group_observation(
        'Example/Heart',
        variants,
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='end_level_heart',
        reason='旧版整实体确认',
        evidence='in_game_test',
    )

    detail = store.entity_detail('Example/Heart', report_id)
    assert detail.kind_confirmation is None
    assert detail.legacy_kind_confirmation is not None
    assert detail.legacy_kind_confirmation.kind == 'end_level_heart'


def test_non_collectible_variant_is_a_negative_rule_candidate_example(tmp_path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Heart',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {'fake': False},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {'fake': True},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.save_attribute_knowledge('Example/Heart', 'fake', AttributeAuditStatus.AFFECTS_KIND)
    store.save_observation(
        'Example/Heart',
        {'fake': False},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='end_level_heart',
    )
    store.save_observation(
        'Example/Heart',
        {'fake': True},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.NOT_COLLECTIBLE,
    )

    assert store.rule_candidates('Example/Heart', report_id) == (
        RuleCandidate('end_level_heart', {'fake': False}, {}, 1),
        RuleCandidate(None, {'fake': True}, {}, 1),
    )
    store.save_entity_knowledge('Example/Heart', EntityAuditStatus.ENTITY_CANDIDATE)
    generated = store.generated_rule_layer(report_id)
    assert generated.entities['Example/Heart'].rules[1].kind is None
    assert 'exclude' not in entity_rules_toml(generated)
    store.save_attribute_knowledge(
        'Example/Heart',
        'fake',
        AttributeAuditStatus.AFFECTS_KIND,
        default_value=False,
    )

    assert store.rule_candidates('Example/Heart', report_id) == (
        RuleCandidate('end_level_heart', {'fake': False}, {}, 1),
        RuleCandidate(None, {'fake': True}, {}, 1),
        RuleCandidate('end_level_heart', {}, {}, 1, fallback=True),
    )


def test_map_metadata_is_preserved_and_can_distinguish_rule_candidates(tmp_path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Heart',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/End.bin',
                                    'map_name': 'End',
                                    'meta': {'HeartIsEnd': True},
                                },
                                'room': 'a',
                                'attrs': {},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Extra.bin',
                                    'map_name': 'Extra',
                                    'meta': {'HeartIsEnd': False},
                                },
                                'room': 'a',
                                'attrs': {},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.save_attribute_knowledge(
        'Example/Heart', '@meta.HeartIsEnd', AttributeAuditStatus.AFFECTS_KIND
    )
    store.save_observation(
        'Example/Heart',
        {},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='end_level_heart',
        meta={'HeartIsEnd': True},
    )
    store.save_observation(
        'Example/Heart',
        {},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='keep_going_heart',
        meta={'HeartIsEnd': False},
    )

    detail = store.entity_detail('Example/Heart', report_id)

    assert detail.variants[0].meta == {'HeartIsEnd': False}
    assert detail.attributes[0].name == '@meta.HeartIsEnd'
    assert store.rule_candidates('Example/Heart', report_id) == (
        RuleCandidate('end_level_heart', {}, {'HeartIsEnd': True}, 1),
        RuleCandidate('keep_going_heart', {}, {'HeartIsEnd': False}, 1),
    )


def test_audit_store_migrates_existing_raw_occurrences_to_include_metadata(tmp_path) -> None:
    path = tmp_path / 'audit.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE raw_entity_occurrences (
                id INTEGER PRIMARY KEY,
                report_id INTEGER NOT NULL,
                entity_name TEXT NOT NULL,
                attrs_json TEXT NOT NULL,
                scope TEXT NOT NULL,
                map_file TEXT NOT NULL,
                map_name TEXT NOT NULL,
                mod_name TEXT,
                mod_file TEXT,
                package TEXT,
                room TEXT NOT NULL,
                entity_id INTEGER
            )
            """
        )

    EntityAuditStore(path)

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info(raw_entity_occurrences)')
        }
    assert 'meta_json' in columns


def test_attribute_default_value_is_saved_separately_from_missing_raw_values(tmp_path) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Heart',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {'fake': True},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )

    report_id = store.import_report(report_path)
    store.save_attribute_knowledge(
        'Example/Heart',
        'fake',
        AttributeAuditStatus.AFFECTS_KIND,
        default_value=False,
        evidence='源码分析',
    )
    detail = store.entity_detail('Example/Heart', report_id)

    assert detail.attributes[0].name == 'fake'
    assert detail.attributes[0].value_counts == ((None, 1), (True, 1))
    assert detail.attributes[0].default_value is False
    assert detail.attributes[0].evidence == '源码分析'


def test_attribute_default_can_be_confirmed_as_json_null(tmp_path) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'Example/Heart',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'a',
                                'attrs': {'fake': False},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    report_id = store.import_report(report_path)
    store.save_attribute_knowledge(
        'Example/Heart',
        'fake',
        AttributeAuditStatus.AFFECTS_KIND,
        default_value=None,
    )

    attribute = store.entity_detail('Example/Heart', report_id).attributes[0]

    assert attribute.default_value is None
