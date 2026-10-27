import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pytest
from pydantic import ValidationError

from berries.entities.config import (
    load_default_entity_rules,
)
from berries.entities.rules import (
    EntityKindLayer,
    EntityRule,
    EntityRules,
)
from berries.entities.serialization import entity_rules_toml
from berries.game.binmap import AttrValue
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
from pist.entities.audit.models import AuditGroup, AuditOccurrence, AuditReport, VariantKey
from pist.entities.collected import VariantReview
from pist.ui.entities.audit.classification import classification_groups


@pytest.mark.parametrize('scope', ('vanilla', 'mod'))
def test_audit_source_scope_is_plain_string(scope: str) -> None:
    source = AuditSource.model_validate(
        {'scope': scope, 'map_file': 'Maps/Test.bin', 'map_name': 'Test'}
    )
    assert type(source.scope) is str
    assert source.model_dump()['scope'] == scope


def test_audit_source_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError, match='scope'):
        AuditSource.model_validate(
            {'scope': 'unknown', 'map_file': 'Maps/Test.bin', 'map_name': 'Test'}
        )


@pytest.mark.parametrize('first,second', ((False, 0), (True, 1), (1, 1.0)))
@pytest.mark.parametrize('metadata', (False, True))
def test_classification_evidence_preserves_scalar_types(
    tmp_path: Path, first: AttrValue, second: AttrValue, metadata: bool
) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test')
    occurrences = tuple(
        AuditOccurrence(
            source=source.model_copy(update={'meta': {'flag': value}}) if metadata else source,
            room='a',
            attrs={} if metadata else {'flag': value},
        )
        for value in (first, second)
    )
    store.collect_entities((AuditGroup(entity_name='Example/Berry', occurrences=occurrences),))
    store.save_entity_knowledge('Example/Berry', EntityAuditStatus.ENTITY_CANDIDATE)
    store.save_attr_knowledge(
        'Example/Berry', '@meta.flag' if metadata else 'flag', AttrAuditStatus.AFFECTS_KIND
    )
    store.save_observation(
        'Example/Berry',
        occurrences[0].attrs,
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='strawberry',
        meta=occurrences[0].source.meta,
    )
    detail = store.entity_detail('Example/Berry')
    assert len(classification_groups(detail)) == 2
    assert sum(variant.classification is None for variant in detail.variants) == 1
    assert store.needs_variant_review(
        'Example/Berry', occurrences[1].attrs, occurrences[1].source.meta
    )
    assert store.rule_generation_result().incomplete_entities == ('Example/Berry',)
    for occurrence, kind in zip(occurrences, ('strawberry', 'moonberry'), strict=True):
        store.save_observation(
            'Example/Berry',
            occurrence.attrs,
            ObservationQuestion.ENTITY_CLASSIFICATION,
            ObservationStatus.CONFIRMED,
            kind=kind,
            meta=occurrence.source.meta,
        )
    result = store.rule_generation_result()
    assert not result.incomplete_entities
    rules = EntityRules.from_layers(
        EntityKindLayer(kinds=load_default_entity_rules().kinds), result.layer
    )
    for occurrence, kind in zip(occurrences, ('strawberry', 'moonberry'), strict=True):
        assert (
            rules.entity_kind('Example/Berry', occurrence.attrs, meta=occurrence.source.meta)
            == kind
        )


def test_encountered_variants_accumulate_without_resetting_reviews(tmp_path: Path) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test')
    old = AuditOccurrence(source=source, room='a', entity_id=1, attrs={'moon': False})
    report = AuditReport(
        entities=(
            AuditGroup(entity_name='Example/Berry', occurrences=(old,)),
            AuditGroup(entity_name='Other/Entity', occurrences=(old,)),
        )
    )
    assert store.collect_entities(report.entities) == 2
    store.confirm_entity_kind('Example/Berry', 'strawberry')
    new = old.model_copy(update={'attrs': {'moon': True, 'delay': 0.29999998211860657}})
    additions = AuditReport(
        entities=(AuditGroup(entity_name='Example/Berry', occurrences=(new, new)),)
    )
    assert store.collect_entities(additions.entities) == 1
    assert len(store.entity_detail('Example/Berry').variants) == 2
    assert {row.entity_name for row in store.entity_summaries()} == {
        'Example/Berry',
        'Other/Entity',
    }
    assert store.unreviewed_variant_count('Example/Berry') == 1
    assert store.needs_variant_review('Example/Berry', new.attrs, {})
    assert not store.needs_variant_review('Example/Berry', old.attrs, {})
    assert store.collect_entities(additions.entities) == 0
    assert len(store.occurrences('Example/Berry')) == 2
    store.save_observation(
        'Example/Berry',
        new.attrs,
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='moonberry',
    )
    assert not store.needs_variant_review('Example/Berry', new.attrs, {})


