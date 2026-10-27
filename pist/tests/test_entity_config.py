"""Application-owned entity rule loading, publishing and persistence."""

import tomllib
from pathlib import Path

import pytest
import tomlkit

from berries.entities import rules as entity_rules
from pist.entities import config
from pist.entities.maintenance import EntityRuleLibrary


def _split_config(path: Path) -> Path:
    """Move a compact test fixture's kinds table into its required sibling file."""
    config = tomllib.loads(path.read_text(encoding='utf-8'))
    kinds = config.pop('kinds', {})
    kinds_path = path.with_name('kinds.toml')
    kinds_path.write_text(tomlkit.dumps({'kinds': kinds}), encoding='utf-8')
    path.write_text(tomlkit.dumps(config), encoding='utf-8')
    return kinds_path


def test_entity_rules_can_add_a_kind_under_an_existing_parent(tmp_path: Path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'
""",
        encoding='utf-8',
    )
    store = EntityRuleLibrary(config_path, kinds_path=_split_config(config_path))

    rules = store.load().with_kind(
        'customberry',
        entity_rules.EntityKind(label='Custom Berry', parent='berry', sprite='customberry.png'),
    )
    store.save(rules)

    loaded = store.load()
    assert loaded.kinds['customberry'].label == 'Custom Berry'
    assert loaded.kinds['customberry'].parent == 'berry'
    assert loaded.kinds['customberry'].sprite == 'customberry.png'
    assert 'customberry' in loaded.leaf_kind_names


def test_shared_store_renames_kinds_without_writing_local_rules(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    shared_path = tmp_path / 'shared.toml'
    local_path = tmp_path / 'local.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.goldenberry]
label = 'Golden Berry'
parent = 'strawberry'
""",
        encoding='utf-8',
    )
    shared_path.write_text(
        """[entities.strawberry]
rules = [{ kind = 'strawberry' }]

[entities."Example/Collectible"]
rules = [{ kind = 'strawberry', when = { moon = false } }]
""",
        encoding='utf-8',
    )
    local_path.write_text(
        """[entities."Local/Collectible"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    store = EntityRuleLibrary(shared_path, kinds_path=kinds_path)

    renamed = store.load().with_renamed_kind(
        'strawberry', 'redberry', entity_rules.EntityKind(label='Red Berry', parent='berry')
    )
    store.rename_kind('strawberry', 'redberry', renamed)

    loaded = store.load()
    assert loaded.kinds['goldenberry'].parent == 'redberry'
    assert loaded.entity_kind('strawberry', {}) == 'redberry'
    assert loaded.entity_kind('Example/Collectible', {'moon': False}) == 'redberry'
    assert loaded.entity_kind('Local/Collectible', {}) is None
    assert 'strawberry' in local_path.read_text(encoding='utf-8')

    deleted = loaded.with_deleted_kind('redberry')
    store.delete_kind('redberry', 'berry', deleted)

    loaded = store.load()
    assert 'redberry' not in loaded.kinds
    assert loaded.kinds['goldenberry'].parent == 'berry'
    assert loaded.entity_kind('strawberry', {}) == 'berry'
    assert loaded.entity_kind('Example/Collectible', {'moon': False}) == 'berry'
    assert loaded.entity_kind('Local/Collectible', {}) is None
    assert 'strawberry' in local_path.read_text(encoding='utf-8')


def test_rule_store_updates_a_template_condition_without_duplicate(tmp_path: Path) -> None:
    source_path = tmp_path / 'entities.toml'
    source_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    store = EntityRuleLibrary(source_path, kinds_path=_split_config(source_path))

    rules = store.load().with_rule('TestHelper/Berry', 'strawberry', {'moon': True})
    store.save(rules)
    reloaded = store.load()

    assert [rule.when for rule in reloaded.entities['TestHelper/Berry'].rules] == [
        {'moon': True},
        {},
    ]
    updated = reloaded.with_rule('TestHelper/Berry', 'strawberry', {'moon': True})
    assert len(updated.entities['TestHelper/Berry'].rules) == 2


def test_local_rule_layer_adds_conditions_without_copying_shared_rules(tmp_path: Path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.moonberry]
label = 'Moonberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[entities."TestHelper/Berry"]
rules = [{ kind = 'moonberry', when = { moon = true } }]
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(shared_path)
    store = config.EntityConfigStore(
        shared_path=shared_path, kinds_path=kinds_path, path=local_path
    )
    rules = store.load().with_rule('TestHelper/Berry', 'moonberry', {'golden': True})
    store.save(rules)

    reloaded = config.load_entity_rule_layers(shared_path, local_path, kinds_path=kinds_path)
    assert reloaded.entity_kind('TestHelper/Berry', {}) == 'strawberry'
    assert reloaded.entity_kind('TestHelper/Berry', {'moon': True}) == 'moonberry'
    assert reloaded.entity_kind('TestHelper/Berry', {'golden': True}) == 'moonberry'
    assert '[kinds.strawberry]' not in local_path.read_text(encoding='utf-8')


def test_local_entity_layer_rejects_kind_definitions(tmp_path: Path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.heart]
label = 'Heart'
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[kinds.collectible]
label = 'Collectible'

[kinds.heart]
label = 'Local heart'
parent = 'collectible'
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(shared_path)
    with pytest.raises(ValueError, match='Invalid entity rules config layers'):
        config.load_entity_rule_layers(shared_path, local_path, kinds_path=kinds_path)


def test_local_rule_layer_reports_conflicting_shared_rule_overrides(tmp_path: Path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.moonberry]
label = 'Moonberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[entities."TestHelper/Berry"]
rules = [{ kind = 'moonberry' }]
""",
        encoding='utf-8',
    )

    kinds_path = _split_config(shared_path)
    store = config.EntityConfigStore(
        shared_path=shared_path, kinds_path=kinds_path, path=local_path
    )
    rules = store.load()

    assert rules.entity_kind('TestHelper/Berry', {}) == 'moonberry'
    assert len(store.conflicts) == 1
    conflict = store.conflicts[0]
    assert conflict.entity_name == 'TestHelper/Berry'
    assert conflict.conditions.when == ()
    assert conflict.conditions.meta == ()
    assert conflict.shared_kind == 'strawberry'
    assert conflict.local_kind == 'moonberry'


