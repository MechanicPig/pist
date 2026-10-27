"""Shared primitive parsing without domain-specific storage bounds."""

import pytest
from pydantic import TypeAdapter, ValidationError

from berries.types import DecimalInt


@pytest.mark.parametrize('value', (0, 1, -1, 2**100, -(2**100)))
def test_decimal_int_accepts_native_and_decimal_text(value: int) -> None:
    adapter = TypeAdapter(DecimalInt)
    assert adapter.validate_python(value) == adapter.validate_python(str(value)) == value
    assert adapter.validate_python('01') == 1


@pytest.mark.parametrize('value', (True, False, None, 1.0, '1.0', '+1', ' 1 ', '1_000'))
def test_decimal_int_rejects_other_input(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DecimalInt).validate_python(value)
