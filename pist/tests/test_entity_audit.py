import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from textual import events
from textual.app import App
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Button, Input, OptionList, Select, Static, Tree

from berries.entities.rules import (
    EntityKind,
    EntityRuleLayer,
    EntityRules,
)
from berries.game.content import ContentPath
from berries.map_entrances import MapEntranceRules
from berries.map_layout import MapLayout, MapPreviewEntity
from pist.entities import audit as backend
from pist.entities.config import EntityConfigStore
from pist.save_progress import MapProgress, SaveProgressIndex
from pist.ui.entities import audit as audit_ui
from pist.ui.entities.audit.app import (
    ATTR_DEFAULT_ID,
    OBSERVATION_KIND_ID,
    OBSERVATION_QUESTION_ID,
    OBSERVATION_STATUS_ID,
    SAVE_ATTR_ID,
)
from pist.ui.entities.audit.classification import classification_groups
from pist.ui.entities.audit.kind_dialogs import (
    NEW_KIND_SPRITE_ID,
    SAVE_NEW_KIND_ID,
    AuditSelect,
    KindEdit,
)
from pist.ui.entities.audit.rules_refresh import (
    AuditRulesRefreshScreen,
    IncompleteAuditReviewScreen,
    audit_layer_diff,
)
from pist.ui.entities.kinds import KindTree, add_kind_nodes
from pist.ui.entities.occurrences import (
    MapOccurrenceItem,
    OccurrenceScreen,
    load_occurrence_map,
    occurrence_preview_entities,
)
from test_support.saves import MemorySaveReader


@pytest.mark.parametrize('down', (False, True))
def test_audit_select_wheel_at_overlay_edges_does_not_scroll_parent(down: bool) -> None:
    app = App[None]()

    async def check() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            selector = AuditSelect((str(i), str(i)) for i in range(40))
            before, after = Static('before'), Static('after')
            before.styles.height = after.styles.height = 30
            parent = VerticalScroll(before, selector, after)
            parent.styles.height = 20
            await app.screen.mount(parent)
            selector.scroll_visible(top=True, immediate=True)
            await pilot.pause()
            for _ in range(5):
                if app.focused is selector:
                    break
                await pilot.press('tab')
            assert app.focused is selector
            await pilot.press('space')
            assert selector.expanded
            await pilot.press('end' if down else 'home')
            await pilot.pause()
            overlay = selector.query_one(OptionList)
            assert overlay.scroll_y == (overlay.max_scroll_y if down else 0)
            original = parent.scroll_y
            assert 0 < original < parent.max_scroll_y
            event_type = events.MouseScrollDown if down else events.MouseScrollUp
            for _ in range(3):
                overlay.post_message(
                    event_type(overlay, 1, 1, 0, 1 if down else -1, 0, False, False, False)
                )
                await pilot.pause()
            assert parent.scroll_y == original
            assert selector.expanded
            await pilot.press('escape')

    asyncio.run(check())


async def select_entity(app: audit_ui.EntityAuditApp, pilot: Pilot[None], name: str) -> None:
    """Select the visible entity row and wait for the normal background load."""
    options = app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList)
    options.highlighted = options.get_option_index(name)
    options.action_select()
    await pilot.pause()
    await app.workers.wait_for_complete()


def test_occurrence_map_uses_application_rule_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'Content').mkdir()
    rules = EntityRules()
    entrances = MapEntranceRules()
    layout = MapLayout(())

    def load_layout(
        root: Path,
        path: str,
        *,
        entity_rules: EntityRules,
        entrance_rules: MapEntranceRules,
    ) -> MapLayout:
        assert root == tmp_path / 'Content'
        assert path == 'Maps/Test.bin'
        assert entity_rules is rules
        assert entrance_rules is entrances
        return layout

    monkeypatch.setattr('pist.ui.entities.occurrences.load_entity_rule_layers', lambda: rules)
    monkeypatch.setattr(
        'pist.ui.entities.occurrences.load_map_entrance_rule_layers', lambda: entrances
    )
    monkeypatch.setattr('pist.ui.entities.occurrences.MapLayout.from_path', load_layout)
    source = backend.AuditSource(scope='vanilla', map_file='Maps/Test.bin', map_name='Test')
    assert load_occurrence_map(tmp_path, source) == (ContentPath('Maps/Test.bin'), layout)


