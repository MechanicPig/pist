import ast
from importlib.util import resolve_name
from pathlib import Path

PUBLIC_FILES = (*Path('berries/src/berries').rglob('*.py'),)
FORBIDDEN_PREFIXES = ('pist',)


def _import_target(path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ''
    pkg = '.'.join(path.parent.parts[2:])
    return resolve_name('.' * node.level + (node.module or ''), pkg)


def test_public_modules_do_not_import_application_or_audit_modules() -> None:
    assert PUBLIC_FILES, 'Public source directory must exist and contain Python modules.'
    violations: list[str] = []
    for path in PUBLIC_FILES:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...]
            if isinstance(node, ast.ImportFrom):
                modules = (_import_target(path, node),)
            elif isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            else:
                continue
            for module in modules:
                if module.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f'{path}:{node.lineno}: {module}')

    assert violations == []


def test_raw_map_reader_does_not_depend_on_map_interpretation() -> None:
    path = Path('berries/src/berries/game/map_data.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    forbidden = ('berries.entities', 'berries.map_layout', 'berries.map_preview', 'pist')
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _import_target(path, node)
            assert not module.startswith(forbidden)
            if module == 'berries':
                assert not any(
                    alias.name in {'entities', 'map_layout', 'map_preview'} for alias in node.names
                )
        elif isinstance(node, ast.Import):
            assert not any(alias.name.startswith(forbidden) for alias in node.names)


def test_sheet_protocol_does_not_depend_on_personal_records_or_ui() -> None:
    for name in ('models', 'client'):
        path = Path(f'pist/src/pist/smartsheet/{name}.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        forbidden = ('pist.records', 'pist.ui', 'textual')
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _import_target(path, node)
                assert not module.startswith(forbidden), str(path)
                if module == 'pist':
                    assert not any(alias.name in {'records', 'ui'} for alias in node.names)
                if module == 'pist.smartsheet':
                    assert not any(
                        alias.name in {'encoding', 'fields', 'report'} for alias in node.names
                    )
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith(forbidden) for alias in node.names), str(path)


def test_record_domain_does_not_import_ui() -> None:
    paths = tuple(Path('pist/src/pist/records').glob('*.py'))
    assert paths
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _import_target(path, node)
                assert not module.startswith(('pist.ui', 'textual')), str(path)
                if module == 'pist':
                    assert not any(alias.name == 'ui' for alias in node.names), str(path)
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith(('pist.ui', 'textual')) for alias in node.names
                ), str(path)


def test_save_codecs_and_operations_keep_their_dependency_direction() -> None:
    saves = Path('berries/src/berries/game/saves')
    boundaries = (
        ('codecs', ('berries.game.saves.files', 'berries.game.saves.operations')),
        ('operations', ('lxml', 'yaml')),
    )
    for directory, forbidden in boundaries:
        paths = tuple((saves / directory).rglob('*.py'))
        assert paths, directory
        for path in paths:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = _import_target(path, node)
                    modules = (module, *(f'{module}.{alias.name}' for alias in node.names))
                elif isinstance(node, ast.Import):
                    modules = tuple(alias.name for alias in node.names)
                else:
                    continue
                assert not any(
                    module == prefix or module.startswith(f'{prefix}.')
                    for module in modules
                    for prefix in forbidden
                ), f'{path}:{node.lineno}'
