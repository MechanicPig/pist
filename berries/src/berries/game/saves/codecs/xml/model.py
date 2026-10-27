"""Compiled, direct-lxml bindings for save boundary value models.

Bindings only describe fields, not progress ownership, read policies or I/O.
No input tree is retained and no intermediate XML object tree is created.
"""

from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from functools import cache, partial
from types import UnionType
from typing import Annotated, ClassVar, Protocol, Self, Union, Unpack, cast, get_args, get_origin

from lxml import etree
from pydantic import ConfigDict
from pydantic.fields import FieldInfo
from pydantic_core import SchemaValidator, core_schema

from berries.game.duration import Duration
from berries.game.saves.codecs import single, values
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.unknown import UnknownFieldPolicy, check_fields
from berries.model_behavior import FieldModel
from berries.models import ExternalModel

from . import tree

_NIL = f'{{{tree.XSI}}}nil'
_EMPTY: frozenset[str] = frozenset()
_MISSING = object()


class XmlDecoder(Protocol):
    def __call__(
        self, node: etree.Element, /, *, strict: bool, unknown: UnknownFieldPolicy, path: str
    ) -> object: ...


@dataclass(frozen=True)
class Xml:
    """Override a field's default alias binding; use Annotated metadata."""

    path: str | None
    encoder: Callable[..., str] | None = None
    nillable: bool = False
    decoder: XmlDecoder | None = None
    element_encoder: Callable[..., etree.Element] | None = None
    missing: object = _MISSING
    many: bool | None = None
    nil_as_missing: bool = False
    strict_required: bool = False


@dataclass(frozen=True)
class XmlReadContext:
    """Transient XML facts for field validation and dependent model initialization."""

    tag: str
    strict: bool
    path: str

    @classmethod
    def from_element(cls, root: etree.Element, *, strict: bool, path: str | None) -> Self:
        return cls(
            str(root.tag),
            strict,
            str(root.tag) if path is None else path,
        )


@dataclass
class _Shape:
    attributes: set[str] = field(default_factory=set)
    children: dict[str, _Shape] = field(default_factory=dict)
    many: bool = False
    collection: bool = False
    delegated: bool = False


@dataclass(frozen=True)
class _Rule:
    name: str
    alias: str
    groups: tuple[str, ...]
    leaf: str
    attribute: str | None
    many: bool
    encoder: Callable[..., str] | None
    nillable: bool
    decoder: XmlDecoder | None
    element_encoder: Callable[..., etree.Element] | None
    missing: object
    nil_as_missing: bool
    strict_required: bool

    @property
    def path(self) -> str:
        parts = (*self.groups, self.leaf) if self.many else self.groups
        if self.attribute is not None:
            parts = (*parts, f'@{self.attribute}')
        return '/'.join(parts)


def _value_type(annotation: object) -> object:
    """Unwrap metadata and a single nullable type, without guessing unions."""
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, UnionType):
            variants = tuple(item for item in get_args(annotation) if item is not type(None))
            if len(variants) != 1:
                return annotation
            annotation = variants[0]
        else:
            return annotation


def _model_type(annotation: object) -> type[XmlModel] | None:
    value = _value_type(annotation)
    return value if isinstance(value, type) and issubclass(value, XmlModel) else None


@cache
def _xml_fields(model: type[XmlModel]) -> dict[str, FieldInfo]:
    fields = model.model_fields.copy()
    if issubclass(model, FieldModel):
        fields.update(
            (name, info)
            for name, info in model.private_input_fields().items()
            if any(isinstance(item, Xml) for item in info.metadata)
        )
    return fields


