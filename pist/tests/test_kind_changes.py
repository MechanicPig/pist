import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from berries.entities import rules as entity_rules
from pist.entities.audit import kind_changes
from pist.entities.audit.store import EntityAuditStore
from pist.ui.entities.audit.app import EntityAuditApp
from pist.ui.entities.audit.kind_dialogs import NewKind


@pytest.fixture
def config(tmp_path: Path) -> entity_rules.EntityConfigStore:
    kinds = tmp_path / 'kinds.toml'
    shared = tmp_path / 'shared.toml'
    local = tmp_path / 'local.toml'
    kinds.write_text(
        '[kinds.root]\nlabel="Root"\n[kinds.old]\nlabel="Old"\nparent="root"\n',
        encoding='utf-8',
    )
    shared.write_text('[[entities.test.rules]]\nkind="old"\n', encoding='utf-8')
    local.write_text('[[entities.local.rules]]\nkind="old"\n', encoding='utf-8')
    return entity_rules.EntityConfigStore(kinds_path=kinds, shared_path=shared, local_path=local)


@pytest.fixture
def audit(tmp_path: Path) -> tuple[EntityAuditStore, int]:
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
    report_id = store.import_report(path)
    store.confirm_entity_kind('test', report_id, 'old')
    return store, report_id


@pytest.mark.parametrize('operation', ('rename', 'delete'))
@pytest.mark.parametrize('failure', ('kinds.toml', 'shared.toml', 'local.toml', 'database', None))
def test_kind_changes_preserve_all_files_and_audit_references_on_failure(
    tmp_path: Path,
    config: entity_rules.EntityConfigStore,
    audit: tuple[EntityAuditStore, int],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: str | None,
) -> None:
    store, report_id = audit
    original = config.load()
    before = {path: path.read_bytes() for path in tmp_path.glob('*.toml')}
    rules = (
        original.with_renamed_kind('old', 'new', 'New', parent='root')
        if operation == 'rename'
        else original.with_deleted_kind('old')
    )
    target = 'new' if operation == 'rename' else 'root'
    replace = Path.replace

    def replace_file(path: Path, destination: Path) -> Path:
        if path.suffix == '.tmp' and destination.name == failure:
            raise PermissionError('simulated read-only target')
        return replace(path, destination)

    monkeypatch.setattr(Path, 'replace', replace_file)
    if failure == 'database':
        with sqlite3.connect(store.path) as conn:
            conn.execute("""CREATE TRIGGER reject_kind_change
                BEFORE UPDATE ON entity_kind_confirmations BEGIN
                SELECT RAISE(ABORT, 'simulated audit failure'); END""")
    if failure is None:
        kind_changes.change_kind(config, store, 'old', target, rules)
        assert config.load() == rules
    else:
        with pytest.raises(RuntimeError):
            kind_changes.change_kind(config, store, 'old', target, rules)
        assert config.load() == original
        assert all(path.read_bytes() == data for path, data in before.items())
    detail = store.entity_detail('test', report_id)
    assert detail.kind_confirmation is not None
    expected = target if failure is None else 'old'
    assert detail.kind_confirmation.kind == expected
    assert detail.variants[0].observations[0].kind == expected
    assert not tuple(tmp_path.glob('.*.bak'))
    assert not tuple(tmp_path.glob('.*.tmp'))


def test_audit_commit_failure_restores_files_and_rolls_back_references(
    config: entity_rules.EntityConfigStore,
    audit: tuple[EntityAuditStore, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, report_id = audit
    original = config.load()
    rules = original.with_renamed_kind('old', 'new', 'New', parent='root')
    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def commit(self) -> None:
            raise sqlite3.OperationalError('simulated commit failure')

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda path: connect(path, factory=FailingConnection))
        with pytest.raises(RuntimeError, match='commit failure'):
            kind_changes.change_kind(config, store, 'old', 'new', rules)
    assert config.load() == original
    detail = store.entity_detail('test', report_id)
    assert detail.kind_confirmation is not None and detail.kind_confirmation.kind == 'old'
    assert detail.variants[0].observations[0].kind == 'old'


@pytest.mark.parametrize('operation', ('rename', 'delete'))
def test_kind_failure_is_visible_in_ui_and_keeps_original_memory(
    config: entity_rules.EntityConfigStore,
    audit: tuple[EntityAuditStore, int],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store, report_id = audit
    app = EntityAuditApp(store, report_id, kind_store=config)
    original = config.load()
    messages: list[str] = []
    monkeypatch.setattr(app, 'notify', lambda message, **kwargs: messages.append(message))
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TRIGGER reject_kind_change BEFORE UPDATE ON variant_observations
            BEGIN SELECT RAISE(ABORT, 'simulated database error'); END""")

    async def run() -> None:
        async with app.run_test() as pilot:
            result = (
                app._save_kind_from_picker(NewKind('new', 'New', 'root', None), 'old')
                if operation == 'rename'
                else app._delete_kind_from_picker('old')
            )
            await pilot.pause()
            assert result is None
            assert app._rules == original

    asyncio.run(run())
    assert messages and 'simulated database error' in messages[-1]
    assert config.load() == original
