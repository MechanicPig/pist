"""Archived static Loenn placement analysis kept for possible future reuse."""

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import cast
from zipfile import BadZipFile

from luaparser.ast import SyntaxException

from pist.game.binmap import AttrValue
from pist.game.mod_path import ModPath, iter_files

from .eval import LuaKey, LuaModule, LuaTable, LuaValue, evaluate
from .selene import SeleneSyntaxError, preprocess

LOENN_DIRNAME = 'Loenn'
LOENN_ENTITIES_DIRNAME = 'entities'
LOENN_LANG_DIRNAME = 'lang'
LANG_FILE_EXT = '.lang'
DEFAULT_LANGUAGE = 'en_gb'
BUILTIN_TEMPLATES_PATH = ('data', 'loenn_builtin_templates.toml')

LANG_ENTRY_PATTERN = re.compile(r'(?m)^(?P<key>[^#=\r\n][^=\r\n]*)=(?P<value>.*)$')
MOD_LANGUAGE_PATTERN = re.compile(r'^mods\.(?P<name>.+)\.name$')


@dataclass(frozen=True, slots=True)
class LoennPlacement:
    """One static entity placement declared for Loenn."""

    entity_name: str
    name: str
    attrs: dict[str, AttrValue]
    display_name: str
    source: str
    mod_name: str | None = None


@dataclass(frozen=True, slots=True)
class LoennMod:
    """One validated Mod package and its Everest metadata name."""

    path: ModPath
    metadata_name: str


@dataclass(frozen=True, slots=True)
class LoennWarning:
    """One Mod whose Loenn metadata could not be loaded safely."""

    source: str
    message: str


@dataclass(frozen=True, slots=True)
class _EntityDefinition:
    """One statically extracted entity table and its placement metadata."""

    entity_name: str
    placements: tuple[tuple[str, dict[str, AttrValue]], ...]
    associated_mods: tuple[str, ...]


class LoennRegistry:
    """Static placement metadata indexed by exact entity ID."""

    def __init__(
        self,
        placements: Iterable[LoennPlacement] = (),
        warnings: Iterable[LoennWarning] = (),
    ) -> None:
        entities: dict[str, list[LoennPlacement]] = {}
        for placement in placements:
            entities.setdefault(placement.entity_name, []).append(placement)
        self._entities = {
            entity_name: tuple(placements) for entity_name, placements in entities.items()
        }
        self.warnings = tuple(warnings)

    def placements_for(self, entity_name: str) -> tuple[LoennPlacement, ...]:
        """Return the known static Loenn placements for one exact entity ID."""
        return self._entities.get(entity_name, ())

    def entity_names(self) -> tuple[str, ...]:
        """Return entity IDs with at least one static Loenn placement."""
        return tuple(self._entities)

    def display_names_for(self, entity_name: str) -> tuple[str, ...]:
        """Return distinct Loenn display names for one exact entity ID."""
        return tuple(
            dict.fromkeys(placement.display_name for placement in self.placements_for(entity_name))
        )


def _builtin_placements() -> tuple[LoennPlacement, ...]:
    content = files('pist').joinpath(*BUILTIN_TEMPLATES_PATH).read_text(encoding='utf-8')
    data = tomllib.loads(content)
    templates = data.get('template')
    if not isinstance(templates, list):
        raise TypeError('Loenn built-in template data must contain a template list.')
    source = data.get('source')
    commit = source.get('commit') if isinstance(source, Mapping) else None
    if not isinstance(commit, str):
        raise TypeError('Loenn built-in template data must identify its source commit.')
    return tuple(_builtin_placement(template, commit=commit) for template in templates)


def _builtin_placement(data: object, *, commit: str) -> LoennPlacement:
    if not isinstance(data, Mapping):
        raise TypeError('Loenn built-in template must be a table.')
    entity_name = data.get('entity_name')
    name = data.get('name')
    display_name = data.get('display_name')
    source = data.get('source')
    attrs = data.get('attrs')
    if (
        not isinstance(entity_name, str)
        or not isinstance(name, str)
        or not isinstance(display_name, str)
        or not isinstance(source, str)
    ):
        raise TypeError('Loenn built-in template must have string metadata.')
    if not isinstance(attrs, Mapping) or not all(
        isinstance(key, str) and type(value) in {bool, int, float, str}
        for key, value in attrs.items()
    ):
        raise ValueError('Loenn built-in template has invalid attributes.')
    return LoennPlacement(
        entity_name=entity_name,
        name=name,
        attrs=dict(attrs),
        display_name=display_name,
        source=f'Loenn#{commit[:7]}/Loenn/{source}',
    )