def test_audit_rule_diff_groups_changes_by_entity() -> None:
    current = EntityRuleLayer.model_validate(
        {
            'entities': {
                'strawberry': {'rules': [{'kind': 'strawberry'}]},
                'Unchanged/Entity': {'rules': [{'kind': 'strawberry'}]},
            }
        }
    )
    refreshed = EntityRuleLayer.model_validate(
        {
            'entities': {
                'strawberry': {
                    'rules': [
                        {'kind': 'strawberry', 'when': {'moon': False}},
                        {'kind': 'strawberry', 'missing': ['moon']},
                    ]
                },
                'New/Entity': {'rules': [{'kind': 'moonberry'}]},
                'Unchanged/Entity': {'rules': [{'kind': 'strawberry'}]},
            }
        }
    )

    diff = audit_layer_diff(current, refreshed)

    assert '--- [entities."New/Entity"]（当前）' in diff
    assert '+++ [entities."New/Entity"]（刷新后）' in diff
    assert '--- [entities.strawberry]（当前）' in diff
    assert '+++ [entities.strawberry]（刷新后）' in diff
    assert '+    { kind = "strawberry", missing = ["moon"] },' in diff
    assert 'Unchanged/Entity' not in diff


@pytest.mark.parametrize('value', ('', 'null', 'false'))
def test_confirmed_default_input_distinguishes_json_null_from_blank(
    tmp_path: Path, value: str
) -> None:
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = backend.AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test')
    store.collect_entities(
        [
            backend.AuditGroup(
                entity_name='test',
                occurrences=(
                    backend.AuditOccurrence(source=source, room='room', attrs={'moon': True}),
                ),
            )
        ]
    )
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test(size=(160, 60)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click(app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList))
            await pilot.press('home', 'enter')
            await app.workers.wait_for_complete()
            selector = app.query_one(f'#{audit_ui.ATTR_SELECT_ID}', Select)
            selector.scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click(selector, offset=(2, 0))
            await pilot.press('end', 'enter')
            assert selector.value == 'moon'
            field = app.query_one(f'#{ATTR_DEFAULT_ID}', Input)
            field.scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click(field)
            await pilot.press(*value)
            assert field.value == value
            save = app.query_one(f'#{SAVE_ATTR_ID}', Button)
            for _ in range(10):
                if app.focused is save:
                    break
                await pilot.press('tab')
            assert app.focused is save
            await pilot.press('enter')
            await app.workers.wait_for_complete()

    asyncio.run(check())
    summary = next(
        attr for attr in store.entity_detail('test').attr_summaries if attr.name == 'moon'
    )
    expected = {'': backend.UNKNOWN, 'null': None, 'false': False}[value]
    assert summary.default_value is expected


