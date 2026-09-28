from collections.abc import Iterable, Iterator, MutableMapping
from collections.abc import Set as AbstractSet
from itertools import repeat
from typing import TYPE_CHECKING, Self, overload

from typing_extensions import Sentinel as sentinel

if TYPE_CHECKING:
    from _typeshed import SupportsKeysAndGetItem
else:
    from collections.abc import Mapping as SupportsKeysAndGetItem

_MISSING = sentinel('_MISSING')

type _Item[T] = tuple[str, T]
type _UpdateSource[T] = SupportsKeysAndGetItem[str, T] | Iterable[_Item[T]]


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
        self._dict: dict[str, VT] = {}
        self.update(other, **kwargs)

    def __contains__(self, key: object, /) -> bool:
        try:
            key = key.casefold()  # type: ignore[attr-defined]
        except AttributeError:
            return False
        return key in self._dict

    def __getitem__(self, key: str, /) -> VT:
        try:
            key = key.casefold()
        except AttributeError:
            raise KeyError(key) from None
        return self._dict[key]

    def __setitem__(self, key: str, value: VT, /) -> None:
        try:
            key = key.casefold()
        except AttributeError:
            raise TypeError(f'{type(key).__name__!r} object is not case-foldable') from None
        self._dict[key] = value

    def __delitem__(self, key: str, /) -> None:
        try:
            key = key.casefold()
        except AttributeError:
            raise KeyError(key) from None
        del self._dict[key]

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
        return self._dict.get(key, default)

    def update(self, other: _UpdateSource[VT] = (), /, **kwargs: VT) -> None:
        if isinstance(other, CaseFoldDict):
            self._dict.update(other._dict)
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
            return self._dict.pop(key)
        return self._dict.pop(key, default)

    def clear(self, /) -> None:
        self._dict.clear()

    def copy(self, /) -> Self:
        return type(self)(self)

    @classmethod
    def fromkeys(cls, iterable: Iterable[str], value: VT = None, /) -> Self:
        return cls(zip(iterable, repeat(value)))

    def __len__(self, /) -> int:
        return len(self._dict)

    def __iter__(self, /) -> Iterator[str]:
        return iter(self._dict)

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self._dict!r})'
