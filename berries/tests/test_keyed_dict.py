"""Derived-key invariants with ordinary mapping writes."""

from collections import UserDict
from collections.abc import Mapping, MutableMapping
from copy import copy, deepcopy
from operator import itemgetter

import pytest

from berries.containers import KeyedDict

type _Item = tuple[str, int]


def test_mapping_reads_and_equality_use_keys_and_values() -> None:
    values: KeyedDict[str, _Item] = KeyedDict([('a', 1), ('b', 2)], key=itemgetter(0))
    assert isinstance(values, Mapping) and not isinstance(values, MutableMapping)
    assert list(values) == ['a', 'b'] and 'a' in values
    assert ('a', 1) not in values
    assert list(values.values()) == [('a', 1), ('b', 2)]
    assert list(values.items()) == [('a', ('a', 1)), ('b', ('b', 2))]
    assert values.get('a') == ('a', 1)
    assert values.get('missing') is None and values.get('missing', 42) == 42
    assert values == {'a': ('a', 1), 'b': ('b', 2)}
    assert values != {'a': ('a', 9), 'b': ('b', 2)}
    for name in (
        '__setitem__',
        '__existing__',
        '__or__',
        '__and__',
        '__sub__',
        '__xor__',
        '__ior__',
        'discard',
        'remove',
        'isdisjoint',
    ):
        assert not hasattr(values, name)


def test_membership_queries_stored_keys_without_looking_up_values() -> None:
    class NoLookup(KeyedDict[str, _Item]):
        def __getitem__(self, key: str, /) -> _Item:
            raise AssertionError('Membership must not fetch a value.')

    values = NoLookup([('a', 1)], key=itemgetter(0))
    assert 'a' in values
    assert 'missing' not in values
    assert ('a', 1) not in values
    with pytest.raises(TypeError, match='unhashable'):
        [] in values  # noqa: B015  # pyright: ignore[reportUnusedExpression]


def test_constructor_add_update_and_replace_overwrite_without_moving_keys() -> None:
    values: KeyedDict[str, _Item] = KeyedDict([('a', 1), ('b', 2), ('a', 3)], key=itemgetter(0))
    assert values['a'] == ('a', 3)
    values.add(('a', 4))
    values.update([('b', 5)], (item for item in [('c', 6)]))
    assert list(values.values()) == [('a', 4), ('b', 5), ('c', 6)]
    values.replace(('a', 7))
    assert list(values.values()) == [('a', 7), ('b', 5), ('c', 6)]
    with pytest.raises(KeyError):
        values.replace(('missing', 0))


def test_get_uses_stored_values_and_preserves_defaults() -> None:
    class NoLookup(KeyedDict[str, None]):
        def __getitem__(self, key: str, /) -> None:
            raise AssertionError('Get must query storage directly.')

    values = NoLookup([None], key=lambda _: 'present')
    marker = object()
    assert values.get('present', marker) is None
    assert values.get('missing') is None
    assert values.get('missing', marker) is marker
    assert values.get('missing', default=42) == 42


def test_equality_supports_mappings_and_unhashable_values_without_projecting_keys() -> None:
    def projection(value: list[str]) -> str:
        return value[0]

    left = KeyedDict([['a', 'one'], ['b', 'two']], key=projection)
    right = KeyedDict([['b', 'two'], ['a', 'one']], key=itemgetter(0))
    expected = {'a': ['a', 'one'], 'b': ['b', 'two']}
    for other in (right, expected, UserDict(expected)):
        assert left == other and other == left
        assert (left != other) is False
    assert left != {'a': ['a', 'one']}
    assert left != {'a': ['a', 'changed'], 'b': ['b', 'two']}
    assert left != {'a': ['a', 'one'], 'c': ['b', 'two']}
    assert left != list(expected.items())
    assert left.__eq__(object()) is NotImplemented
    with pytest.raises(TypeError, match='unhashable'):
        hash(left)