@pytest.mark.parametrize('continue_anyway', (False, True))
def test_incomplete_candidate_is_visible_and_allows_explicit_continuation(
    tmp_path: Path, continue_anyway: bool
) -> None:
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = backend.AuditSource(
        scope='mod', map_file='Maps/Test.bin', map_name='Test', meta={'HeartIsEnd': True}
    )
    original = backend.AuditOccurrence(source=source, room='a', attrs={'fake': False})
    missing_meta = original.model_copy(update={'source': source.model_copy(update={'meta': {}})})
    store.collect_entities(
        (backend.AuditGroup(entity_name='Example/Heart', occurrences=(original, missing_meta)),)
    )
    store.save_entity_knowledge('Example/Heart', backend.EntityAuditStatus.ENTITY_CANDIDATE)
    store.save_attr_knowledge(
        'Example/Heart', '@meta.HeartIsEnd', backend.AttrAuditStatus.AFFECTS_KIND
    )
    store.save_observation(
        'Example/Heart',
        original.attrs,
        backend.ObservationQuestion.ENTITY_CLASSIFICATION,
        backend.ObservationStatus.CONFIRMED,
        kind='heart',
        meta=source.meta,
    )
    kinds_path = tmp_path / 'kinds.toml'
    shared_path = tmp_path / 'shared.toml'
    config = EntityConfigStore(
        kinds_path=kinds_path,
        shared_path=shared_path,
        path=tmp_path / 'local.toml',
    )
    kinds_path.write_text('[kinds.heart]\nlabel="Heart"\n', encoding='utf-8')
    shared_path.write_text(
        '[[entities."Example/Heart".rules]]\nkind="heart"\nmeta={HeartIsEnd=true}\n',
        encoding='utf-8',
    )
    before = shared_path.read_bytes()
    result = store.rule_generation_result()
    assert result.incomplete_entities == ('Example/Heart',)
    assert 'Example/Heart' not in result.layer.entities
    app = audit_ui.EntityAuditApp(store, kind_store=config)

    async def check() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            options = app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList)
            assert '未完全覆盖' in str(options.get_option('Example/Heart').prompt)
            app.refresh_audit_rules()
            async with asyncio.timeout(5):
                while not isinstance(app.screen, IncompleteAuditReviewScreen):
                    await asyncio.sleep(0.01)
            await pilot.pause()
            assert 'Example/Heart' in str(
                app.screen.query_one('#audit-incomplete-list', Static).content
            )
            assert shared_path.read_bytes() == before
            assert await pilot.click(
                '#audit-incomplete-continue' if continue_anyway else '#audit-incomplete-cancel'
            )
            async with asyncio.timeout(5):
                await app.workers.wait_for_complete()
            if continue_anyway:
                assert isinstance(app.screen, AuditRulesRefreshScreen)
                assert shared_path.read_bytes() == before
                await pilot.pause()
                assert await pilot.click('#audit-rules-refresh-save')
                async with asyncio.timeout(5):
                    await app.workers.wait_for_complete()
                assert config.load().entities == {}
                return
            assert not isinstance(app.screen, AuditRulesRefreshScreen)
            assert shared_path.read_bytes() == before
            store.save_observation(
                'Example/Heart',
                missing_meta.attrs,
                backend.ObservationQuestion.ENTITY_CLASSIFICATION,
                backend.ObservationStatus.CONFIRMED,
                kind='heart',
                meta={},
            )
            await select_entity(app, pilot, 'Example/Heart')
            assert '未完全覆盖' not in str(options.get_option('Example/Heart').prompt)
            app.refresh_audit_rules()
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, AuditRulesRefreshScreen)
            assert shared_path.read_bytes() == before
            uncovered = original.model_copy(
                update={'source': source.model_copy(update={'meta': {'HeartIsEnd': False}})}
            )
            store.collect_entities(
                (backend.AuditGroup(entity_name='Example/Heart', occurrences=(uncovered,)),)
            )
            app.screen.dismiss(True)
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert shared_path.read_bytes() == before
            assert '未完全覆盖' in str(options.get_option('Example/Heart').prompt)

    asyncio.run(check())


