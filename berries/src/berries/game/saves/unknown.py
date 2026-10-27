"""Explicit handling of fields outside a supported save schema."""

import warnings
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from typing import Literal

type UnknownFieldPolicy = Literal['error', 'warn']


class UnknownSaveFieldError(ValueError):
    """A save contains a field outside the supported schema."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f'Unknown save field {path!r}.')


class UnknownSaveFieldWarning(UserWarning):
    """An unknown field was ignored and will not be included in output."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f'Unknown save field {path!r}; it will be omitted from output.')


def check_fields(
    fields: Iterable[str],
    known: AbstractSet[str],
    *,
    path: str,
    unknown: UnknownFieldPolicy,
) -> None:
    """Report unknown names without inspecting their unrecognized payloads."""
    if unknown not in ('error', 'warn'):
        raise ValueError(f'Invalid unknown-field policy: {unknown!r}.')
    for name in fields:
        if name in known:
            continue
        field_path = f'{path}.{name}'
        if unknown == 'error':
            raise UnknownSaveFieldError(field_path)
        warnings.warn(UnknownSaveFieldWarning(field_path), stacklevel=3)
