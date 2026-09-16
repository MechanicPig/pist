import asyncio
import json

from textual.app import App
from textual.widgets import Button, Input, ListView, Select, Tree

from pist.entities.audit import (
    AttributeAuditStatus,
    AuditSource,
    EntityAuditStatus,
    EntityAuditStore,
    EntityAuditSummary,
    RawEntityOccurrence,
    occurrences_for_variants,
)
from pist.entities.rules import (
    EntityKind,
    EntityRules,
    EntityStat,
    EntityTableField,
)
from pist.game.routes import MapMarker
from pist.ui.entities.audit import (
    ATTRIBUTE_SELECT_ID,
    ATTRIBUTE_STATUS_ID,
    ENTITY_LIST_ID,
    ENTITY_STATUS_ID,
    KIND_TREE_ID,
    NEW_KIND_FIELD_ID,
    NEW_KIND_LABEL_ID,
    NEW_KIND_NAME_ID,
    NEW_KIND_STAT_ID,
    NEW_KIND_TABLE_ID,
    REVOKE_ENTITY_KIND_ID,
    UNKNOWN,
    VARIANT_SELECT_ID,
    VIEW_GROUP_OCCURRENCES_ID,
    EntityAuditApp,
    EntityAuditGroupItem,
    EntityAuditItem,
    KindContextScreen,
    KindEditorScreen,
    KindPickerScreen,
    _classification_groups,
    _default_value,
)
from pist.ui.entities.kinds import KindTree, add_kind_nodes
from pist.ui.entities.occurrences import (
    MapOccurrenceItem,
    MapOccurrences,
    OccurrenceScreen,
    map_occurrences,
    occurrence_markers,
)


def test_confirmed_default_input_distinguishes_json_null_from_blank() -> None:
    assert _default_value('') is UNKNOWN
    assert _default_value('null') is None
    assert _default_value('false') is False


def test_selecting_an_unreviewed_entity_opens_its_attribute_review(tmp_path) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    app = EntityAuditApp(store, store.import_report(report_path))

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(EntityAuditItem))
            assert app.query_one(f'#{ENTITY_STATUS_ID}', Select).value == 'unknown'

    asyncio.run(check())


def test_saving_attribute_knowledge_refreshes_attribute_options(tmp_path) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    app = EntityAuditApp(store, store.import_report(report_path))

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(EntityAuditItem))
            attributes = app.query_one(f'#{ATTRIBUTE_SELECT_ID}', Select)
            attributes.value = 'moon'
            await pilot.pause()
            app.query_one(
                f'#{ATTRIBUTE_STATUS_ID}', Select
            ).value = AttributeAuditStatus.DOES_NOT_AFFECT_KIND.value
            app.save_attribute()

            assert attributes.value == 'moon'
            label = next(label for label, value in attributes._options if value == 'moon')
            assert '不影响分类' in str(label)

    asyncio.run(check())


def test_saving_entity_reuses_loaded_navigation_summaries(tmp_path, monkeypatch) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    calls = 0
    original = store.entity_summaries

    def summaries(report_id: int | None = None) -> tuple[EntityAuditSummary, ...]:
        nonlocal calls
        calls += 1
        return original(report_id)

    monkeypatch.setattr(store, 'entity_summaries', summaries)
    app = EntityAuditApp(store, report_id)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(EntityAuditItem))
            entity_filter = app.query_one('#audit-entity-filter', Input)
            entity_filter.value = 'Other'
            await pilot.pause()
            app.query_one(
                f'#{ENTITY_STATUS_ID}', Select
            ).value = EntityAuditStatus.ENTITY_CANDIDATE.value
            app.save_entity()
            await pilot.pause()
            assert calls == 1
            assert entity_filter.value == 'Other'
            assert (
                app.query_one(EntityAuditItem).summary.status is EntityAuditStatus.ENTITY_CANDIDATE
            )
            assert not app.query_one(EntityAuditItem).display

    asyncio.run(check())