def test_equivalent_variant_keeps_confirmed_group_and_classification_controls(
    tmp_path: Path,
) -> None:
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    source = backend.AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test')
    original = backend.AuditOccurrence(source=source, room='a', attrs={'moon': False, 'delay': 1.0})
    added = original.model_copy(update={'attrs': {'moon': False, 'delay': 2.0}})
    store.collect_entities(
        (backend.AuditGroup(entity_name='Example/Berry', occurrences=(original, added)),)
    )
    store.save_attr_knowledge('Example/Berry', 'delay', backend.AttrAuditStatus.AFFECTS_BEHAVIOR)
    store.save_observation(
        'Example/Berry',
        original.attrs,
        backend.ObservationQuestion.ENTITY_CLASSIFICATION,
        backend.ObservationStatus.CONFIRMED,
        kind='strawberry',
    )
    store.save_observation(
        'Example/Berry',
        added.attrs,
        backend.ObservationQuestion.PAUSE_MENU_COUNT,
        backend.ObservationStatus.CONFIRMED,
        kind='strawberry',
    )
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList).action_select()
            await pilot.pause()
            await app.workers.wait_for_complete()
            variants = app.query_one(f'#{audit_ui.VARIANT_SELECT_ID}', Select)
            variants.focus()
            await pilot.press('enter')
            options = variants.query_one(OptionList)
            assert options.option_count == 2  # Empty choice plus one equivalent-variant group.
            assert '未确认' not in str(options.get_option_at_index(1).prompt)
            await pilot.press('escape')
            app.query_one(f'#{audit_ui.VARIANT_SELECT_ID}', Select).value = '0'
            await pilot.pause()
            assert (
                app.query_one(f'#{OBSERVATION_QUESTION_ID}', Select).value
                == 'entity_classification'
            )
            assert app.query_one(f'#{OBSERVATION_STATUS_ID}', Select).value == 'confirmed'
            assert '(strawberry)' in str(app.query_one(f'#{OBSERVATION_KIND_ID}', Button).label)

    asyncio.run(check())
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM variant_observations').fetchone()[0] == 2


def test_selecting_an_unreviewed_entity_opens_its_attribute_review(tmp_path: Path) -> None:
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
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList).action_select()
            await pilot.pause()
            assert app.query_one(f'#{audit_ui.ENTITY_STATUS_ID}', Select).value == 'unknown'

    asyncio.run(check())


def test_selecting_entity_calculates_deferred_unreviewed_variant_count(tmp_path: Path) -> None:
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
                                'attrs': {'moon': True},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    store.save_observation(
        'Example/Berry',
        {'moon': False},
        backend.ObservationQuestion.ENTITY_CLASSIFICATION,
        backend.ObservationStatus.CONFIRMED,
        kind='strawberry',
    )
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            options = app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList)
            before = str(options.get_option('Example/Berry').prompt)
            assert 'variants' not in before and '个新变体待复核' not in before
            await select_entity(app, pilot, 'Example/Berry')
            after = str(options.get_option('Example/Berry').prompt)
            assert '2 variants' in after and '1 个新变体待复核' in after

    asyncio.run(check())


def test_saving_attr_knowledge_refreshes_attr_options(tmp_path: Path) -> None:
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
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            await select_entity(app, pilot, 'Example/Berry')
            attributes = app.query_one(f'#{audit_ui.ATTR_SELECT_ID}', Select)
            attributes.value = 'moon'
            await pilot.pause()
            app.query_one(
                f'#{audit_ui.ATTR_STATUS_ID}', Select
            ).value = backend.AttrAuditStatus.DOES_NOT_AFFECT_KIND.value
            app.save_attr()

            assert attributes.value == 'moon'
            attributes.focus()
            await pilot.press('enter')
            assert any(
                '不影响分类' in str(option.prompt)
                for option in attributes.query_one(OptionList).options
            )

    asyncio.run(check())


