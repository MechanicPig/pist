"""Type-preserving identity for entity attribute and metadata conditions."""

from collections.abc import Iterable

from berries.game.binmap import AttrValue

type TypedAttrsKey = tuple[tuple[str, type[AttrValue | None], AttrValue | None], ...]


def typed_attrs_key(attrs: Iterable[tuple[str, AttrValue | None]]) -> TypedAttrsKey:
    """Distinguish equal-valued scalars of different types without losing missing values."""
    return tuple((name, type(value), value) for name, value in sorted(attrs))