def test_variant_key_rejects_unknown_persisted_fields() -> None:
    with pytest.raises(ValueError):
        VariantKey.model_validate({'attrs': {}, 'meta': {}, 'unexpected': True})


@pytest.mark.parametrize(
    'irrelevant_status',
    (
        AttrAuditStatus.DOES_NOT_AFFECT_KIND,
        AttrAuditStatus.LIKELY_NOT_AFFECT_KIND,
        AttrAuditStatus.AFFECTS_BEHAVIOR,
    ),
)
def test_classification_coverage_survives_equivalent_variants_and_rejects_conflicts(
    tmp_path: Path, irrelevant_status: AttrAuditStatus
) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = AuditSource(
        scope='mod', map_file='Maps/Test.bin', map_name='Test', meta={'HeartIsEnd': False}
    )
    old = AuditOccurrence(source=source, room='a', attrs={'delay': 1.0})
    old_false = old.model_copy(update={'attrs': {'delay': 1.0, 'moon': False}})
    store.collect_entities((AuditGroup(entity_name='Example/Berry', occurrences=(old, old_false)),))
    store.save_entity_knowledge('Example/Berry', EntityAuditStatus.ENTITY_CANDIDATE)
    for occurrence in (old, old_false):
        store.save_observation(
            'Example/Berry',
            occurrence.attrs,
            ObservationQuestion.ENTITY_CLASSIFICATION,
            ObservationStatus.CONFIRMED,
            kind='strawberry',
            meta=source.meta,
        )
    for name in ('delay', '@meta.HeartIsEnd'):
        store.save_attr_knowledge('Example/Berry', name, irrelevant_status)
    store.save_attr_knowledge('Example/Berry', 'moon', AttrAuditStatus.AFFECTS_KIND)
    new_source = source.model_copy(update={'meta': {'HeartIsEnd': True}})
    added = old.model_copy(update={'source': new_source, 'attrs': {'delay': 2.0}})
    added_false = added.model_copy(update={'attrs': {'delay': 2.0, 'moon': False}})
    store.collect_entities(
        (AuditGroup(entity_name='Example/Berry', occurrences=(added, added_false)),)
    )
    detail = store.entity_detail('Example/Berry')
    groups = classification_groups(detail)
    assert all(
        group.classification == (ObservationStatus.CONFIRMED, 'strawberry') for group in groups
    )
    assert all(variant.classification is not None for variant in detail.variants)
    assert any(
        not variant.observations and variant.equivalent_observations for variant in detail.variants
    )
    assert store.unreviewed_variant_count('Example/Berry') == 0
    assert not store.needs_variant_review('Example/Berry', added_false.attrs, new_source.meta)
    assert store.rule_candidates('Example/Berry')
    new_moon = added.model_copy(update={'attrs': {'delay': 2.0, 'moon': True}})
    store.collect_entities((AuditGroup(entity_name='Example/Berry', occurrences=(new_moon,)),))
    groups = classification_groups(store.entity_detail('Example/Berry'))
    assert next(group for group in groups if group.attrs['moon'] is True).classification is None
    assert all(
        group.classification is not None for group in groups if group.attrs['moon'] is not True
    )
    assert store.unreviewed_variant_count('Example/Berry') == 1
    assert store.needs_variant_review('Example/Berry', new_moon.attrs, new_source.meta)
    assert store.rule_candidates('Example/Berry') == ()
    store.save_observation(
        'Example/Berry',
        added_false.attrs,
        ObservationQuestion.ENTITY_CLASSIFICATION,
        ObservationStatus.CONFIRMED,
        kind='moonberry',
        meta=new_source.meta,
    )
    groups = classification_groups(store.entity_detail('Example/Berry'))
    assert next(group for group in groups if group.attrs['moon'] is False).classification is None
    assert store.needs_variant_review('Example/Berry', old_false.attrs, source.meta)
    assert store.unreviewed_variant_count('Example/Berry') == 2
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM variant_observations').fetchone()[0] == 3


