"""Opt-in Pydantic comparison and copy policies declared alongside fields.

Use FieldOptions as Annotated metadata, independently of pydantic.Field.
PrivateAttr accepts the same policies; alias additionally declares validated input.
copy=False omits the value entirely; model_post_copy rebuilds derived state.
"""

from annotationlib import Format, get_annotations
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache, wraps
from inspect import Parameter, Signature
from keyword import iskeyword
from typing import Any, Literal, Self, Unpack, cast, dataclass_transform, overload

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler
from pydantic._internal._model_construction import ModelMetaclass  # noqa: PLC2701
from pydantic.fields import FieldInfo, ModelPrivateAttr
from pydantic_core import PydanticUndefined, core_schema


@dataclass(frozen=True)
class FieldOptions:
    """Comparison/copy metadata, without replacing Pydantic's Field signature."""

    compare: bool = True
    copy: bool = True


class _PrivateAttr(ModelPrivateAttr):
    def __init__(
        self,
        default: object,
        default_factory: Callable[[], object] | None,
        *,
        alias: str | None,
        compare: bool,
        copy: bool,
    ) -> None:
        if default is not PydanticUndefined and default_factory is not None:
            raise TypeError('cannot specify both default and default_factory')
        super().__init__(default, default_factory=default_factory)
        if alias is not None and (
            not alias.isidentifier() or iskeyword(alias) or alias.startswith('_')
        ):
            raise ValueError('PrivateAttr alias must be a public Python parameter name.')
        self.alias = alias
        self.compare = compare
        self.copy = copy

    def get_default(
        self, *, call_default_factory: bool = False, validated_data: dict[str, Any] | None = None
    ) -> object:
        # Aliased defaults are validated by the input schema, not evaluated a
        # second time by Pydantic's ordinary private-attribute initialization.
        if self.alias is not None:
            return PydanticUndefined
        if call_default_factory:
            return super().get_default(call_default_factory=True, validated_data=validated_data)
        return super().get_default()

    def initial_value(self) -> object:
        return super().get_default(call_default_factory=True)


@overload
def PrivateAttr[T](
    default: T, *, alias: str, init: Literal[True] = True, compare: bool = True, copy: bool = True
) -> T: ...


@overload
def PrivateAttr[T](
    *,
    alias: str,
    default_factory: Callable[[], T],
    init: Literal[True] = True,
    compare: bool = True,
    copy: bool = True,
) -> T: ...


@overload
def PrivateAttr(
    *, alias: str, init: Literal[True] = True, compare: bool = True, copy: bool = True
) -> Any: ...  # noqa: ANN401


@overload
def PrivateAttr[T](
    default: T,
    *,
    alias: None = None,
    init: Literal[False] = False,
    compare: bool = True,
    copy: bool = True,
) -> T: ...


@overload
def PrivateAttr[T](
    *,
    default_factory: Callable[[], T],
    alias: None = None,
    init: Literal[False] = False,
    compare: bool = True,
    copy: bool = True,
) -> T: ...


@overload
def PrivateAttr(
    *, alias: None = None, init: Literal[False] = False, compare: bool = True, copy: bool = True
) -> Any: ...  # noqa: ANN401


def PrivateAttr(
    default: object = PydanticUndefined,
    *,
    default_factory: Callable[[], object] | None = None,
    alias: str | None = None,
    init: bool | None = None,
    compare: bool = True,
    copy: bool = True,
) -> Any:
    """Declare private state, optionally accepting validated keyword input by alias.

    An alias participates in generated constructor signatures but not model_dump.
    Without an alias the attribute retains Pydantic's internal-state semantics.
    """
    if init is not None and init != (alias is not None):
        raise TypeError('Only aliased PrivateAttr declarations accept constructor input.')
    return _PrivateAttr(default, default_factory, alias=alias, compare=compare, copy=copy)


@dataclass(frozen=True)
class _PrivateInput:
    name: str
    attr: _PrivateAttr
    annotation: object


@cache
def _private_inputs(model: type[BaseModel]) -> tuple[_PrivateInput, ...]:
    attributes = [
        (name, attr)
        for name, attr in model.__private_attributes__.items()
        if isinstance(attr, _PrivateAttr) and attr.alias is not None
    ]
    if not attributes:
        return ()
    annotations = {}
    for base in reversed(model.__mro__):
        annotations.update(get_annotations(base, format=Format.FORWARDREF))
    used = set(model.model_fields)
    for info in model.model_fields.values():
        if isinstance(info.alias, str):
            used.add(info.alias)
    result = []
    for name, attr in attributes:
        assert attr.alias is not None
        if attr.alias in used:
            raise TypeError(f'Duplicate constructor alias {attr.alias!r}.')
        used.add(attr.alias)
        result.append(_PrivateInput(name, attr, annotations[name]))
    return tuple(result)


