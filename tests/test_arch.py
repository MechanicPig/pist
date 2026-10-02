import ast
from pathlib import Path

PUBLIC_FILES = (*Path('berries/src/berries').rglob('*.py'),)
FORBIDDEN_PREFIXES = ('pist',)


def test_public_modules_do_not_import_application_or_audit_modules() -> None:
    assert PUBLIC_FILES, 'Public source directory must exist and contain Python modules.'
    violations: list[str] = []
    for path in PUBLIC_FILES:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...]
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = (node.module,)
            elif isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            else:
                continue
            for module in modules:
                if module.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f'{path}:{node.lineno}: {module}')

    assert violations == []