def test_incremental_collection_distinguishes_presence_types_metadata_and_sources(
    tmp_path: Path,
) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test')
    base = AuditOccurrence(source=source, room='a', entity_id=1, attrs={'moon': False})
    occurrences = (
        base,
        base.model_copy(update={'attrs': {}}),
        base.model_copy(update={'attrs': {'moon': 0}}),
        base.model_copy(
            update={'source': source.model_copy(update={'meta': {'HeartIsEnd': True}})}
        ),
        base.model_copy(update={'source': source.model_copy(update={'mod_file': 'Other.zip'})}),
    )
    group = AuditGroup(entity_name='Example/Berry', occurrences=occurrences)
    assert store.collect_entities((group,)) == 5
    assert store.collect_entities((group,)) == 0
    assert len(store.occurrences('Example/Berry')) == 5
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name = 'audit_reports'").fetchone()
            is None
        )
        assert 'report_id' not in {
            row['name'] for row in conn.execute('PRAGMA table_info(raw_entity_occurrences)')
        }


def test_fingerprint_collision_rolls_back_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test')
    base = AuditOccurrence(source=source, room='a', entity_id=1, attrs={'moon': False})
    monkeypatch.setattr(audit_store, 'occurrence_fingerprint', lambda values: bytes(32))
    group = AuditGroup(
        entity_name='Example/Berry',
        occurrences=(
            base,
            base.model_copy(update={'attrs': {'moon': True}}),
        ),
    )
    with pytest.raises(ValueError, match='collision'):
        store.collect_entities((group,))
    assert store.entity_summaries() == ()


def test_audit_store_preserves_raw_attributes_and_attribute_missingness(tmp_path: Path) -> None:
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

    store.import_report(report_path)

    assert store.entity_summaries() == (
        EntityAuditSummary('Example/Berry', 2, None, EntityAuditStatus.UNKNOWN, ('Maps/Test.bin',)),
    )
    detail = store.entity_detail('Example/Berry')
    assert store.occurrences('Example/Berry')[0].attrs == {'moon': True, 'x': 8, 'y': 16}
    assert store.occurrence_maps('Example/Berry') == (
        AuditMapOccurrences(
            AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test', meta={}),
            (('a', 2),),
        ),
    )
    moon_variant = next(variant for variant in detail.variants if variant.attrs['moon'] is True)
    assert store.occurrence_maps('Example/Berry', (moon_variant,))[0].rooms == (('a', 1),)
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

    detail = store.entity_detail('Example/Berry')

    assert detail.variants[0].attrs == {'moon': False, 'tempo': 1}
    assert detail.variants[1].observations[0].kind == 'moonberry'
    assert detail.variants[1].observations[0].evidence == '游戏内测试'
    assert store.rule_candidates('Example/Berry') == (
        RuleCandidate(EntityRule(kind='moonberry', when={'moon': True}), 1),
        RuleCandidate(EntityRule(kind='strawberry', when={'moon': False}), 1),
    )
    store.save_attr_knowledge('Example/Berry', 'tempo', AttrAuditStatus.LIKELY_NOT_AFFECT_KIND)
    assert store.rule_candidates('Example/Berry') == (
        RuleCandidate(EntityRule(kind='moonberry', when={'moon': True}), 1, provisional=True),
        RuleCandidate(EntityRule(kind='strawberry', when={'moon': False}), 1, provisional=True),
    )