def test_saving_entity_reuses_loaded_navigation_summaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'entities': [
                    {
                        'entity_name': 'Example/Entity',
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
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    calls = 0
    original = store.entity_summaries

    def summaries() -> tuple[backend.EntityAuditSummary, ...]:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(store, 'entity_summaries', summaries)
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            await select_entity(app, pilot, 'Example/Entity')
            entity_filter = app.query_one('#audit-entity-filter', Input)
            entity_filter.value = 'Other'
            await pilot.pause()
            app.query_one(
                f'#{audit_ui.ENTITY_STATUS_ID}', Select
            ).value = backend.EntityAuditStatus.ENTITY_CANDIDATE.value
            app.save_entity()
            await pilot.pause()
            assert calls == 1
            assert entity_filter.value == 'Other'
            assert (
                store.entity_detail('Example/Entity').status
                is backend.EntityAuditStatus.ENTITY_CANDIDATE
            )
            assert app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList).option_count == 0

    asyncio.run(check())


def test_saving_entity_rebuilds_virtual_navigation(tmp_path: Path) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'entities': [
                    {
                        'entity_name': name,
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': f'Maps/{name}.bin',
                                    'map_name': name,
                                },
                                'room': 'a',
                                'attrs': {},
                            }
                        ],
                    }
                    for name in ('First', 'Second')
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            entities = app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList)
            await select_entity(app, pilot, 'First')
            app.query_one(
                f'#{audit_ui.ENTITY_STATUS_ID}', Select
            ).value = backend.EntityAuditStatus.ENTITY_CANDIDATE.value
            app.save_entity()
            await pilot.pause()

            assert '实体候选' in str(entities.get_option('First').prompt)
            assert [option.id for option in entities.options if option.disabled] == [None, None]

    asyncio.run(check())


def test_whole_entity_kind_is_reloaded_and_hides_attribute_review(tmp_path: Path) -> None:
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
                    },
                    {
                        'entity_name': 'Example/Unreviewed',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'b',
                                'attrs': {'setting': 1},
                            }
                        ],
                    },
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    store.confirm_entity_kind('Example/Heart', 'heart')
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            await select_entity(app, pilot, 'Example/Heart')
            assert not app.query_one('#audit-attribute-review').display
            assert not app.query_one(f'#{audit_ui.REVOKE_ENTITY_KIND_ID}', Button).disabled

            await select_entity(app, pilot, 'Example/Unreviewed')
            await pilot.pause()

            review = app.query_one('#audit-attribute-review')
            assert review.display
            assert review.region.height > 1
            assert app.query_one(f'#{audit_ui.ATTR_SELECT_ID}', Select).disabled is False

            await select_entity(app, pilot, 'Example/Heart')
            app.revoke_entity_kind()

            assert app.query_one('#audit-attribute-review').display
            assert app.query_one(f'#{audit_ui.ATTR_SELECT_ID}', Select).disabled is False
            assert app.query_one(f'#{audit_ui.REVOKE_ENTITY_KIND_ID}', Button).disabled

    asyncio.run(check())


def test_classification_groups_merge_only_confirmed_irrelevant_attributes(tmp_path: Path) -> None:
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
                                    'map_file': 'Maps/First.bin',
                                    'map_name': 'First',
                                },
                                'room': 'a',
                                'attrs': {'moon': True, 'tempo': 1},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Second.bin',
                                    'map_name': 'Second',
                                },
                                'room': 'b',
                                'attrs': {'moon': True, 'tempo': 2},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)

    detail = store.entity_detail('Example/Berry')
    assert [group.attrs for group in classification_groups(detail)] == [
        {'moon': True, 'tempo': 1},
        {'moon': True, 'tempo': 2},
    ]

    store.save_attr_knowledge(
        'Example/Berry', 'tempo', backend.AttrAuditStatus.LIKELY_NOT_AFFECT_KIND
    )

    groups = classification_groups(store.entity_detail('Example/Berry'))
    assert [group.attrs for group in groups] == [{'moon': True}]
    assert len(groups[0].variants) == 2
    assert {
        occurrence.source.map_file
        for occurrence in backend.occurrences_for_variants(
            store.occurrences('Example/Berry'), groups[0].variants
        )
    } == {'Maps/First.bin', 'Maps/Second.bin'}

    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test() as pilot:
            await select_entity(app, pilot, 'Example/Berry')
            variants = app.query_one(f'#{audit_ui.VARIANT_SELECT_ID}', Select)
            variants.value = '0'
            await pilot.pause()
            assert not app.query_one(f'#{audit_ui.VIEW_GROUP_OCCURRENCES_ID}', Button).disabled
            app.view_group_occurrences()
            await asyncio.sleep(0.05)
            await pilot.pause()
            assert isinstance(app.screen, OccurrenceScreen)
            assert {item.data.source.map_file for item in app.screen.query(MapOccurrenceItem)} == {
                'Maps/First.bin',
                'Maps/Second.bin',
            }

    asyncio.run(check())


