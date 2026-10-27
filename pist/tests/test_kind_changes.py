import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, OptionList

from berries.entities.rules import EntityKind
from pist.entities import config as entity_config
from pist.entities.audit import kind_changes
from pist.entities.audit.store import EntityAuditStore
from pist.entities.maintenance import EntityRuleLibrary
from pist.ui.entities.audit.app import ENTITY_KIND_ID, ENTITY_LIST_ID, EntityAuditApp
from pist.ui.entities.audit.kind_dialogs import KindContextScreen, KindPickerScreen
from pist.ui.entities.kinds import KindTree


async def _open_kind_menu(app: EntityAuditApp, pilot: Pilot[None]) -> KindTree:
    await app.workers.wait_for_complete()
    options = app.query_one(f'#{ENTITY_LIST_ID}', OptionList)
    assert await pilot.click(options)
    await pilot.press('home', 'enter')
    await app.workers.wait_for_complete()
    button = app.query_one(f'#{ENTITY_KIND_ID}')
    button.scroll_visible(immediate=True)
    await pilot.pause()
    assert await pilot.click(button)
    await pilot.pause()
    assert isinstance(app.screen, KindPickerScreen)
    tree = app.screen.query_one(KindTree)
    node = next(child for child in tree.root.children[0].children if child.data == 'old')
    offset = tree.content_region.offset - tree.region.offset + (8, node.line)
    assert await pilot.click(tree, offset=offset, button=3)
    await pilot.pause()
    return tree


async def _submit_kind_change(pilot: Pilot[None], operation: str) -> None:
    assert await pilot.click(f'#kind-context-{"edit" if operation == "rename" else "delete"}')
    await pilot.pause()
    if operation == 'rename':
        assert await pilot.click('#audit-new-kind-name')
        await pilot.press('ctrl+a', 'n', 'e', 'w')
        assert await pilot.click('#audit-save-new-kind')
    else:
        assert await pilot.click('#kind-delete-confirm')
    await pilot.pause()


@pytest.fixture
def config(tmp_path: Path) -> EntityRuleLibrary:
    kinds = tmp_path / 'kinds.toml'
    shared = tmp_path / 'shared.toml'
    local = tmp_path / 'local.toml'
    kinds.write_text(
        '[kinds.root]\nlabel="Root"\n[kinds.old]\nlabel="Old"\nparent="root"\n',
        encoding='utf-8',
    )
    shared.write_text('[[entities.test.rules]]\nkind="old"\n', encoding='utf-8')
    local.write_text('[[entities.local.rules]]\nkind="old"\n', encoding='utf-8')
    return EntityRuleLibrary(shared, kinds_path=kinds)