def _initialize_private_inputs(model: BaseModel, _: object, /) -> None:
    for item in _private_inputs(type(model)):
        alias = item.attr.alias
        assert alias is not None
        if alias in model.__dict__:
            assert model.__pydantic_private__ is not None
            model.__pydantic_private__[item.name] = cast(dict[str, object], model.__dict__).pop(
                alias
            )
        elif model.__pydantic_private__ is not None and item.name not in model.__pydantic_private__:
            value = item.attr.initial_value()
            if value is not PydanticUndefined:
                model.__pydantic_private__[item.name] = value


def _input_property(member: property, alias: str) -> property:
    setter = cast(Callable[[BaseModel, object], None], member.fset)

    @wraps(setter)
    def set_value(self: BaseModel, value: object) -> None:
        setter(self, value)
        self.__pydantic_fields_set__.add(alias)

    return member.setter(set_value)


@cache
def _excluded(model: type[BaseModel], option: str) -> tuple[frozenset[str], frozenset[str]]:
    fields = frozenset(
        name
        for name, field in model.model_fields.items()
        if any(
            isinstance(item, FieldOptions) and not getattr(item, option) for item in field.metadata
        )
    )
    private = frozenset(
        name
        for name, attr in model.__private_attributes__.items()
        if isinstance(attr, _PrivateAttr) and not getattr(attr, option)
    )
    return fields, private


def _hash_values(model: BaseModel) -> int:
    fields, _ = _excluded(type(model), 'compare')
    return hash(
        tuple(model.__dict__[name] for name in type(model).model_fields if name not in fields)
    )


@dataclass_transform(kw_only_default=True, field_specifiers=(Field, PrivateAttr))
class _FieldModelMeta(ModelMetaclass):
    """Declare constructor field specifiers without changing class creation."""