def test_double_clicking_an_occurrence_requests_a_map_preview() -> None:
    occurrence = backend.RawEntityOccurrence(
        entity_name='Example/Berry',
        attrs={'x': 8, 'y': 16},
        source=backend.AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test'),
        room='a',
        entity_id=1,
    )
    map_data = backend.AuditMapOccurrences(occurrence.source, ((occurrence.room, 1),))
    requested: list[backend.AuditMapOccurrences] = []
    app = App()

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(
                OccurrenceScreen(
                    'Example/Berry',
                    (map_data,),
                    map_progress=lambda _: 0,
                    preview=requested.append,
                )
            )
            await pilot.pause()
            await pilot.double_click(app.screen.query_one(MapOccurrenceItem))

    asyncio.run(check())

    assert [data.source.map_file for data in requested] == ['Maps/Test.bin']


def test_kind_picker_tree_keeps_the_configured_hierarchy() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='浆果'),
            'special': EntityKind(label='特殊草莓', parent='berry'),
            'goldenberry': EntityKind(label='金草莓', parent='special'),
        }
    )
    tree = Tree[str]('kind')

    add_kind_nodes(tree.root, rules)

    berry = tree.root.children[0]
    assert berry.data == 'berry'
    assert berry.children[0].data == 'special'
    assert berry.children[0].children[0].data == 'goldenberry'


def test_kind_picker_context_click_offers_edit_and_child_creation() -> None:
    rules = EntityRules(kinds={'berry': EntityKind(label='浆果')})
    app = App()

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(audit_ui.KindPickerScreen(rules, save_kind=lambda _, __: rules))
            await pilot.pause()
            tree = app.screen.query_one(f'#{audit_ui.KIND_TREE_ID}', KindTree)
            assert tree.get_style_at(2, 0).meta['line'] == 0
            await pilot.click(tree, offset=(2, 0), button=3)
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, audit_ui.KindContextScreen)
            await pilot.click('#kind-context-add')
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, audit_ui.KindEditorScreen)
            assert screen.query_one(f'#{audit_ui.NEW_KIND_NAME_ID}', Input).value == ''

    asyncio.run(check())


def test_kind_picker_context_edit_opens_the_existing_kind() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(
                label='浆果',
                sprite='berry.png',
            )
        }
    )
    app = App()

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(audit_ui.KindPickerScreen(rules, save_kind=lambda _, __: rules))
            await pilot.pause()
            tree = app.screen.query_one(f'#{audit_ui.KIND_TREE_ID}', KindTree)
            await pilot.click(tree, offset=(2, 0), button=3)
            await pilot.pause()
            await pilot.click('#kind-context-edit')
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, audit_ui.KindEditorScreen)
            name = screen.query_one(f'#{audit_ui.NEW_KIND_NAME_ID}', Input)
            assert name.value == 'berry'
            assert not name.disabled
            assert screen.query_one(f'#{audit_ui.NEW_KIND_LABEL_ID}', Input).value == '浆果'

    asyncio.run(check())