def test_saving_entity_moves_only_the_saved_navigation_item(tmp_path) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    app = EntityAuditApp(store, report_id)

    async def check() -> None:
        async with app.run_test() as pilot:
            entities = app.query_one(f'#{ENTITY_LIST_ID}', ListView)
            items = {item.summary.entity_name: item for item in entities.query(EntityAuditItem)}
            await pilot.click(items['First'])
            app.query_one(
                f'#{ENTITY_STATUS_ID}', Select
            ).value = EntityAuditStatus.ENTITY_CANDIDATE.value
            app.save_entity()
            await pilot.pause()

            refreshed = {item.summary.entity_name: item for item in entities.query(EntityAuditItem)}
            assert refreshed['First'] is not items['First']
            assert refreshed['Second'] is items['Second']
            assert refreshed['First'].summary.status is EntityAuditStatus.ENTITY_CANDIDATE
            groups = entities.query(EntityAuditGroupItem)
            assert [group.status for group in groups] == [
                EntityAuditStatus.UNKNOWN,
                EntityAuditStatus.ENTITY_CANDIDATE,
            ]

    asyncio.run(check())


def test_whole_entity_kind_is_reloaded_and_hides_attribute_review(tmp_path) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.confirm_entity_kind('Example/Heart', report_id, 'heart')
    app = EntityAuditApp(store, report_id)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(EntityAuditItem))
            assert not app.query_one('#audit-attribute-review').display
            assert not app.query_one(f'#{REVOKE_ENTITY_KIND_ID}', Button).disabled

            unreviewed = next(
                item
                for item in app.query(EntityAuditItem)
                if item.summary.entity_name == 'Example/Unreviewed'
            )
            await pilot.click(unreviewed)
            await pilot.pause()

            review = app.query_one('#audit-attribute-review')
            assert review.display
            assert review.region.height > 1
            assert app.query_one(f'#{ATTRIBUTE_SELECT_ID}', Select).disabled is False

            confirmed = next(
                item
                for item in app.query(EntityAuditItem)
                if item.summary.entity_name == 'Example/Heart'
            )
            await pilot.click(confirmed)
            app.revoke_entity_kind()

            assert app.query_one('#audit-attribute-review').display
            assert app.query_one(f'#{ATTRIBUTE_SELECT_ID}', Select).disabled is False
            assert app.query_one(f'#{REVOKE_ENTITY_KIND_ID}', Button).disabled

    asyncio.run(check())


def test_classification_groups_merge_only_confirmed_irrelevant_attributes(tmp_path) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)

    detail = store.entity_detail('Example/Berry', report_id)
    assert [group.attrs for group in _classification_groups(detail)] == [
        {'moon': True, 'tempo': 1},
        {'moon': True, 'tempo': 2},
    ]

    store.save_attribute_knowledge(
        'Example/Berry', 'tempo', AttributeAuditStatus.LIKELY_NOT_AFFECT_KIND
    )

    groups = _classification_groups(store.entity_detail('Example/Berry', report_id))
    assert [group.attrs for group in groups] == [{'moon': True}]
    assert len(groups[0].variants) == 2
    assert {
        occurrence.source.map_file
        for occurrence in occurrences_for_variants(detail.occurrences, groups[0].variants)
    } == {'Maps/First.bin', 'Maps/Second.bin'}

    app = EntityAuditApp(store, report_id)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.click(app.query_one(EntityAuditItem))
            variants = app.query_one(f'#{VARIANT_SELECT_ID}', Select)
            variants.value = '0'
            await pilot.pause()
            assert not app.query_one(f'#{VIEW_GROUP_OCCURRENCES_ID}', Button).disabled
            app.view_group_occurrences()
            assert isinstance(app.screen, OccurrenceScreen)
            assert {item.source.map_file for item in app.screen._maps} == {
                'Maps/First.bin',
                'Maps/Second.bin',
            }

    asyncio.run(check())