@pytest.mark.parametrize('value', ('[]', '{"value": []}', '{"value": null}'))
def test_attribute_json_rejects_values_outside_the_entity_attribute_domain(
    tmp_path: Path, value: str
) -> None:
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.collect_entities(
        [
            AuditGroup(
                entity_name='test',
                occurrences=(
                    AuditOccurrence(
                        source=AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test'),
                        room='room',
                        attrs={},
                    ),
                ),
            )
        ]
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE raw_entity_occurrences SET attrs_json = ?', (value,))
    with pytest.raises(TypeError, match='serialized entity attributes'):
        store.occurrences('test')


def test_audit_store_scopes_knowledge_to_one_entity_and_attribute(tmp_path: Path) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    store.import_report(report_path)
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

    layer = store.generated_rule_layer()

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


def test_confirm_entity_kind_preserves_raw_variants_and_suggests_default_rule(
    tmp_path: Path,
) -> None:
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
    store.import_report(report_path)

    assert (
        store.confirm_entity_kind(
            'Example/Collectible',
            'strawberry',
            evidence='名称与位置推断',
        )
        == 2
    )

    detail = store.entity_detail('Example/Collectible')
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
    assert store.rule_candidates('Example/Collectible') == (
        RuleCandidate(EntityRule(kind='strawberry'), 2),
    )
    confirmation = store.entity_detail('Example/Collectible').kind_confirmation
    assert confirmation is not None
    assert confirmation.kind == 'strawberry'
    assert confirmation.evidence == '名称与位置推断'

    assert store.revoke_entity_kind('Example/Collectible') == 2
    detail = store.entity_detail('Example/Collectible')
    assert detail.kind_confirmation is None
    assert all(not variant.observations for variant in detail.variants)
    assert store.rule_candidates('Example/Collectible') == ()
    store.save_attr_knowledge(
        'Example/Collectible',
        'mode',
        AttrAuditStatus.LIKELY_NOT_AFFECT_KIND,
        evidence='名称与位置推断',
    )
    assert store.rule_candidates('Example/Collectible') == ()


def test_audit_report_marks_new_classification_variants_after_terminal_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    store.import_report(initial_path)
    store.confirm_entity_kind('Example/Berry', 'strawberry')
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
    store.import_report(updated_path)

    assert store.unreviewed_variant_count('Example/Berry') == 1
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    store.import_report(initial_path)
    store.confirm_entity_kind('Example/Berry', 'strawberry')

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
                                'room': f'room-{i}',
                                'attrs': {'moon': True, 'x': i * 8, 'y': 16},
                            }
                            for i in range(12)
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store.import_report(updated_path)

    # Count decoding work to guard against per-instance expansion of semantic variants.
    attrs = audit_store._attrs  # noqa: SLF001
    attrs_calls = 0

    def count_attrs(value: str) -> dict[str, AttrValue]:
        nonlocal attrs_calls
        attrs_calls += 1
        return attrs(value)

    monkeypatch.setattr(audit_store, '_attrs', count_attrs)

    assert store.unreviewed_variant_count('Example/Berry') == 1
    assert attrs_calls <= 4


def test_empty_variant_review_checker_skips_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EntityAuditStore(tmp_path / 'entity-audit.sqlite3')
    monkeypatch.setattr(
        store, '_connect', lambda: pytest.fail('Empty snapshot must not open SQLite.')
    )

    assert not store.variant_review_checker(frozenset())('Example/Berry', {}, {})


def test_needs_variant_review_scopes_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EntityAuditStore(tmp_path / 'entity-audit.sqlite3')
    queried_names: list[set[str]] = []

    def checker(entity_names: Iterable[str] | None = None) -> VariantReview:
        assert entity_names is not None
        queried_names.append(set(entity_names))
        return lambda *_args: False

    monkeypatch.setattr(store, 'variant_review_checker', checker)

    assert not store.needs_variant_review('Example/Berry', {}, {})
    assert queried_names == [{'Example/Berry'}]


def test_audit_store_renames_saved_kind_refs(tmp_path: Path) -> None:
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
    store.import_report(report_path)
    store.confirm_entity_kind('Example/Collectible', 'strawberry')

    store.rename_kind('strawberry', 'redberry')

    detail = store.entity_detail('Example/Collectible')
    assert detail.kind_confirmation is not None
    assert detail.kind_confirmation.kind == 'redberry'
    assert detail.variants[0].observations[0].kind == 'redberry'