@pytest.mark.parametrize('editing', (False, True))
def test_kind_editor_keeps_invalid_input_and_returns_validated_definition(editing: bool) -> None:
    rules = EntityRules(
        kinds={
            'root': EntityKind(label='Root'),
            'berry': EntityKind(label='Berry', parent='root'),
        }
    )
    app = App()
    results: list[KindEdit | None] = []

    async def check() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = audit_ui.KindEditorScreen(
                rules, kind='berry' if editing else None, parent='root'
            )
            app.push_screen(screen, results.append)
            await pilot.pause()
            screen.query_one(f'#{audit_ui.NEW_KIND_NAME_ID}', Input).value = 'custom'
            screen.query_one(f'#{audit_ui.NEW_KIND_LABEL_ID}', Input).value = ''
            sprite = screen.query_one(f'#{NEW_KIND_SPRITE_ID}', Input)
            sprite.value = '../outside.png'
            assert await pilot.click(f'#{SAVE_NEW_KIND_ID}')
            await pilot.pause()
            assert app.screen is screen
            assert results == []
            assert sprite.value == '../outside.png'
            assert screen.query_one(f'#{audit_ui.NEW_KIND_NAME_ID}', Input).value == 'custom'
            assert any('Sprite path' in call.args[0] for call in notify.call_args_list)

            sprite.value = 'custom.png'
            # Textual ignores repeated clicks while the button's press effect is active.
            async with asyncio.timeout(1):
                while screen.query_one(f'#{SAVE_NEW_KIND_ID}', Button).has_class('-active'):
                    await asyncio.sleep(0.01)
            assert await pilot.click(f'#{SAVE_NEW_KIND_ID}')
            await pilot.pause()
            assert app.screen is not screen

    with patch.object(app, 'notify', wraps=app.notify) as notify:
        asyncio.run(check())
    assert results == [
        KindEdit('custom', EntityKind(label='custom', parent='root', sprite='custom.png'))
    ]


def test_kind_picker_toggles_with_one_click_and_selects_with_two() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(label='浆果'),
            'strawberry': EntityKind(label='草莓', parent='berry'),
        }
    )
    app = App()
    selected: list[str | None] = []

    async def check() -> None:
        async with app.run_test() as pilot:
            picker = audit_ui.KindPickerScreen(rules)
            app.push_screen(picker, selected.append)
            await pilot.pause()
            tree = app.screen.query_one(f'#{audit_ui.KIND_TREE_ID}', KindTree)
            assert tree.root.children[0].is_expanded
            await pilot.click(tree, offset=(2, 0))
            await pilot.pause()

            assert app.screen is picker
            assert tree.root.children[0].is_collapsed
            assert selected == []

            await pilot.click(tree, offset=(2, 0), times=2)
            await pilot.pause()

    asyncio.run(check())

    assert selected == ['berry']


def test_entity_list_groups_statuses_and_prioritizes_map_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ('SingleRun', 'Completed', 'Entered', 'Unvisited', 'Candidate', 'Ignored')
    report_path = tmp_path / 'report.json'
    report_path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': name,
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': f'Maps/{name}.bin',
                                    'map_name': name,
                                },
                                'room': 'a',
                                'attrs': {},
                            }
                        ],
                    }
                    for name in names
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    store.save_entity_knowledge('Candidate', backend.EntityAuditStatus.ENTITY_CANDIDATE)
    store.save_entity_knowledge('Ignored', backend.EntityAuditStatus.IGNORED)
    app = audit_ui.EntityAuditApp(store, save_reader=MemorySaveReader())
    progress = {
        'Maps/SingleRun.bin': MapProgress.SINGLE_RUN_COMPLETED,
        'Maps/Completed.bin': MapProgress.COMPLETED,
        'Maps/Entered.bin': MapProgress.ENTERED,
        'Maps/Unvisited.bin': MapProgress.UNRECORDED,
        'Maps/Candidate.bin': MapProgress.UNRECORDED,
        'Maps/Ignored.bin': MapProgress.UNRECORDED,
    }
    calls: dict[str, int] = {}

    def map_progress(index: SaveProgressIndex, map_file: ContentPath) -> MapProgress:
        path = map_file.as_posix()
        calls[path] = calls.get(path, 0) + 1
        return progress[path]

    monkeypatch.setattr(SaveProgressIndex, 'local_map_progress', map_progress)

    async def check() -> None:
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            options = app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList)
            assert sum(option.disabled for option in options.options) == 3
            expected = list(names)
            assert [option.id for option in options.options if not option.disabled] == expected
            search = app.query_one('#audit-entity-filter', Input)
            search.value = 'Single'
            await pilot.pause()
            assert [option.id for option in options.options if not option.disabled] == ['SingleRun']
            search.value = ''
            await pilot.pause()
            assert [option.id for option in options.options if not option.disabled] == expected

    asyncio.run(check())
    assert calls == dict.fromkeys(progress, 1)