class FieldModel(BaseModel, metaclass=_FieldModelMeta):
    """BaseModel with opt-in field policies; other Pydantic models are unchanged."""

    model_post_init = _initialize_private_inputs

    def __init_subclass__(cls, **kwargs: Unpack[ConfigDict]) -> None:
        super().__init_subclass__(**kwargs)
        callback = vars(cls).get('model_post_init')
        if callback is None:
            return
        callback = cast(Callable[[BaseModel, object], None], callback)

        @wraps(callback)
        def model_post_init(self: BaseModel, context: object, /) -> None:
            # Pydantic has already wrapped the subclass callback at this point.
            # Initialize defaults before transferring validated aliases; its
            # wrapper will then retain this storage instead of initializing again.
            if self.__pydantic_private__ is None:
                values = {}
                for name, attr in self.__private_attributes__.items():
                    if attr.default_factory_takes_validated_data:
                        value = attr.get_default(
                            call_default_factory=True, validated_data={**self.__dict__, **values}
                        )
                    else:
                        value = attr.get_default(call_default_factory=True)
                    if value is not PydanticUndefined:
                        values[name] = value
                object.__setattr__(self, '__pydantic_private__', values)
            _initialize_private_inputs(self, context)
            callback(self, context)

        cls.model_post_init = model_post_init

    def iter_comparable_items(
        self, *, model: type[FieldModel] | None = None
    ) -> Iterator[tuple[str, object]]:
        """Yield declared comparison values, using constructor aliases for private state.

        Computed projections and undeclared extras are not additional fields.
        Private values without an alias retain their declared attribute names.
        A base model can limit the scope to its declarations and inherited fields.
        """
        model = type(self) if model is None else model
        if not isinstance(self, model):
            raise TypeError('Comparison scope must be a model base of this instance.')
        fields, private = _excluded(cast(type[BaseModel], model), 'compare')
        for name in model.model_fields:
            if name not in fields:
                yield name, self.__dict__.get(name, PydanticUndefined)
        values = self.__pydantic_private__ or {}
        for name, attr in model.__private_attributes__.items():
            if name not in private:
                alias = attr.alias if isinstance(attr, _PrivateAttr) else None
                yield alias or name, values.get(name, PydanticUndefined)

    @classmethod
    def private_input_fields(cls) -> dict[str, FieldInfo]:
        """Describe aliased private constructor inputs without exposing their storage."""
        result = {}
        for item in _private_inputs(cast(type[BaseModel], cls)):
            info = FieldInfo.from_annotation(cast(type, item.annotation))
            info.default = item.attr.default
            info.default_factory = item.attr.default_factory
            result[cast(str, item.attr.alias)] = info
        return result

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[BaseModel], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        schema = handler(source)
        inputs = _private_inputs(cast(type[BaseModel], cls))
        if not inputs:
            return schema
        node = schema
        while node['type'] in ('definitions', 'function-before', 'function-after', 'function-wrap'):
            wrapper = cast(
                core_schema.DefinitionsSchema
                | core_schema.BeforeValidatorFunctionSchema
                | core_schema.AfterValidatorFunctionSchema
                | core_schema.WrapValidatorFunctionSchema,
                node,
            )
            node = wrapper['schema']
        if node['type'] != 'model':
            raise TypeError('Aliased private inputs require a Pydantic model schema.')
        fields = cast(core_schema.ModelSchema, node)['schema']
        while fields['type'] in ('function-before', 'function-after', 'function-wrap'):
            fields = cast(
                core_schema.BeforeValidatorFunctionSchema
                | core_schema.AfterValidatorFunctionSchema
                | core_schema.WrapValidatorFunctionSchema,
                fields,
            )['schema']
        if fields['type'] != 'model-fields':
            raise TypeError('Aliased private inputs require model fields.')
        model_fields = cast(core_schema.ModelFieldsSchema, fields)['fields']
        for item in inputs:
            value = handler.generate_schema(item.annotation)
            attr = item.attr
            if attr.default is not PydanticUndefined or attr.default_factory is not None:
                value = core_schema.with_default_schema(
                    value, default_factory=attr.initial_value, validate_default=True
                )
            assert attr.alias is not None
            model_fields[attr.alias] = core_schema.model_field(value, serialization_exclude=True)
        return schema

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        cls._init_signature()
        for item in _private_inputs(cast(type[BaseModel], cls)):
            alias = cast(str, item.attr.alias)
            member = vars(cls).get(alias)
            if isinstance(member, property) and member.fset is not None:
                setattr(cls, alias, _input_property(member, alias))
        # Replace only Pydantic's generated frozen-model hash, never a caller's
        # explicit implementation. Excluded comparison fields must not affect it.
        hash_method = cls.__hash__
        if (
            cls.model_config.get('frozen')
            and _excluded(cast(type[BaseModel], cls), 'compare')[0]
            and hash_method is not None
            and hash_method.__module__.startswith('pydantic.')
        ):
            type.__setattr__(cls, '__hash__', _hash_values)

    @classmethod
    def _init_signature(cls) -> None:
        signature = cls.__signature__
        parameters = list(signature.parameters.values())
        variadic = [p for p in parameters if p.kind is Parameter.VAR_KEYWORD]
        parameters = [p for p in parameters if p.kind is not Parameter.VAR_KEYWORD]
        for item in _private_inputs(cast(type[BaseModel], cls)):
            parameters.append(
                Parameter(
                    cast(str, item.attr.alias),
                    Parameter.KEYWORD_ONLY,
                    default=item.attr
                    if item.attr.default_factory is not None
                    else item.attr.default
                    if item.attr.default is not PydanticUndefined
                    else Parameter.empty,
                    annotation=item.annotation,
                )
            )
        cls.__signature__ = Signature(
            [*parameters, *variadic], return_annotation=signature.return_annotation
        )

    @classmethod
    def model_rebuild(
        cls,
        *,
        force: bool = False,
        raise_errors: bool = True,
        _parent_namespace_depth: int = 2,
        _types_namespace: Mapping[str, object] | None = None,
    ) -> bool | None:
        result = super().model_rebuild(
            force=force,
            raise_errors=raise_errors,
            _parent_namespace_depth=_parent_namespace_depth + 1,
            _types_namespace=_types_namespace,
        )
        if result:
            cls._init_signature()
        return result

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BaseModel):
            return NotImplemented
        origin = self.__pydantic_generic_metadata__['origin'] or type(self)
        other_origin = other.__pydantic_generic_metadata__['origin'] or type(other)
        if origin is not other_origin:
            return False
        fields, private = _excluded(cast(type[BaseModel], type(self)), 'compare')
        if not fields and not private:
            return super().__eq__(other)
        if (self.__pydantic_extra__ or {}) != (other.__pydantic_extra__ or {}):
            return False
        return dict(self.iter_comparable_items()) == dict(
            cast(FieldModel, other).iter_comparable_items()
        )

    def _copy_values(self) -> Self:
        result = super().__copy__()
        fields, private = _excluded(cast(type[BaseModel], type(self)), 'copy')
        if fields:
            object.__setattr__(
                result,
                '__dict__',
                {name: value for name, value in result.__dict__.items() if name not in fields},
            )
            result.__pydantic_fields_set__.difference_update(fields)
        if result.__pydantic_private__ is not None:
            for name in private:
                result.__pydantic_private__.pop(name, None)  # pyright: ignore[reportUnusedCallResult]
        return result

    def __copy__(self) -> Self:
        result = self._copy_values()
        result.model_post_copy()
        return result

    def __deepcopy__(self, memo: dict[int, object] | None = None) -> Self:
        if memo is None:
            memo = {}
        result = self._copy_values()
        memo[id(self)] = result
        # Excluded state is removed before traversal, not copied and discarded.
        object.__setattr__(result, '__dict__', deepcopy(result.__dict__, memo))
        object.__setattr__(result, '__pydantic_extra__', deepcopy(result.__pydantic_extra__, memo))
        object.__setattr__(
            result, '__pydantic_private__', deepcopy(result.__pydantic_private__, memo)
        )
        result.model_post_copy()
        return result

    def model_post_copy(self) -> None:
        """Rebuild copy=False state after either shallow or deep copying."""
