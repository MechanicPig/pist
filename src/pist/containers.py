from collections.abc import Iterable, Iterator, MutableMapping
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


class CaseFoldDict[VT](MutableMapping[str, VT]):
    """A string-keyed mapping with Unicode case-folded lookups."""

    def __init__(self, other: _UpdateSource[VT] = (), /, **kwargs: VT) -> None:
        self._dict: dict[str, VT] = {}
        self.update(other, **kwargs)

    @staticmethod
    def _fold(key: str) -> str:
        if not isinstance(key, str):
            raise TypeError(f'{type(key).__name__!r} object is not case-foldable')
        return key.casefold()

    def __contains__(self, key: object, /) -> bool:
        return isinstance(key, str) and key.casefold() in self._dict

    def __getitem__(self, key: str, /) -> VT:
        return self._dict[self._fold(key)]

    def __setitem__(self, key: str, value: VT, /) -> None:
        self._dict[self._fold(key)] = value

    def __delitem__(self, key: str, /) -> None:
        del self._dict[self._fold(key)]

    @overload
    def get(self, key: str, /) -> VT | None: ...

    @overload
    def get(self, key: str, /, default: VT) -> VT: ...

    @overload
    def get[T](self, key: str, /, default: T) -> VT | T: ...

    def get(self, key: str, /, default: object = None) -> object:
        return self._dict.get(self._fold(key), default)

    def update(self, other: _UpdateSource[VT] = (), /, **kwargs: VT) -> None:
        for key, value in dict(other).items():
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    @overload
    def pop(self, key: str, /) -> VT: ...

    @overload
    def pop(self, key: str, /, default: VT) -> VT: ...

    @overload
    def pop[T](self, key: str, /, default: T) -> VT | T: ...

    def pop(self, key: str, /, default: object = _MISSING) -> object:
        key = self._fold(key)
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