def test_shared_kind_library_is_not_written_into_shared_entity_rules(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'
""",
        encoding='utf-8',
    )
    entities_path = tmp_path / 'entities.toml'
    entities_path.write_text('', encoding='utf-8')
    store = EntityRuleLibrary(entities_path, kinds_path=kinds_path)

    store.save(store.load().with_rule('TestHelper/Berry', 'strawberry', {}))

    assert 'kinds' not in tomllib.loads(entities_path.read_text(encoding='utf-8'))
    assert tomllib.loads(kinds_path.read_text(encoding='utf-8'))['kinds']['strawberry'] == {
        'label': 'Strawberry',
        'parent': 'berry',
    }


def test_rule_library_publishes_generated_rules(tmp_path: Path) -> None:
    kinds_path = tmp_path / 'kinds.toml'
    kinds_path.write_text(
        """[kinds.berry]
label = 'Berry'
""",
        encoding='utf-8',
    )
    shared_path = tmp_path / 'entities.toml'
    shared_path.write_text('', encoding='utf-8')
    store = EntityRuleLibrary(shared_path, kinds_path=kinds_path)
    generated = entity_rules.EntityRuleLayer(
        entities={
            'TestHelper/Berry': entity_rules.EntityRulesForId(
                rules=(entity_rules.EntityRule(kind='berry'),)
            )
        }
    )

    store.save_layer(generated)

    assert store.load_layer() == generated
    assert store.load().entity_kind('TestHelper/Berry', {}) == 'berry'


@pytest.mark.parametrize('operation', ('add', 'edit', 'rename', 'delete'))
def test_user_category_changes_are_disabled(tmp_path: Path, operation: str) -> None:
    shared = tmp_path / 'shared.toml'
    kinds = tmp_path / 'shared-kinds.toml'
    local = tmp_path / 'local.toml'
    shared.write_text('[entities.test]\nrules = [{kind="builtin"}]\n', encoding='utf-8')
    kinds.write_text(
        '[kinds.root]\nlabel="Root"\n[kinds.builtin]\nlabel="Builtin"\nparent="root"\n',
        encoding='utf-8',
    )
    store = config.EntityConfigStore(shared_path=shared, kinds_path=kinds, path=local)
    rules = store.load().with_rule('Example/Custom', 'builtin', {})
    store.save(rules)  # Local rule overrides remain supported.
    before = {path: path.read_bytes() for path in (shared, kinds, local)}
    assert not hasattr(store, 'save_layer')
    assert not hasattr(store, 'kind_change')
    changed = (
        rules.with_kind('custom', entity_rules.EntityKind(label='Custom', parent='root'))
        if operation == 'add'
        else rules.with_renamed_kind(
            'builtin', 'builtin', entity_rules.EntityKind(label='Changed', parent='root')
        )
        if operation == 'edit'
        else rules.with_renamed_kind('builtin', 'other', entity_rules.EntityKind(label='Other'))
        if operation == 'rename'
        else rules.with_deleted_kind('builtin')
    )
    with pytest.raises(ValueError, match='Local kinds are disabled'):
        store.save(changed)
    assert store.load() == rules
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize('target', ('shared.toml', 'kinds.toml'))
def test_user_store_rejects_shared_files_as_local_override_path(
    tmp_path: Path, target: str
) -> None:
    with pytest.raises(ValueError, match='separate file'):
        config.EntityConfigStore(
            tmp_path / target,
            shared_path=tmp_path / 'shared.toml',
            kinds_path=tmp_path / 'kinds.toml',
        )


def test_shared_maintenance_can_add_categories_and_generate_rules(tmp_path: Path) -> None:
    shared = tmp_path / 'shared.toml'
    kinds = tmp_path / 'kinds.toml'
    shared.write_text('[entities]\n', encoding='utf-8')
    kinds.write_text('[kinds.root]\nlabel="Root"\n', encoding='utf-8')
    store = EntityRuleLibrary(shared, kinds_path=kinds)
    store.save(
        store.load().with_kind('custom', entity_rules.EntityKind(label='Custom', parent='root'))
    )
    generated = entity_rules.EntityRuleLayer.model_validate(
        {'entities': {'Example/Custom': {'rules': [{'kind': 'custom'}]}}}
    )
    store.save_layer(generated)
    assert store.load_layer() == generated
    assert store.load().entity_kind('Example/Custom', {}) == 'custom'


@pytest.mark.parametrize('operation', ('local', 'shared', 'publish'))
def test_ordinary_rule_writes_propagate_errors_without_restoring_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    shared = tmp_path / 'shared.toml'
    kinds = tmp_path / 'kinds.toml'
    local = tmp_path / 'nested' / 'local.toml'
    shared.write_text('[entities]\n', encoding='utf-8')
    kinds.write_text('[kinds.root]\nlabel="Root"\n', encoding='utf-8')
    library = EntityRuleLibrary(shared, kinds_path=kinds)
    rules = library.load().with_rule('Example/Berry', 'root', {})
    target = local if operation == 'local' else shared
    write_text = Path.write_text

    def interrupted_write(path: Path, text: str, *, encoding: str) -> int:
        if path == target:
            write_text(path, 'partial', encoding=encoding)
            raise PermissionError('simulated write interruption')
        return write_text(path, text, encoding=encoding)

    monkeypatch.setattr(Path, 'write_text', interrupted_write)

    def save() -> None:
        if operation == 'local':
            config.EntityConfigStore(local, shared_path=shared, kinds_path=kinds).save(rules)
        elif operation == 'shared':
            library.save(rules)
        else:
            library.save_layer(entity_rules.EntityRuleLayer(entities=rules.entities))

    with pytest.raises(PermissionError, match='write interruption'):
        save()

    assert target.read_text(encoding='utf-8') == 'partial'
    assert not tuple(tmp_path.rglob('*.bak'))
    assert not tuple(tmp_path.rglob('*.tmp'))