def test_double_clicking_an_occurrence_requests_a_map_preview() -> None:
    occurrence = RawEntityOccurrence(
        entity_name='Example/Berry',
        attrs={'x': 8, 'y': 16},
        source=AuditSource(scope='mod', map_file='Maps/Test.bin', map_name='Test'),
        room='a',
        entity_id=1,
    )
    requested: list[MapOccurrences] = []
    app = App()

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(
                OccurrenceScreen(
                    'Example/Berry',
                    (occurrence,),
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
            app.push_screen(KindPickerScreen(rules, save_kind=lambda _, __: rules))
            await pilot.pause()
            tree = app.screen.query_one(f'#{KIND_TREE_ID}', KindTree)
            assert tree.get_style_at(2, 0).meta['line'] == 0
            await pilot.click(tree, offset=(2, 0), button=3)
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, KindContextScreen)
            await pilot.click('#kind-context-add')
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, KindEditorScreen)
            assert screen.query_one(f'#{NEW_KIND_NAME_ID}', Input).value == ''

    asyncio.run(check())


def test_kind_picker_context_edit_opens_the_existing_kind() -> None:
    rules = EntityRules(
        kinds={
            'berry': EntityKind(
                label='浆果',
                sprite='berry.png',
                stat=EntityStat.COUNT,
                table_field=EntityTableField(table='主表', field='红草莓数'),
            )
        }
    )
    app = App()

    async def check() -> None:
        async with app.run_test() as pilot:
            app.push_screen(KindPickerScreen(rules, save_kind=lambda _, __: rules))
            await pilot.pause()
            tree = app.screen.query_one(f'#{KIND_TREE_ID}', KindTree)
            await pilot.click(tree, offset=(2, 0), button=3)
            await pilot.pause()
            await pilot.click('#kind-context-edit')
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, KindEditorScreen)
            name = screen.query_one(f'#{NEW_KIND_NAME_ID}', Input)
            assert name.value == 'berry'
            assert not name.disabled
            assert screen.query_one(f'#{NEW_KIND_LABEL_ID}', Input).value == '浆果'
            assert screen.query_one(f'#{NEW_KIND_STAT_ID}', Select).value == 'count'
            assert screen.query_one(f'#{NEW_KIND_TABLE_ID}', Input).value == '主表'
            assert screen.query_one(f'#{NEW_KIND_FIELD_ID}', Input).value == '红草莓数'

    asyncio.run(check())


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
            picker = KindPickerScreen(rules)
            app.push_screen(picker, selected.append)
            await pilot.pause()
            tree = app.screen.query_one(f'#{KIND_TREE_ID}', KindTree)
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


def test_entity_list_groups_statuses_and_prioritizes_map_progress(tmp_path, monkeypatch) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    store.save_entity_knowledge('Candidate', EntityAuditStatus.ENTITY_CANDIDATE)
    store.save_entity_knowledge('Ignored', EntityAuditStatus.IGNORED)
    app = EntityAuditApp(store, report_id)
    progress = {
        'Maps/SingleRun.bin': 0,
        'Maps/Completed.bin': 1,
        'Maps/Entered.bin': 2,
        'Maps/Unvisited.bin': 3,
        'Maps/Candidate.bin': 3,
        'Maps/Ignored.bin': 3,
    }
    monkeypatch.setattr(app, '_map_progress', progress.__getitem__)

    items = app._entity_items()

    assert [item.status for item in items if isinstance(item, EntityAuditGroupItem)] == [
        EntityAuditStatus.UNKNOWN,
        EntityAuditStatus.ENTITY_CANDIDATE,
        EntityAuditStatus.IGNORED,
    ]
    assert [item.summary.entity_name for item in items if isinstance(item, EntityAuditItem)] == [
        'SingleRun',
        'Completed',
        'Entered',
        'Unvisited',
        'Candidate',
        'Ignored',
    ]


def test_occurrence_preview_groups_one_package_and_marks_its_entity_positions(tmp_path) -> None:
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
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    report_id = store.import_report(report_path)
    occurrences = store.entity_detail('Example/Berry', report_id).occurrences

    maps = map_occurrences(occurrences, lambda _: 3)

    assert len(maps) == 2
    assert maps[0].source.mod_file == 'Example.zip'
    assert occurrence_markers(maps[0].occurrences) == {'a': (MapMarker(8, 16, 'audit'),)}
