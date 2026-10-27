"""Field policies affect values and copies, independently of serialization."""

from copy import copy, deepcopy
from inspect import signature
from typing import Annotated

import pytest
from pydantic import ConfigDict, Field, ValidationError

from berries.model_behavior import FieldModel, FieldOptions, PrivateAttr


class _Derived:
    def __deepcopy__(self, memo: dict[int, object]) -> _Derived:
        raise AssertionError('Derived state must not be traversed by deepcopy')

    def __eq__(self, other: object) -> bool:
        raise AssertionError('Derived state must not participate in equality')


class _Example(FieldModel):
    values: list[int] = Field(default_factory=list, alias='Values')
    label: Annotated[str, FieldOptions(compare=False)] = 'label'
    cache: Annotated[int, FieldOptions(compare=False, copy=False)] = 0
    _derived: _Derived = PrivateAttr(compare=False, copy=False)
    _history: list[int] = PrivateAttr(default_factory=list, compare=False)
    _state: list[int] = PrivateAttr(default_factory=list)

    def model_post_init(self, context: object, /) -> None:
        self._derived = _Derived()

    def model_post_copy(self) -> None:
        self._derived = _Derived()
        self.cache = len(self.values)

    @property
    def history(self) -> list[int]:
        return self._history

    @property
    def state(self) -> list[int]:
        return self._state


class _Child(_Example):
    extra_value: int = 0


class _Generic[T](FieldModel):
    value: T
    noise: Annotated[int, FieldOptions(compare=False)] = 0


def test_comparison_policies_are_independent_of_serialization_and_aliases() -> None:
    old = _Example(Values=[1])
    new = _Example(Values=[1], label='other', cache=42)
    new.history.append(1)
    assert old == new
    assert new.model_dump(by_alias=True) == {'Values': [1], 'label': 'other', 'cache': 42}
    new.state.append(1)
    assert old != new
    new.state.clear()
    new.values.append(2)
    assert old != new


def test_comparable_items_include_inherited_and_private_values_using_metadata() -> None:
    value = _Child(Values=[1], label='ignored', extra_value=4)
    value.history.append(8)
    value.state.append(2)
    assert dict(value.iter_comparable_items()) == {
        'values': [1],
        'extra_value': 4,
        '_state': [2],
    }
    assert dict(value.iter_comparable_items(model=_Example)) == {'values': [1], '_state': [2]}
    with pytest.raises(TypeError, match='model base'):
        dict(value.iter_comparable_items(model=_Aliased))
    aliased = _Aliased(values=[3])
    assert dict(aliased.iter_comparable_items()) == {'name': 'example', 'values': [3]}


def test_private_input_property_setters_track_only_successful_explicit_writes() -> None:
    class Value(FieldModel):
        writes: int = 0
        _amount: int = PrivateAttr(alias='amount', default=0)

        def model_post_init(self, _: object, /) -> None:
            self._amount += 3

        @property
        def amount(self) -> int:
            return self._amount

        @amount.setter
        def amount(self, value: int) -> None:
            if value < 0:
                raise ValueError('Negative amount')
            self._amount = value
            self.writes += 1

    class Child(Value):
        pass

    value = Child()
    assert value.amount == 3 and 'amount' not in value.model_fields_set
    with pytest.raises(ValueError):
        value.amount = -1
    assert value.amount == 3 and 'amount' not in value.model_fields_set
    value.amount = 7
    assert value.amount == 7 and value.writes == 1
    assert 'amount' in value.model_fields_set


@pytest.mark.parametrize('deep', (False, True))
@pytest.mark.parametrize('model_copy', (False, True))
def test_copy_rebuilds_excluded_values_and_keeps_non_comparing_history(
    deep: bool, model_copy: bool
) -> None:
    original = _Example(Values=[1], cache=100)
    original.history.append(7)
    original.state.append(9)
    clone = original.model_copy(deep=deep) if model_copy else (deepcopy if deep else copy)(original)
    assert clone == original
    assert original.cache == 100
    assert clone.cache == 1
    assert clone.history == [7]
    assert clone.state == [9]
    assert (clone.values is original.values) is not deep
    assert (clone.history is original.history) is not deep
    assert (clone.state is original.state) is not deep


def test_inherited_policies_and_concrete_types() -> None:
    old = _Child(Values=[1])
    new = _Child(Values=[1], label='other', cache=2)
    assert old == new
    assert copy(old) == old == deepcopy(old)
    new.extra_value = 1
    assert old != new
    assert old != _Example(Values=[1])
    assert old != {'Values': [1]}


def test_generic_origins_follow_pydantic_equality() -> None:
    assert _Generic[int](value=1) == _Generic(value=1, noise=2)


def test_extra_values_still_participate_in_equality_and_copy() -> None:
    class Example(_Example):
        model_config = ConfigDict(extra='allow')

    old = Example.model_validate({'Values': [], 'extra': [1]})
    new = Example.model_validate({'Values': [], 'extra': [1], 'label': 'other'})
    assert old == new == deepcopy(old)
    assert new.model_extra is not None
    new.model_extra['extra'] = [1, 2]
    assert old != new