@cache
def _plan(model: type[XmlModel]) -> tuple[tuple[_Rule, ...], _Shape]:
    rules = []
    shape = _Shape()
    used: set[tuple[str, ...]] = set()
    for name, info in _xml_fields(model).items():
        if info.exclude:
            continue
        alias = info.alias or name
        bindings = [item for item in info.metadata if isinstance(item, Xml)]
        if len(bindings) > 1:
            raise TypeError(f'Multiple XML bindings for {model.__name__}.{name}.')
        annotation = _value_type(info.annotation)
        collection = get_origin(annotation) in (list, set, frozenset)
        nested = _model_type(get_args(annotation)[0] if collection else annotation)
        binding = (
            bindings[0]
            if bindings
            else Xml(alias if model.xml_elements or nested is not None else f'@{alias}')
        )
        if binding.path is None:
            continue
        parts = tuple(binding.path.split('/'))
        if not all(parts) or any(part.startswith('@') for part in parts[:-1]):
            raise TypeError(f'Invalid XML binding {binding.path!r}.')
        if parts in used:
            raise TypeError(f'Duplicate XML binding {binding.path!r}.')
        used.add(parts)
        many = collection if binding.many is None else binding.many
        nested = _model_type(get_args(annotation)[0] if many and collection else annotation)
        decoder = binding.decoder
        element_encoder = binding.element_encoder
        if nested is not None and decoder is None:
            decoder = nested.from_xml
        attribute = parts[-1][1:] if parts[-1].startswith('@') else None
        elements = parts[:-1] if attribute is not None else parts
        if attribute == '' or (many and not elements):
            raise TypeError(f'Invalid XML attribute binding {binding.path!r}.')
        if nested is not None and element_encoder is None and elements:
            element_encoder = partial(nested.to_xml, name=elements[-1])
        if (decoder is None) != (element_encoder is None):
            raise TypeError(f'XML node bindings require both decoder and element_encoder: {name}.')
        if decoder is not None and attribute is not None:
            raise TypeError(f'XML node bindings require an element: {name}.')
        spec = shape
        parent = shape
        for part in elements:
            parent = spec
            spec = spec.children.setdefault(part, _Shape())
        if attribute is not None:
            spec.attributes.add(attribute)
        spec.many = spec.many or many
        spec.delegated = decoder is not None
        if many:
            parent.collection = True
        groups = elements[:-1] if many else elements
        rules.append(
            _Rule(
                name,
                alias,
                groups,
                elements[-1] if elements else '',
                attribute,
                many,
                binding.encoder,
                binding.nillable,
                decoder,
                element_encoder,
                binding.missing,
                binding.nil_as_missing,
                binding.strict_required,
            )
        )
    return tuple(rules), shape


def _check(
    node: etree.Element,
    shape: _Shape,
    path: str,
    unknown: UnknownFieldPolicy,
    attributes: AbstractSet[str] = _EMPTY,
    children: AbstractSet[str] = _EMPTY,
) -> None:
    if shape.delegated:
        return
    for name in node.attrib:
        if name != _NIL and name not in shape.attributes and name not in attributes:
            check_fields((f'@{name}',), _EMPTY, path=path, unknown=unknown)
    for child in node:
        if (
            isinstance(child.tag, str)
            and child.tag not in shape.children
            and child.tag not in children
        ):
            check_fields((child.tag,), _EMPTY, path=path, unknown=unknown)
    if not shape.children or (shape.collection and tree.is_nil(node)):
        return
    counts = {}
    for child in node:
        if not isinstance(child.tag, str):
            continue
        spec = shape.children.get(child.tag)
        if spec is None:
            continue
        i = counts.get(child.tag, 0)
        counts[child.tag] = i + 1
        if i and not spec.many:
            raise ValueError(f'Duplicate XML field {child.tag!r}.')
        child_path = f'{path}[{i}]' if spec.many else f'{path}.{child.tag}'
        _check(child, spec, child_path, unknown)


def _read(
    root: etree.Element, rule: _Rule, strict: bool, unknown: UnknownFieldPolicy, path: str
) -> object:
    node = root
    for part in rule.groups:
        found = next(node.iterchildren(tag=part), None)
        if found is None:
            return _MISSING
        node = found
    if strict and rule.strict_required and tree.is_nil(node):
        raise BadProgressSave(f'Missing or nil normal XML field {path}.{rule.path}.')
    if rule.many:
        if tree.is_nil(node):
            return _MISSING if rule.nil_as_missing else []
        if rule.decoder is not None:
            return [
                rule.decoder(
                    item,
                    strict=strict,
                    unknown=unknown,
                    path=f'{path}.{".".join(rule.groups)}[{i}]',
                )
                for i, item in enumerate(node.iterchildren(tag=rule.leaf))
            ]
        if rule.attribute is not None:
            return [item.get(rule.attribute, '') for item in node.iterchildren(tag=rule.leaf)]
        return [
            None if tree.is_nil(item) else tree.text(item)
            for item in node.iterchildren(tag=rule.leaf)
        ]
    if rule.attribute is not None:
        return node.get(rule.attribute, _MISSING)
    if rule.decoder is not None:
        value = rule.decoder(
            node, strict=strict, unknown=unknown, path=f'{path}.{".".join(rule.groups)}'
        )
        return _MISSING if rule.nil_as_missing and tree.is_nil(node) else value
    if rule.nil_as_missing and tree.is_nil(node):
        return _MISSING
    return None if rule.nillable and tree.is_nil(node) else tree.text(node)