def _builtin_modules(placements: Iterable[LoennPlacement]) -> dict[str, LuaValue]:
    """Build safe module tables for the bundled Loenn entity definitions."""
    strawberry_placements = tuple(
        placement for placement in placements if placement.entity_name == 'strawberry'
    )
    if not strawberry_placements:
        return {}
    return {
        'entities.strawberry': LuaTable(
            {
                'name': 'strawberry',
                'placements': LuaTable(
                    {
                        index: LuaTable(
                            {
                                'name': placement.name,
                                'data': LuaTable(
                                    cast(dict[LuaKey, LuaValue], placement.attrs.copy())
                                ),
                            }
                        )
                        for index, placement in enumerate(strawberry_placements, start=1)
                    }
                ),
            }
        )
    }


def load_loenn_registry(
    mods: Iterable[LoennMod], *, language: str = DEFAULT_LANGUAGE
) -> LoennRegistry:
    """Load static Loenn entity placements contributed by the given Mod packages."""
    mods = tuple(mods)
    mod_labels = _mod_labels(mods, language=language)
    builtin_placements = _builtin_placements()
    static_modules = _builtin_modules(builtin_placements)
    placements = list(builtin_placements)
    warnings: list[LoennWarning] = []
    for mod in mods:
        try:
            mod_placements, mod_warnings = _read_mod_placements(
                mod, language=language, mod_labels=mod_labels, static_modules=static_modules
            )
            placements.extend(mod_placements)
            warnings.extend(mod_warnings)
        except (BadZipFile, KeyError, OSError, UnicodeError, ValueError) as error:
            warnings.append(LoennWarning(source=mod.metadata_name, message=str(error)))
    return LoennRegistry(placements, warnings)


def _read_mod_placements(
    mod: LoennMod,
    *,
    language: str,
    mod_labels: Mapping[str, str],
    static_modules: Mapping[str, LuaValue],
) -> tuple[tuple[LoennPlacement, ...], tuple[LoennWarning, ...]]:
    mod_path = mod.path
    loenn_dir = _find_child(mod_path, LOENN_DIRNAME)
    if loenn_dir is None:
        return (), ()
    display_names = _read_display_names(loenn_dir, language=language)
    entities_dir = _find_child(loenn_dir, LOENN_ENTITIES_DIRNAME)
    if entities_dir is None:
        return (), ()
    placements: list[LoennPlacement] = []
    warnings: list[LoennWarning] = []
    for path in iter_files(entities_dir):
        if path.at.suffix.casefold() != '.lua':
            continue
        source = f'{mod_path.name}/{path.at.as_posix()}'
        try:
            placements.extend(
                _parse_entity_file(
                    _read_loenn_text(path),
                    display_names=display_names,
                    source=source,
                    mod_name=mod.metadata_name,
                    mod_labels=mod_labels,
                    static_modules=static_modules,
                )
            )
        except (SeleneSyntaxError, SyntaxException) as error:
            warnings.append(LoennWarning(source=source, message=str(error)))
    return tuple(placements), tuple(warnings)


def _read_display_names(loenn_dir: ModPath, *, language: str) -> dict[str, str]:
    lang_dir = _find_child(loenn_dir, LOENN_LANG_DIRNAME)
    if lang_dir is None:
        return {}
    filename = f'{language}{LANG_FILE_EXT}'
    lang_path = _find_child(lang_dir, filename)
    if lang_path is None:
        return {}
    return {
        match['key'].strip(): match['value'].strip()
        for match in LANG_ENTRY_PATTERN.finditer(_read_loenn_text(lang_path))
    }


def _mod_labels(mods: Iterable[LoennMod], *, language: str) -> dict[str, str]:
    """Read the human-readable Mod labels used by Loenn's entity registry."""
    labels = {mod.metadata_name: mod.metadata_name for mod in mods}
    for mod in mods:
        loenn_dir = _find_child(mod.path, LOENN_DIRNAME)
        if loenn_dir is None:
            continue
        for key, value in _read_display_names(loenn_dir, language=language).items():
            if (match := MOD_LANGUAGE_PATTERN.fullmatch(key)) is not None:
                labels[match['name']] = value
    return labels