def test_equality_preserves_reflected_comparison() -> None:
    class Reflected:
        def __eq__(self, other: object, /) -> bool:
            return isinstance(other, KeyedDict)

    values: KeyedDict[str, _Item] = KeyedDict(key=itemgetter(0))
    assert values == Reflected()
    assert (values != Reflected()) is False


def test_adddefault_returns_existing_value_or_inserts_and_returns_new_value() -> None:
    original = ('a', 1)
    incoming = ('a', 2)
    new = ('b', 3)
    values: KeyedDict[str, _Item] = KeyedDict([original], key=itemgetter(0))
    assert values.adddefault(incoming) is original
    assert values['a'] is original
    assert values.adddefault(new) is new
    assert values['b'] is new
    assert list(values) == ['a', 'b']
    values.add(incoming)
    assert values['a'] is incoming and list(values) == ['a', 'b']


def test_views_follow_deletion_pop_and_reinsertion() -> None:
    values: KeyedDict[str, _Item] = KeyedDict([('a', 1), ('b', 2)], key=itemgetter(0))
    keys, items, members = values.keys(), values.items(), values.values()
    del values['a']
    assert list(keys) == ['b']
    assert values.pop('missing', 42) == 42
    assert values.pop('missing', None) is None
    with pytest.raises(KeyError):
        values.pop('missing')
    values.add(('a', 3))
    assert values.popitem() == ('a', ('a', 3))
    assert values.pop('b') == ('b', 2)
    assert not keys and not items and not members
    with pytest.raises(KeyError):
        values.popitem()
    with pytest.raises(KeyError):
        del values['missing']
    values.update([('c', 4)])
    assert list(members) == [('c', 4)]
    values.clear()
    assert not members


def test_views_are_native_dict_views_and_follow_replacement() -> None:
    values: KeyedDict[str, _Item] = KeyedDict([('a', 1), ('b', 2)], key=itemgetter(0))
    keys, items, members = values.keys(), values.items(), values.values()
    assert type(keys) is type({}.keys())
    assert type(items) is type({}.items())
    assert type(members) is type({}.values())
    values.add(('a', 3))
    assert list(keys) == ['a', 'b']
    assert list(items) == [('a', ('a', 3)), ('b', ('b', 2))]
    assert list(members) == [('a', 3), ('b', 2)]


def test_reverse_iteration_follows_key_insertion_order() -> None:
    values: KeyedDict[str, _Item] = KeyedDict(key=itemgetter(0))
    assert list(reversed(values)) == []
    values.update([('a', 1), ('b', 2), ('c', 3)])
    assert list(reversed(values)) == ['c', 'b', 'a']
    values.add(('b', 4))
    assert list(reversed(values)) == ['c', 'b', 'a']
    del values['a']
    values.add(('a', 5))
    assert list(reversed(values)) == ['a', 'c', 'b']


def test_self_update_skips_subclass_insertion_without_skipping_later_inputs() -> None:
    class UniqueValues(KeyedDict[str, _Item]):
        def add(self, value: _Item, /) -> None:
            if value[0] in self:
                raise ValueError('Existing key')
            super().add(value)

    values = UniqueValues([('a', 1)], key=itemgetter(0))
    values.update(values)
    values.update(values, [('b', 2)], values, [('c', 3)])
    assert list(values.values()) == [('a', 1), ('b', 2), ('c', 3)]


def test_key_is_instance_configuration_and_read_only() -> None:
    def projection(value: _Item) -> str:
        return value[0]

    values = KeyedDict([('a', 1)], key=projection)
    assert values.key is projection and values['a'] == ('a', 1)
    with pytest.raises(AttributeError):
        values.key = itemgetter(1)  # pyright: ignore[reportAttributeAccessIssue]


def test_same_rule_update_reuses_keys_for_empty_and_populated_targets() -> None:
    seen: list[_Item] = []

    def projection(value: _Item) -> str:
        seen.append(value)
        return value[0]

    other = KeyedDict([('a', 2), ('b', 3)], key=projection)
    empty: KeyedDict[str, _Item] = KeyedDict(key=projection)
    for values in (empty, KeyedDict([('a', 1)], key=projection)):
        seen.clear()
        members = values.values()
        values.update(other)
        assert not seen
        assert list(members) == [('a', 2), ('b', 3)]
        values.update(values)
        assert not seen and list(values) == ['a', 'b']


