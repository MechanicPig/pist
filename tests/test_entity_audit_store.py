import json
from collections.abc import Collection

import pytest

from pist.entities.audit import (
    AttrAuditStatus,
    AuditMapOccurrences,
    AuditSource,
    EntityAuditStatus,
    EntityAuditStore,
    EntityAuditSummary,
    ObservationQuestion,
    ObservationStatus,
    RuleCandidate,
)
from pist.entities.audit import store as audit_store
from pist.entities.audit.models import VariantKey
from pist.entities.audit.store import _attrs
from pist.entities.classification import VariantReview
from pist.entities.rules import entity_rules_toml
from pist.game.binmap import AttrValue
from pist.game.map_source import MapSource


def test_variant_key_rejects_unknown_persisted_fields() -> None:
    with pytest.raises(ValueError):
        VariantKey.model_validate({'attrs': {}, 'meta': {}, 'unexpected': True})


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
                                'room': 'a',
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
        EntityAuditSummary('Example/Berry', 2, None, EntityAuditStatus.UNKNOWN, ('Maps/Test.bin',)),
    )
    detail = store.entity_detail('Example/Berry', report_id)
    assert store.occurrences('Example/Berry', report_id)[0].attrs == {'moon': True, 'x': 8, 'y': 16}
    assert store.occurrence_maps('Example/Berry', report_id) == (
        AuditMapOccurrences(
            AuditSource(scope=MapSource.MOD, map_file='Maps/Test.bin', map_name='Test', meta={}),
            (('a', 2),),
        ),
    )
    moon_variant = next(variant for variant in detail.variants if variant.attrs['moon'] is True)
    assert store.occurrence_maps('Example/Berry', report_id, (moon_variant,))[0].rooms == (
        ('a', 1),
    )
    assert [(attribute.name, attribute.value_counts) for attribute in detail.attr_summaries] == [
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
    store.save_attr_knowledge(
        'Example/Berry',
        'moon',
        AttrAuditStatus.AFFECTS_KIND,
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
    store.save_attr_knowledge('Example/Berry', 'tempo', AttrAuditStatus.LIKELY_NOT_AFFECT_KIND)
    assert store.rule_candidates('Example/Berry', report_id) == (
        RuleCandidate('moonberry', {'moon': True}, {}, 1, provisional=True),
        RuleCandidate('strawberry', {'moon': False}, {}, 1, provisional=True),
    )


@pytest.mark.parametrize('value', ('[]', '{"value": []}', '{"value": null}'))
def test_attribute_json_rejects_values_outside_the_entity_attribute_domain(value: str) -> None:
    with pytest.raises(TypeError, match='serialized entity attributes'):
        _attrs(value)


def test_audit_store_scopes_knowledge_to_one_entity_and_attribute(tmp_path) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.save_entity_knowledge(
        'Example/Berry',
        EntityAuditStatus.ENTITY_CANDIDATE,
        reason='需要验证暂停菜单。',
        evidence='人工审查',
    )
    store.save_attr_knowledge(
        'Example/Berry',
        'moon',
        AttrAuditStatus.AFFECTS_KIND,
        reason='月莓属性。',
        evidence='游戏内测试',
    )

    detail = store.entity_detail('Example/Berry')

    assert detail.status is EntityAuditStatus.ENTITY_CANDIDATE
    assert detail.reason == '需要验证暂停菜单。'
    assert detail.evidence == '人工审查'
    assert detail.attr_summaries == ()


def test_generated_rule_layer_contains_only_complete_candidate_entities(
    tmp_path, monkeypatch
) -> None:
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
    store.save_attr_knowledge('Example/Berry', 'moon', AttrAuditStatus.AFFECTS_KIND)
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
        {
            'kind': 'moonberry',
            'when': {'moon': True},
            'meta': {},
            'missing': (),
            'missing_meta': (),
        },
        {
            'kind': 'strawberry',
            'when': {'moon': False},
            'meta': {},
            'missing': (),
            'missing_meta': (),
        },
    ]
    assert 'Example/Incomplete' not in layer.entities


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
    store.save_attr_knowledge(
        'Example/Collectible',
        'mode',
        AttrAuditStatus.LIKELY_NOT_AFFECT_KIND,
        evidence='名称与位置推断',
    )
    assert store.rule_candidates('Example/Collectible', report_id) == ()


def test_audit_report_marks_new_classification_variants_after_terminal_review(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial_path = tmp_path / 'initial.json'
    initial_path.write_text(
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
                                'attrs': {
                                    'moon': False,
                                    'order': 1,
                                    'appearance': 'red',
                                    'behavior': 'one',
                                },
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    initial_report_id = store.import_report(initial_path)
    store.confirm_entity_kind('Example/Berry', initial_report_id, 'strawberry')
    store.save_attr_knowledge('Example/Berry', 'order', AttrAuditStatus.LIKELY_NOT_AFFECT_KIND)
    store.save_attr_knowledge('Example/Berry', 'appearance', AttrAuditStatus.DOES_NOT_AFFECT_KIND)
    store.save_attr_knowledge('Example/Berry', 'behavior', AttrAuditStatus.AFFECTS_BEHAVIOR)

    updated_path = tmp_path / 'updated.json'
    updated_path.write_text(
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
                                'attrs': {
                                    'moon': False,
                                    'order': 2,
                                    'appearance': 'blue',
                                    'behavior': 'two',
                                },
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {
                                    'moon': True,
                                    'order': 1,
                                    'appearance': 'red',
                                    'behavior': 'one',
                                },
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'c',
                                'attrs': {
                                    'x': 8,
                                    'y': 16,
                                    'moon': True,
                                    'order': 1,
                                    'appearance': 'red',
                                    'behavior': 'one',
                                },
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    updated_report_id = store.import_report(updated_path)

    assert store.unreviewed_variant_count('Example/Berry', updated_report_id) == 1
    assert not store.needs_variant_review(
        'Example/Berry',
        {
            'id': 99,
            'x': 16,
            'moon': False,
            'order': 2,
            'appearance': 'blue',
            'behavior': 'two',
        },
        {},
    )
    assert store.needs_variant_review(
        'Example/Berry',
        {'moon': True, 'order': 1, 'appearance': 'red', 'behavior': 'one'},
        {},
    )
    checker = store.variant_review_checker({'Example/Berry'})
    monkeypatch.setattr(store, '_connect', lambda: pytest.fail('Snapshot must not reopen SQLite.'))
    assert not checker(
        'Example/Berry',
        {
            'id': 99,
            'x': 16,
            'moon': False,
            'order': 2,
            'appearance': 'blue',
            'behavior': 'two',
        },
        {},
    )
    assert checker(
        'Example/Berry',
        {'moon': True, 'order': 1, 'appearance': 'red', 'behavior': 'one'},
        {},
    )


def test_unreviewed_variant_count_deduplicates_location_only_variants(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    initial_path = tmp_path / 'initial.json'
    initial_path.write_text(
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
                                'attrs': {'moon': False},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    initial_report_id = store.import_report(initial_path)
    store.confirm_entity_kind('Example/Berry', initial_report_id, 'strawberry')

    updated_path = tmp_path / 'updated.json'
    updated_path.write_text(
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
                                'room': f'room-{index}',
                                'attrs': {'moon': True, 'x': index * 8, 'y': 16},
                            }
                            for index in range(12)
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    updated_report_id = store.import_report(updated_path)

    attrs = audit_store._attrs
    attrs_calls = 0

    def count_attrs(value: str) -> dict[str, AttrValue]:
        nonlocal attrs_calls
        attrs_calls += 1
        return attrs(value)

    monkeypatch.setattr(audit_store, '_attrs', count_attrs)

    assert store.unreviewed_variant_count('Example/Berry', updated_report_id) == 1
    assert attrs_calls <= 4


def test_empty_variant_review_checker_skips_database(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EntityAuditStore(tmp_path / 'entity-audit.sqlite3')
    monkeypatch.setattr(
        store, '_connect', lambda: pytest.fail('Empty snapshot must not open SQLite.')
    )

    assert not store.variant_review_checker(frozenset())('Example/Berry', {}, {})


def test_needs_variant_review_scopes_its_snapshot(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EntityAuditStore(tmp_path / 'entity-audit.sqlite3')
    queried_names: list[set[str]] = []

    def checker(entity_names: Collection[str] | None = None) -> VariantReview:
        assert entity_names is not None
        queried_names.append(set(entity_names))
        return lambda *_args: False

    monkeypatch.setattr(store, 'variant_review_checker', checker)

    assert not store.needs_variant_review('Example/Berry', {}, {})
    assert queried_names == [{'Example/Berry'}]


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
    store.save_attr_knowledge('Example/Heart', 'fake', AttrAuditStatus.AFFECTS_KIND)
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
    store.save_attr_knowledge(
        'Example/Heart',
        'fake',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
    )

    assert store.rule_candidates('Example/Heart', report_id) == (
        RuleCandidate('end_level_heart', {'fake': False}, {}, 1),
        RuleCandidate(None, {'fake': True}, {}, 1),
        RuleCandidate('end_level_heart', {}, {}, 1, fallback=True),
    )


def test_rule_candidates_keep_affecting_attributes_without_competing_kinds(tmp_path) -> None:
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
                                'attrs': {'moon': False},
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.save_attr_knowledge('Example/Berry', 'moon', AttrAuditStatus.AFFECTS_KIND)
    store.save_observation(
        'Example/Berry',
        {'moon': False},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='strawberry',
    )
    store.save_observation(
        'Example/Berry',
        {},
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='strawberry',
    )

    assert store.rule_candidates('Example/Berry', report_id) == (
        RuleCandidate('strawberry', {'moon': False}, {}, 1),
        RuleCandidate('strawberry', {}, {}, 1, missing=('moon',)),
    )
    store.save_attr_knowledge(
        'Example/Berry',
        'moon',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
    )

    assert store.rule_candidates('Example/Berry', report_id) == (
        RuleCandidate('strawberry', {'moon': False}, {}, 1),
        RuleCandidate('strawberry', {}, {}, 1, missing=('moon',)),
    )


def test_rule_candidates_do_not_broaden_default_when_missing_is_confirmed(tmp_path) -> None:
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
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'c',
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
    store.save_attr_knowledge(
        'Example/Berry',
        'moon',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
    )
    observations: tuple[tuple[dict[str, AttrValue], str], ...] = (
        ({'moon': True}, 'moonberry'),
        ({'moon': False}, 'strawberry'),
        ({}, 'strawberry'),
    )
    for attrs, kind in observations:
        store.save_observation(
            'Example/Berry',
            attrs,
            ObservationQuestion.ENTITY_CLASSIFICATION,
            ObservationStatus.CONFIRMED,
            kind=kind,
        )

    assert store.rule_candidates('Example/Berry', report_id) == (
        RuleCandidate('moonberry', {'moon': True}, {}, 1),
        RuleCandidate('strawberry', {'moon': False}, {}, 1),
        RuleCandidate('strawberry', {}, {}, 1, missing=('moon',)),
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
    store.save_attr_knowledge('Example/Heart', '@meta.HeartIsEnd', AttrAuditStatus.AFFECTS_KIND)
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
    assert detail.attr_summaries[0].name == '@meta.HeartIsEnd'
    assert store.rule_candidates('Example/Heart', report_id) == (
        RuleCandidate('end_level_heart', {}, {'HeartIsEnd': True}, 1),
        RuleCandidate('keep_going_heart', {}, {'HeartIsEnd': False}, 1),
    )


def test_attr_default_value_is_saved_separately_from_missing_raw_values(tmp_path) -> None:
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
    store.save_attr_knowledge(
        'Example/Heart',
        'fake',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
        evidence='源码分析',
    )
    detail = store.entity_detail('Example/Heart', report_id)

    assert detail.attr_summaries[0].name == 'fake'
    assert detail.attr_summaries[0].value_counts == ((None, 1), (True, 1))
    assert detail.attr_summaries[0].default_value is False
    assert detail.attr_summaries[0].evidence == '源码分析'


def test_attr_default_can_be_confirmed_as_json_null(tmp_path) -> None:
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
    store.save_attr_knowledge(
        'Example/Heart',
        'fake',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=None,
    )

    attribute = store.entity_detail('Example/Heart', report_id).attr_summaries[0]

    assert attribute.default_value is None