@pytest.fixture
def audit(tmp_path: Path) -> EntityAuditStore:
    path = tmp_path / 'report.json'
    path.write_text(
        json.dumps(
            {
                'unmatched': [
                    {
                        'entity_name': 'test',
                        'occurrences': [
                            {
                                'source': {
                                    'scope': 'mod',
                                    'map_file': 'Maps/Test.bin',
                                    'map_name': 'Test',
                                },
                                'room': 'room',
                                'attrs': {'id': 1},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    store = EntityAuditStore(tmp_path / 'audit.sqlite3')
    store.import_report(path)
    store.confirm_entity_kind('test', 'old')
    return store


@pytest.mark.parametrize('operation', ('rename', 'delete'))
@pytest.mark.parametrize('failure', ('kinds.toml', 'shared.toml', 'local.toml', 'database', None))
def test_kind_changes_leave_completed_writes_and_keep_database_transactional(
    tmp_path: Path,
    config: EntityRuleLibrary,
    audit: EntityAuditStore,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: str | None,
) -> None:
    store = audit
    original = config.load()
    before = {path: path.read_bytes() for path in tmp_path.glob('*.toml')}
    rules = (
        original.with_renamed_kind('old', 'new', EntityKind(label='New', parent='root'))
        if operation == 'rename'
        else original.with_deleted_kind('old')
    )
    target = 'new' if operation == 'rename' else 'root'
    write_text = Path.write_text

    def write_file(path: Path, text: str, *, encoding: str) -> int:
        if path.name == failure:
            raise PermissionError('simulated read-only target')
        return write_text(path, text, encoding=encoding)

    monkeypatch.setattr(Path, 'write_text', write_file)
    if failure == 'database':
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """CREATE TRIGGER reject_kind_change
                BEFORE UPDATE ON entity_kind_confirmations BEGIN
                SELECT RAISE(ABORT, 'simulated audit failure'); END"""
            )
    if failure in (None, 'local.toml'):
        kind_changes.change_kind(config, store, 'old', target, rules)
        assert config.load() == rules
    else:
        with pytest.raises(RuntimeError, match='未自动恢复'):
            kind_changes.change_kind(config, store, 'old', target, rules)
    if failure == 'kinds.toml':
        assert all(path.read_bytes() == data for path, data in before.items())
    elif failure == 'shared.toml':
        assert (tmp_path / 'kinds.toml').read_bytes() != before[tmp_path / 'kinds.toml']
        assert (tmp_path / 'shared.toml').read_bytes() == before[tmp_path / 'shared.toml']
    else:
        assert config.load() == rules
    detail = store.entity_detail('test')
    assert detail.kind_confirmation is not None
    expected = target if failure in (None, 'local.toml') else 'old'
    assert (tmp_path / 'local.toml').read_bytes() == before[tmp_path / 'local.toml']
    assert detail.kind_confirmation.kind == expected
    assert detail.variants[0].observations[0].kind == expected
    assert not tuple(tmp_path.glob('.*.bak'))
    assert not tuple(tmp_path.glob('.*.tmp'))


def test_audit_commit_failure_keeps_written_files_and_rolls_back_refs(
    config: EntityRuleLibrary,
    audit: EntityAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = audit
    original = config.load()
    rules = original.with_renamed_kind('old', 'new', EntityKind(label='New', parent='root'))
    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def commit(self) -> None:
            raise sqlite3.OperationalError('simulated commit failure')

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda path: connect(path, factory=FailingConnection))
        with pytest.raises(RuntimeError, match='commit failure'):
            kind_changes.change_kind(config, store, 'old', 'new', rules)
    assert config.load() == rules
    detail = store.entity_detail('test')
    assert detail.kind_confirmation is not None and detail.kind_confirmation.kind == 'old'
    assert detail.variants[0].observations[0].kind == 'old'


@pytest.mark.parametrize('operation', ('rename', 'delete'))
def test_kind_failure_is_visible_in_ui_and_keeps_original_memory(
    config: EntityRuleLibrary,
    audit: EntityAuditStore,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store = audit
    app = EntityAuditApp(store, kind_store=config)
    original = config.load()
    messages: list[str] = []
    monkeypatch.setattr(app, 'notify', lambda message, **kwargs: messages.append(message))
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """CREATE TRIGGER reject_kind_change BEFORE UPDATE ON variant_observations
            BEGIN SELECT RAISE(ABORT, 'simulated database error'); END"""
        )

    async def run() -> None:
        async with app.run_test(size=(160, 60)) as pilot:
            await _open_kind_menu(app, pilot)
            await _submit_kind_change(pilot, operation)
            assert isinstance(app.screen, KindPickerScreen)
            tree = app.screen.query_one(KindTree)
            assert tree.root.children[0].children[0].data == 'old'

    asyncio.run(run())
    assert len(messages) == 1
    message = messages[0]
    assert 'simulated database error' in message
    assert '未自动恢复' in message and 'Git diff' in message
    assert config.load() != original
    detail = audit.entity_detail('test')
    assert detail.kind_confirmation is not None and detail.kind_confirmation.kind == 'old'
    assert detail.variants[0].observations[0].kind == 'old'


@pytest.mark.parametrize('shared', (False, True))
def test_audit_picker_only_opens_category_edits_in_shared_mode(
    tmp_path: Path,
    config: EntityRuleLibrary,
    audit: EntityAuditStore,
    shared: bool,
) -> None:
    user_config = (
        EntityRuleLibrary(tmp_path / 'shared.toml', kinds_path=tmp_path / 'kinds.toml')
        if shared
        else entity_config.EntityConfigStore(
            tmp_path / 'local.toml',
            shared_path=tmp_path / 'shared.toml',
            kinds_path=tmp_path / 'kinds.toml',
        )
    )
    app = EntityAuditApp(audit, kind_store=user_config)
    before = {path: path.read_bytes() for path in tmp_path.glob('*.toml')}

    async def run() -> None:
        async with app.run_test(size=(160, 60)) as pilot:
            tree = await _open_kind_menu(app, pilot)
            if shared:
                assert isinstance(app.screen, KindContextScreen)
            else:
                assert isinstance(app.screen, KindPickerScreen)
                node = tree.root.children[0].children[0]
                offset = tree.content_region.offset - tree.region.offset + (8, node.line)
                assert await pilot.double_click(tree, offset=offset)
                await pilot.pause()
                assert '(old)' in str(app.query_one(f'#{ENTITY_KIND_ID}', Button).label)
                assert not isinstance(app.screen, KindPickerScreen)

    asyncio.run(run())
    assert all(path.read_bytes() == data for path, data in before.items())