def _parse_entity_file(
    content: str,
    *,
    display_names: dict[str, str],
    source: str,
    mod_name: str,
    mod_labels: Mapping[str, str],
    static_modules: Mapping[str, LuaValue],
) -> tuple[LoennPlacement, ...]:
    definitions = _module_definitions(evaluate(preprocess(content), modules=static_modules))
    return _placements_from_definitions(
        definitions,
        display_names=display_names,
        source=source,
        mod_name=mod_name,
        mod_labels=mod_labels,
    )


def _module_definitions(
    module: LuaModule,
) -> tuple[_EntityDefinition, ...]:
    """Extract entity definitions from known tables in one evaluated module."""
    tables: list[LuaTable] = []
    seen: set[int] = set()
    values = [*module.values.values(), *module.returns]
    while values:
        value = values.pop()
        if not isinstance(value, LuaTable) or id(value) in seen:
            continue
        tables.append(value)
        seen.add(id(value))
        values.extend(value.fields.values())
    return tuple(
        definition for table in tables if (definition := _entity_definition(table)) is not None
    )


def _entity_definition(table: LuaTable) -> _EntityDefinition | None:
    name = table.get('name')
    placements = table.get('placements')
    if not isinstance(name, str) or not isinstance(placements, LuaTable):
        return None
    return _EntityDefinition(name, _placements(placements), _associated_mods(table))


def _associated_mods(table: LuaTable) -> tuple[str, ...]:
    """Return one entity's statically declared Loenn ``associatedMods`` list."""
    associated_mods = table.get('associatedMods')
    if isinstance(associated_mods, str):
        return (associated_mods,)
    if not isinstance(associated_mods, LuaTable):
        return ()
    return tuple(
        value
        for index, value in sorted(associated_mods.fields.items())
        if type(index) is int and isinstance(value, str)
    )


def _placements(table: LuaTable) -> tuple[tuple[str, dict[str, AttrValue]], ...]:
    if isinstance(table.get('name'), str):
        return ((_placement_name(table), _attrs(table)),)
    return tuple(
        (_placement_name(value), _attrs(value))
        for index in range(1, len(table.fields) + 1)
        if isinstance(value := table.get(index), LuaTable) and isinstance(value.get('name'), str)
    )


def _placement_name(table: LuaTable) -> str:
    name = table.get('name')
    assert isinstance(name, str)
    return name


def _attrs(table: LuaTable) -> dict[str, AttrValue]:
    data = table.get('data')
    if not isinstance(data, LuaTable):
        return {}
    attrs: dict[str, AttrValue] = {}
    for name, value in data.fields.items():
        if isinstance(name, str) and type(value) in {bool, int, float, str}:
            attrs[name] = cast(AttrValue, value)
    return attrs


def _placements_from_definitions(
    definitions: Iterable[_EntityDefinition],
    *,
    display_names: dict[str, str],
    source: str,
    mod_name: str,
    mod_labels: Mapping[str, str],
) -> tuple[LoennPlacement, ...]:
    return tuple(
        LoennPlacement(
            entity_name=definition.entity_name,
            name=placement_name,
            attrs=attrs,
            display_name=display_names.get(
                f'entities.{definition.entity_name}.placements.name.{placement_name}',
                placement_name,
            ),
            source=source,
            mod_name=_format_mod_names(definition.associated_mods or (mod_name,), mod_labels),
        )
        for definition in definitions
        for placement_name, attrs in definition.placements
    )


def _format_mod_names(mod_names: Iterable[str], labels: Mapping[str, str]) -> str:
    """Format an entity's associated Mod names exactly once each."""
    return ' + '.join(
        sorted(dict.fromkeys(labels.get(name, name) for name in mod_names), key=str.casefold)
    )


def _find_child(root: ModPath, name: str) -> ModPath | None:
    if not root.is_dir():
        return None
    return next((path for path in root.iterdir() if path.name.casefold() == name.casefold()), None)


def _read_loenn_text(path: ModPath) -> str:
    """Read Mod-provided Loenn metadata without rejecting legacy ANSI files."""
    data = path.read_bytes()
    for encoding in ('utf-8-sig', 'gb18030', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode('latin-1')