def test_entity_navigation_updates_preserve_selection_and_scroll(tmp_path: Path) -> None:
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    names = tuple(f'Berry{i:02}' for i in range(45))
    store.collect_entities(
        tuple(
            backend.AuditGroup(
                entity_name=name,
                occurrences=(
                    backend.AuditOccurrence(
                        source=backend.AuditSource(
                            scope='mod', map_file='Maps/Test.bin', map_name='Test'
                        ),
                        room='a',
                        attrs={},
                    ),
                ),
            )
            for name in names
        )
    )
    app = audit_ui.EntityAuditApp(store)

    async def check() -> None:
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            options = app.query_one(f'#{audit_ui.ENTITY_LIST_ID}', OptionList)
            options.scroll_to(y=40, animate=False, immediate=True, force=True)
            await pilot.pause()
            original_scroll = options.scroll_y
            assert original_scroll > 0
            await pilot.click(options, offset=(3, 5))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert options.scroll_y == original_scroll
            assert options.highlighted is not None
            selected = options.get_option_at_index(options.highlighted)
            assert selected.id is not None
            assert selected.id in str(app.query_one('#audit-detail-content', Static).content)
            assert 'variants' in str(selected.prompt)

            worker = app.refresh_audit_rules()
            async with asyncio.timeout(5):
                while not worker.is_finished:
                    if isinstance(app.screen, IncompleteAuditReviewScreen):
                        await pilot.press('escape')
                    await pilot.pause()
            await worker.wait()
            if isinstance(app.screen, AuditRulesRefreshScreen):
                await pilot.press('escape')
            await pilot.pause()
            assert options.scroll_y == original_scroll
            assert options.get_option_at_index(options.highlighted) is selected

            # A real regroup still rebuilds the rows, but retains the viewport and identity.
            app.query_one(
                f'#{audit_ui.ENTITY_STATUS_ID}', Select
            ).value = backend.EntityAuditStatus.IGNORED.value
            await pilot.pause()
            await pilot.press('ctrl+s')
            await pilot.pause()
            assert store.entity_detail(selected.id).status is backend.EntityAuditStatus.IGNORED
            assert options.scroll_y == original_scroll
            assert options.get_option_at_index(options.highlighted).id == selected.id
            assert options.get_option_at_index(options.highlighted) is not selected

            # Filtering out the selection must not leave a stale entity highlighted.
            app.query_one('#audit-entity-filter', Input).value = 'no such entity'
            await pilot.pause()
            assert options.option_count == 0
            assert options.highlighted is None

    asyncio.run(check())


def test_occurrence_preview_groups_one_pkg_and_marks_its_entity_positions(
    tmp_path: Path,
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
                                    'mod_file': 'Example.zip',
                                },
                                'room': 'a',
                                'attrs': {'x': 8, 'y': 16},
                            },
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                    'mod_file': 'Other.zip',
                                },
                                'room': 'a',
                                'attrs': {'x': 24, 'y': 32},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = backend.EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(report_path)
    maps = store.occurrence_maps('Example/Berry')

    assert len(maps) == 2
    example_map = next(data for data in maps if data.source.mod_file == 'Example.zip')
    occurrences = store.map_occurrences('Example/Berry', example_map.source)
    assert occurrence_preview_entities(occurrences) == {'a': (MapPreviewEntity(8, 16, 'audit'),)}