def _encode(value: object, rule: _Rule) -> str:
    if rule.encoder is not None:
        return rule.encoder(value)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Duration):
        return values.format_duration(value)
    if isinstance(value, float):
        return single.format_value(value)
    if isinstance(value, (str, int)):
        return str(value)
    raise TypeError(f'An XML encoder is required for {type(value).__name__}.')


@cache
def _fields_validator(model: type[XmlModel]) -> SchemaValidator | None:
    """Reuse Pydantic's compiled field schema without constructing a domain object.

    Model-level validators may wrap the schema; retain full model validation in
    that case instead of silently omitting them. Field validators remain inside
    the model-fields schema. Definitions and validation config must travel with it.
    """
    schema = model.__pydantic_core_schema__
    definitions: list[core_schema.CoreSchema] = []
    while schema['type'] == 'definitions':
        wrapped = cast(core_schema.DefinitionsSchema, schema)
        definitions.extend(wrapped['definitions'])
        schema = wrapped['schema']
    if schema['type'] != 'model':
        return None
    model_schema = cast(core_schema.ModelSchema, schema)
    fields: core_schema.CoreSchema = model_schema['schema']
    if fields['type'] != 'model-fields':
        return None
    # Only explicitly bound private inputs participate. Unbound collection
    # defaults must not be built while validating persisted values on output.
    fields = cast(core_schema.ModelFieldsSchema, fields).copy()
    fields['fields'] = {
        name: field for name, field in fields['fields'].items() if name in _xml_fields(model)
    }
    if definitions:
        fields = core_schema.definitions_schema(fields, definitions)
    return SchemaValidator(fields, config=model_schema.get('config'))


def _validate_fields(
    model: type[XmlModel], data: dict[str, object]
) -> tuple[dict[str, object], set[str]]:
    validator = _fields_validator(model)
    if validator is None:
        value = model.model_validate(data)
        return {name: getattr(value, name) for name in _xml_fields(model)}, value.model_fields_set
    fields, _, present = cast(
        tuple[dict[str, object], object, set[str]], validator.validate_python(data)
    )
    return fields, present


def _read_fields(
    model: type[XmlModel],
    root: etree.Element,
    strict: bool,
    unknown: UnknownFieldPolicy,
    path: str | None,
    attributes: AbstractSet[str],
    children: AbstractSet[str],
) -> tuple[dict[str, object], set[str]]:
    if unknown not in ('error', 'warn'):
        raise ValueError(f'Invalid unknown-field policy: {unknown!r}.')
    if not model.xml_nillable and tree.is_nil(root):
        raise BadProgressSave(f'Nil {root.tag} cannot be decoded as {model.__name__}.')
    rules, shape = _plan(model)
    path = str(root.tag) if path is None else path
    _check(root, shape, path, unknown, attributes, children)
    data = {}
    defaulted: set[str] = set()
    for rule in rules:
        value = _read(root, rule, strict, unknown, path)
        if value is not _MISSING:
            data[rule.alias] = value
        elif strict and rule.strict_required:
            raise BadProgressSave(f'Missing or nil normal XML field {path}.{rule.path}.')
        elif rule.missing is not _MISSING:
            data[rule.alias] = rule.missing
            defaulted.add(rule.name)
    return data, defaulted


