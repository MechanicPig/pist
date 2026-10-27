from collections.abc import (
    Callable,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    MutableMapping,
    ValuesView,
)
from collections.abc import Set as AbstractSet
from copy import copy
from itertools import repeat
from reprlib import recursive_repr
from typing import TYPE_CHECKING, Self, cast, overload

from typing_extensions import Sentinel as sentinel

if TYPE_CHECKING:
    from _typeshed import SupportsKeysAndGetItem
else:
    from collections.abc import Mapping as SupportsKeysAndGetItem

_MISSING = sentinel('_MISSING')

type _Item[T] = tuple[str, T]
type _UpdateSource[T] = SupportsKeysAndGetItem[str, T] | Iterable[_Item[T]]


class KeyedDict[K, T](Mapping[K, T]):
    """Ordered mapping whose keys are derived from values by a fixed instance rule.

    Adding overwrites an existing value without changing its position.
    Values must retain the same derived key while stored.
    """

    def __init__[U](
        self, values: Iterable[T] | KeyedDict[U, T] = (), *, key: Callable[[T], K]
    ) -> None:
        self._key = key
        self._data: dict[K, T] = {}
        self.update(values)

    @property
    def key(self) -> Callable[[T], K]:
        return self._key

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object, /) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __reversed__(self) -> Iterator[K]:
        return reversed(self._data)

    def keys(self) -> KeysView[K]:
        return self._data.keys()

    def values(self) -> ValuesView[T]:
        return self._data.values()

    def items(self) -> ItemsView[K, T]:
        return self._data.items()

    def __getitem__(self, key: K, /) -> T:
        return self._data[key]

    @overload
    def get(self, key: K, /) -> T | None: ...
    @overload
    def get(self, key: K, /, default: T) -> T: ...
    @overload
    def get[D](self, key: K, /, default: D) -> T | D: ...
    def get[D](self, key: K, /, default: D | None = None) -> T | D | None:
        return self._data.get(key, default)

    def __eq__(self, other: object, /) -> bool:
        if isinstance(other, KeyedDict):
            # Compare native storage without allocating temporary mappings.
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        if isinstance(other, Mapping):
            return self._data == dict(other.items())
        return NotImplemented

    def __delitem__(self, key: K, /) -> None:
        del self._data[key]

    def add(self, value: T, /) -> None:
        self._data[self._key(value)] = value

    def adddefault(self, value: T, /) -> T:
        return self._data.setdefault(self._key(value), value)

    def update[U](self, *others: Iterable[T] | KeyedDict[U, T]) -> None:
        for other in others:
            if other is self:
                continue
            if isinstance(other, KeyedDict):
                # Mapping also satisfies Iterable (of keys); treat maps as value inputs.
                mapped = cast(KeyedDict[U, T], other)
                if not mapped:
                    continue
                # Bulk copying must not bypass a subclass's public insertion constraints.
                if mapped.key is self._key and type(self).add is KeyedDict.add:
                    # Same-class storage access preserves dict.update's dictionary fast path.
                    # The identical projection guarantees the stored keys have target type K.
                    compatible = cast(KeyedDict[K, T], mapped)
                    self._data.update(compatible._data)  # noqa: SLF001
                    continue
                values = mapped.values()
            else:
                values = other
            for value in values:
                self.add(value)

    def replace(self, value: T, /) -> None:
        key = self._key(value)
        if key not in self._data:
            raise KeyError(key)
        self._data[key] = value

    @overload
    def pop(self, key: K, /) -> T: ...
    @overload
    def pop[D](self, key: K, /, default: D) -> T | D: ...
    def pop[D](self, key: K, /, default: D | _MISSING = _MISSING) -> T | D:
        if default is _MISSING:
            return self._data.pop(key)
        return self._data.pop(key, default)

    def popitem(self) -> tuple[K, T]:
        return self._data.popitem()

    def clear(self) -> None:
        self._data.clear()

    def copy(self) -> Self:
        return copy(self)

    def __copy__(self) -> Self:
        return type(self)(self, key=self._key)

    @recursive_repr()
    def __repr__(self) -> str:
        return f'{type(self).__name__}({list(self._data.values())!r}, key={self._key!r})'


class FrozenCaseFoldSet(AbstractSet[str]):
    """An immutable string set with Unicode case-folded membership checks."""

    def __init__(self, values: Iterable[str] = (), /) -> None:
        self._values = frozenset(self._fold(value) for value in values)

    @staticmethod
    def _fold(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f'{type(value).__name__!r} object is not case-foldable')
        return value.casefold()

    def __contains__(self, value: object, /) -> bool:
        return isinstance(value, str) and value.casefold() in self._values

    def __iter__(self, /) -> Iterator[str]:
        return iter(self._values)

    def __len__(self, /) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f'{type(self).__name__}{repr(self._values).removeprefix("frozenset")}'


class CaseFoldDict[VT](MutableMapping[str, VT]):
    """A mapping whose keys support Unicode case-folded lookups."""

    def __init__(self, other: _UpdateSource[VT] = (), /, **kwargs: VT) -> None:
        self._data: dict[str, VT] = {}
        self.update(other, **kwargs)

    def __contains__(self, key: object, /) -> bool:
        try:
            key = key.casefold()  # type: ignore[attr-defined]
        except AttributeError:
            return False
        return key in self._data

    def __getitem__(self, key: str, /) -> VT:
        try:
            key = key.casefold()
        except AttributeError:
            raise KeyError(key) from None
        return self._data[key]

    def __setitem__(self, key: str, value: VT, /) -> None:
        try:
            key = key.casefold()
        except AttributeError:
            raise TypeError(f'{type(key).__name__!r} object is not case-foldable') from None
        self._data[key] = value

    def __delitem__(self, key: str, /) -> None:
        try:
            key = key.casefold()
        except AttributeError:
            raise KeyError(key) from None
        del self._data[key]

    @overload
    def get(self, key: str, /) -> VT | None: ...

    @overload
    def get(self, key: str, /, default: VT) -> VT: ...

    @overload
    def get[T](self, key: str, /, default: T) -> VT | T: ...

    def get(self, key: str, /, default: object = None) -> object:
        try:
            key = key.casefold()
        except AttributeError:
            return default
        return self._data.get(key, default)

    def update(self, other: _UpdateSource[VT] = (), /, **kwargs: VT) -> None:
        if isinstance(other, CaseFoldDict):
            self._data.update(other._data)  # noqa: SLF001
        else:
            super().update(other)
        for key, value in kwargs.items():
            self[key] = value

    @overload
    def pop(self, key: str, /) -> VT: ...

    @overload
    def pop(self, key: str, /, default: VT) -> VT: ...

    @overload
    def pop[T](self, key: str, /, default: T) -> VT | T: ...

    def pop(self, key: str, /, default: object = _MISSING) -> object:
        try:
            key = key.casefold()
        except AttributeError:
            if default is _MISSING:
                raise KeyError(key) from None
            return default
        if default is _MISSING:
            return self._data.pop(key)
        return self._data.pop(key, default)

    def clear(self, /) -> None:
        self._data.clear()

    def copy(self, /) -> Self:
        return type(self)(self)

    @classmethod
    def fromkeys(cls, iterable: Iterable[str], value: VT = None, /) -> Self:
        return cls(zip(iterable, repeat(value)))

    def __len__(self, /) -> int:
        return len(self._data)

    def __iter__(self, /) -> Iterator[str]:
        return iter(self._data)

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self._data!r})'