def test_non_collectible_variant_is_a_negative_rule_candidate_example(tmp_path: Path) -> None:
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
    store.import_report(report_path)
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

    assert store.rule_candidates('Example/Heart') == (
        RuleCandidate(EntityRule(kind='end_level_heart', when={'fake': False}), 1),
        RuleCandidate(EntityRule(kind=None, when={'fake': True}), 1),
    )
    store.save_entity_knowledge('Example/Heart', EntityAuditStatus.ENTITY_CANDIDATE)
    generated = store.generated_rule_layer()
    assert generated.entities['Example/Heart'].rules[1].kind is None
    assert 'exclude' not in entity_rules_toml(generated)
    store.save_attr_knowledge(
        'Example/Heart',
        'fake',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
    )

    assert store.rule_candidates('Example/Heart') == (
        RuleCandidate(EntityRule(kind='end_level_heart', when={'fake': False}), 1),
        RuleCandidate(EntityRule(kind=None, when={'fake': True}), 1),
        RuleCandidate(EntityRule(kind='end_level_heart'), 1, fallback=True),
    )


def test_rule_candidates_keep_affecting_attributes_without_competing_kinds(tmp_path: Path) -> None:
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
    store.import_report(report_path)
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

    assert store.rule_candidates('Example/Berry') == (
        RuleCandidate(EntityRule(kind='strawberry', when={'moon': False}), 1),
        RuleCandidate(EntityRule(kind='strawberry', missing=('moon',)), 1),
    )
    store.save_attr_knowledge(
        'Example/Berry',
        'moon',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
    )

    assert store.rule_candidates('Example/Berry') == (
        RuleCandidate(EntityRule(kind='strawberry', when={'moon': False}), 1),
        RuleCandidate(EntityRule(kind='strawberry', missing=('moon',)), 1),
    )


def test_rule_candidates_do_not_broaden_default_when_missing_is_confirmed(tmp_path: Path) -> None:
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
    store.import_report(report_path)
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

    assert store.rule_candidates('Example/Berry') == (
        RuleCandidate(EntityRule(kind='moonberry', when={'moon': True}), 1),
        RuleCandidate(EntityRule(kind='strawberry', when={'moon': False}), 1),
        RuleCandidate(EntityRule(kind='strawberry', missing=('moon',)), 1),
    )


def test_map_metadata_is_preserved_and_can_distinguish_rule_candidates(tmp_path: Path) -> None:
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
    store.import_report(report_path)
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

    detail = store.entity_detail('Example/Heart')

    assert detail.variants[0].meta == {'HeartIsEnd': False}
    assert detail.attr_summaries[0].name == '@meta.HeartIsEnd'
    assert store.rule_candidates('Example/Heart') == (
        RuleCandidate(EntityRule(kind='end_level_heart', meta={'HeartIsEnd': True}), 1),
        RuleCandidate(EntityRule(kind='keep_going_heart', meta={'HeartIsEnd': False}), 1),
    )


def test_attr_default_value_is_saved_separately_from_missing_raw_values(tmp_path: Path) -> None:
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

    store.import_report(report_path)
    store.save_attr_knowledge(
        'Example/Heart',
        'fake',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=False,
        evidence='源码分析',
    )
    detail = store.entity_detail('Example/Heart')

    assert detail.attr_summaries[0].name == 'fake'
    assert detail.attr_summaries[0].value_counts == ((None, 1), (True, 1))
    assert detail.attr_summaries[0].default_value is False
    assert detail.attr_summaries[0].evidence == '源码分析'


def test_attr_default_can_be_confirmed_as_json_null(tmp_path: Path) -> None:
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
    store.import_report(report_path)
    store.save_attr_knowledge(
        'Example/Heart',
        'fake',
        AttrAuditStatus.AFFECTS_KIND,
        default_value=None,
    )

    attribute = store.entity_detail('Example/Heart').attr_summaries[0]

    assert attribute.default_value is None
