"""Small project-wide value types with no owning domain."""

import re
from typing import Annotated

from annotated_types import MinLen
from pydantic import BeforeValidator, StrictInt


def parse_decimal_int(value: object) -> int:
    """Normalize one native integer or ASCII decimal representation with an optional minus."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
        return int(value)
    raise ValueError('Expected a decimal integer.')


type NonEmptyStr = Annotated[str, MinLen(1)]
type DecimalInt = Annotated[StrictInt, BeforeValidator(parse_decimal_int)]