class XmlModel(ExternalModel):
    """Validated field snapshot with cached attribute/element binding rules.

    Aliases bind to attributes by default, or scalar elements with elements=True.
    Additional root names belong to the caller and are not recursively checked.
    Xml metadata supports simple paths and repeated scalar/attribute projections;
    nested domain objects remain coordinated by their own decoders.
    """

    xml_elements: ClassVar[bool] = False
    xml_tag: ClassVar[str | None] = None
    xml_nillable: ClassVar[bool] = True
    model_config = ConfigDict(validate_by_name=True)

    def __init_subclass__(
        cls, *, elements: bool | None = None, **kwargs: Unpack[ConfigDict]
    ) -> None:
        super().__init_subclass__(**kwargs)
        if elements is not None:
            cls.xml_elements = elements

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = False,
        unknown: UnknownFieldPolicy = 'error',
        path: str | None = None,
        attributes: AbstractSet[str] = _EMPTY,
        children: AbstractSet[str] = _EMPTY,
    ) -> Self:
        data, defaulted = cls._read_xml_fields(
            root,
            strict=strict,
            unknown=unknown,
            path=path,
            attributes=attributes,
            children=children,
        )
        context = XmlReadContext.from_element(root, strict=strict, path=path)
        result = cls.model_validate(data, context=context)
        result.__pydantic_fields_set__.difference_update(defaulted)
        return result

    @classmethod
    def _read_xml_fields(
        cls,
        root: etree.Element,
        *,
        strict: bool = False,
        unknown: UnknownFieldPolicy = 'error',
        path: str | None = None,
        attributes: AbstractSet[str] = _EMPTY,
        children: AbstractSet[str] = _EMPTY,
    ) -> tuple[dict[str, object], set[str]]:
        """Extract bound inputs for one subsequent model validation."""
        return _read_fields(cls, root, strict, unknown, path, attributes, children)

    def _write_xml(self, root: etree.Element, *, exclude_unset: bool = False) -> None:
        """Append bound fields to a newly constructed output node."""
        rules = _plan(type(self))[0]
        values, _ = _validate_fields(
            type(self),
            {
                info.alias or name: self._xml_value(name)
                for name, info in _xml_fields(type(self)).items()
            },
        )
        for rule in rules:
            if exclude_unset and rule.name not in self.model_fields_set:
                continue
            value = values[rule.name]
            if value is None and not rule.nillable:
                continue
            parent = root
            groups = rule.groups[:-1] if rule.decoder is not None and not rule.many else rule.groups
            for part in groups:
                found = next(parent.iterchildren(tag=part), None)
                if found is None:
                    found = etree.SubElement(parent, part)
                parent = found
            items: Iterable[object]
            if not rule.many or value is None:
                items = (value,)
            elif isinstance(value, (set, frozenset)):
                items = sorted(value, key=str)
            elif isinstance(value, list):
                items = value
            else:
                raise TypeError(f'Expected a collection for XML field {rule.name!r}.')
            if rule.element_encoder is not None:
                for item in items:
                    if item is None:
                        node = etree.Element(rule.leaf)
                        node.set(_NIL, 'true')
                    else:
                        node = rule.element_encoder(item)
                    node.tag = rule.leaf
                    parent.append(node)
                continue
            if rule.many and rule.attribute is not None and value is not None:
                # Resolve the attribute projection once, not once per entity.
                for item in items:
                    text = rule.encoder(item) if rule.encoder is not None else _encode(item, rule)
                    etree.SubElement(parent, rule.leaf).set(rule.attribute, text)
                continue
            for item in items:
                node = etree.SubElement(parent, rule.leaf) if rule.many else parent
                if item is None:
                    node.set(_NIL, 'true')
                elif rule.attribute is not None:
                    node.set(rule.attribute, _encode(item, rule))
                else:
                    node.text = _encode(item, rule)

    def _xml_value(self, name: str) -> object:
        """Project a field for output before validation, without changing its stored value."""
        return getattr(self, name)

    def to_xml(self, name: str | None = None, *, exclude_unset: bool = False) -> etree.Element:
        name = self.xml_tag if name is None else name
        if name is None:
            raise TypeError(f'An XML element name is required for {type(self).__name__}.')
        root = etree.Element(name)
        self._write_xml(root, exclude_unset=exclude_unset)
        return root