def test_updates_reproject_different_rules_and_ignore_inheritance() -> None:
    class Other(KeyedDict[str, _Item]):
        pass

    values: KeyedDict[str, _Item] = KeyedDict([('a', 1)], key=itemgetter(0))
    other = Other([('a', 9), ('c', 4)], key=lambda value: str(value[1]))
    values.update(other)
    assert list(values.values()) == [('a', 9), ('c', 4)]
    cloned = KeyedDict(other, key=values.key)
    assert list(cloned) == ['a', 'c']
    values.update(KeyedDict(key=values.key), [('d', 5)])
    values.update()
    assert list(values) == ['a', 'c', 'd']


def test_partial_updates_retain_completed_values_on_projection_failure() -> None:
    def projection(value: _Item) -> str:
        if value[0] == 'invalid':
            raise ValueError('Invalid value')
        return value[0]

    values: KeyedDict[str, _Item] = KeyedDict(key=projection)
    with pytest.raises(ValueError, match='Invalid value'):
        values.update([('a', 1)], [('invalid', 2), ('b', 3)])
    assert list(values.values()) == [('a', 1)]


def test_constructor_reprojects_a_map_with_a_different_key_type() -> None:
    source: KeyedDict[int, _Item] = KeyedDict([('a', 1), ('b', 2), ('a', 3)], key=itemgetter(1))
    target: KeyedDict[str, _Item] = KeyedDict(source, key=itemgetter(0))
    assert list(target.items()) == [('a', ('a', 3)), ('b', ('b', 2))]
    assert list(source) == [1, 2, 3]


def test_update_accepts_mixed_source_key_types_and_reprojects_collisions() -> None:
    numeric: KeyedDict[int, _Item] = KeyedDict([('a', 1), ('a', 2)], key=itemgetter(1))
    composite: KeyedDict[_Item, _Item] = KeyedDict([('a', 3), ('b', 4)], key=lambda value: value)
    target: KeyedDict[str, _Item] = KeyedDict(key=itemgetter(0))
    target.update(numeric, composite, [('b', 5)])
    assert list(target.items()) == [('a', ('a', 3)), ('b', ('b', 5))]
    assert list(numeric) == [1, 2]
    assert list(composite) == [('a', 3), ('b', 4)]


def test_copy_owns_membership_but_shares_values_and_deepcopy_isolates_values() -> None:
    member = ['a', 'payload']
    values: KeyedDict[str, list[str]] = KeyedDict([member], key=itemgetter(0))
    for branch in (values.copy(), copy(values)):
        assert type(branch) is KeyedDict and branch.key is values.key
        assert branch['a'] is member
        del branch['a']
        assert list(values.values()) == [member]
    cloned = deepcopy(values)
    cloned['a'].append('changed')
    assert member == ['a', 'payload']


def test_none_is_a_real_value_for_adddefault_and_pop() -> None:
    values: KeyedDict[str, None] = KeyedDict(key=lambda _: 'key')
    assert values.adddefault(None) is None
    assert values.adddefault(None) is None
    assert values['key'] is None and len(values) == 1
    assert values.pop('key') is None


def test_repr_shows_values_in_order_projection_and_actual_class() -> None:
    class NamedMap(KeyedDict[str, _Item]):
        pass

    projection = itemgetter(0)
    values = NamedMap([('a', 1), ('b', 2)], key=projection)
    assert repr(values) == f"NamedMap([('a', 1), ('b', 2)], key={projection!r})"
    values.clear()
    assert repr(values) == f'NamedMap([], key={projection!r})'


def test_repr_handles_recursive_values() -> None:
    values: KeyedDict[str, list[object]] = KeyedDict(key=lambda value: str(value[0]))
    values.add(['a', values])
    assert repr(values).startswith("KeyedDict([['a', ...]], key=")