def test_deepcopy_preserves_shared_references_and_cycles() -> None:
    class Linked(FieldModel):
        values: list[int]
        alias: list[int]
        link: Linked | None = None
        cache: Annotated[int, FieldOptions(compare=False, copy=False)] = 0

        def model_post_copy(self) -> None:
            self.cache = len(self.values)

    old = Linked(values=[1], alias=[])
    old.alias = old.values
    old.link = old
    new = deepcopy(old)
    assert new.link is new
    assert new.values is new.alias
    assert new.values is not old.values
    assert new.cache == 1


def test_frozen_model_hash_matches_comparison_fields() -> None:
    class Frozen(FieldModel):
        model_config = ConfigDict(frozen=True)
        value: int
        noise: Annotated[int, FieldOptions(compare=False)] = 0

    old = Frozen(value=1)
    new = Frozen(value=1, noise=2)
    assert old == new
    assert hash(old) == hash(new)


def test_explicit_hash_is_not_replaced() -> None:
    class Frozen(FieldModel):
        model_config = ConfigDict(frozen=True)
        value: Annotated[int, FieldOptions(compare=False)] = 0

        def __hash__(self) -> int:
            return 42

    assert hash(Frozen()) == 42


class _Aliased(FieldModel):
    model_config = ConfigDict(extra='forbid')
    name: str = 'example'
    _values: list[int] = PrivateAttr(alias='values', default_factory=list)
    _cache: int = PrivateAttr(default=0, compare=False, copy=False)

    def model_post_init(self, _: object, /) -> None:
        self._cache = sum(self.values)

    def model_post_copy(self) -> None:
        self._cache = sum(self.values)

    @property
    def values(self) -> list[int]:
        return self._values

    @property
    def total(self) -> int:
        return self._cache


def test_private_alias_is_validated_before_user_initialization() -> None:
    class Child(_Aliased):
        extra: int = 0

    value = Child(values=[1, 2], extra=3)
    assert value.values == [1, 2]
    assert value.total == 3
    assert Child.model_validate({'values': [1, 2], 'extra': 3}) == value
    assert Child.model_validate_json('{"values": [1, 2], "extra": 3}') == value
    assert value.model_dump() == {'name': 'example', 'extra': 3}
    assert 'values' not in vars(value)
    assert 'values' in value.model_fields_set
    assert set(signature(Child).parameters) == {'name', 'extra', 'values'}
    with pytest.raises(ValidationError):
        Child.model_validate({'values': ['invalid']})
    with pytest.raises(ValidationError):
        Child.model_validate({'_values': []})
    with pytest.raises(AttributeError):
        value.values = []  # pyright: ignore[reportAttributeAccessIssue]


def test_private_alias_default_factory_runs_only_for_missing_input() -> None:
    calls = []

    def make_values() -> list[int]:
        calls.append(1)
        return [1]

    class Example(FieldModel):
        _values: list[int] = PrivateAttr(alias='values', default_factory=make_values)

        @property
        def values(self) -> list[int]:
            return self._values

    assert Example().values == [1]
    assert calls == [1]
    assert Example(values=[2]).values == [2]
    assert calls == [1]


def test_private_defaults_and_aliases_are_ready_before_inherited_post_init() -> None:
    events = []

    def make_history() -> list[int]:
        events.append('default')
        return []

    class Parent(FieldModel):
        _amount: int = PrivateAttr(alias='amount')
        _history: list[int] = PrivateAttr(default_factory=make_history)

        def model_post_init(self, context: object, /) -> None:
            events.append('parent')
            self._history.append(self._amount)

        @property
        def history(self) -> list[int]:
            return self._history

    class Child(Parent):
        def model_post_init(self, context: object, /) -> None:
            super().model_post_init(context)
            events.append('child')
            self._history.append(self._amount + 1)

    assert Child(amount=3).history == [3, 4]
    assert events == ['default', 'parent', 'child']


def test_private_alias_required_input_and_defaults() -> None:
    class Example(FieldModel):
        _value: int = PrivateAttr(alias='value')
        _values: list[int] = PrivateAttr(default=[1], alias='values')

    with pytest.raises(ValidationError, match='value'):
        Example.model_validate({})
    first = Example(value=1)
    second = Example(value=1)
    assert first == second
    assert signature(Example).parameters['value'].default is signature(Example).empty


def test_private_alias_copy_and_comparison_use_private_storage() -> None:
    value = _Aliased(values=[1])
    shallow = copy(value)
    deep = deepcopy(value)
    assert value == shallow == deep
    assert shallow.values is value.values
    assert deep.values is not value.values
    assert deep.total == 1
    deep.values.append(2)
    assert deep != value


def test_private_alias_signature_survives_schema_rebuild() -> None:
    class Example(_Aliased):
        extra: int = 0

    assert Example.model_rebuild(force=True)
    assert set(signature(Example).parameters) == {'name', 'extra', 'values'}
    assert Example(values=[2]).total == 2


def test_private_alias_rejects_conflicting_names() -> None:
    with pytest.raises(TypeError, match='Duplicate constructor alias'):

        class Example(FieldModel):
            value: int = 0
            _value: int = PrivateAttr(0, alias='value')

    with pytest.raises(ValueError, match='public Python parameter'):
        PrivateAttr(alias='_private')
