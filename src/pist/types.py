"""Small project-wide value types with no owning domain."""

import re
from typing import Annotated

from annotated_types import Ge, MinLen
from pydantic import BeforeValidator, StrictInt, StringConstraints


def parse_decimal_int(value: object) -> int:
    """Normalize one native integer or ASCII decimal representation."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        return int(value)
    raise ValueError('Expected a non-negative decimal integer.')


type CellValue = int | bool | str
type RecordValues = dict[str, dict[str, CellValue]]
type NonEmptyStr = Annotated[str, MinLen(1)]
type StrippedNonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
type NonNegativeDecimalInt = Annotated[
    StrictInt,
    BeforeValidator(parse_decimal_int),
    Ge(0),
]
