"""Single conversion checked against XmlConvert on .NET 10.0.400."""

import math
import struct
from decimal import Decimal

import gmpy2
import pytest

from berries.game.saves.codecs import single


@pytest.mark.parametrize(
    ('text', 'bits'),
    [
        ('0', 0x00000000),
        ('-0', 0x80000000),
        ('0.1', 0x3DCCCCCD),
        ('1.000000059604644775390625', 0x3F800000),
        ('1.000000059604644775390625000001', 0x3F800001),
        ('1e-45', 0x00000001),
        ('1e-46', 0x00000000),
        ('3.4028234663852886e38', 0x7F7FFFFF),
        ('3.4028236e38', 0x7F800000),
        ('1e50', 0x7F800000),
        ('INF', 0x7F800000),
        ('-INF', 0xFF800000),
    ],
)
def test_single_matches_dotnet_bits_and_roundtrips(text: str, bits: int) -> None:
    value = single.parse(text)
    assert struct.pack('<f', value) == struct.pack('<I', bits)
    restored = single.parse(single.format_value(value))
    assert struct.pack('<f', restored) == struct.pack('<f', value)


def test_nan_and_python_numbers() -> None:
    assert math.isnan(single.parse('NaN'))
    assert single.format_value(math.nan) == 'NaN'
    assert single.parse(16777217) == 16777216.0
    assert single.parse(0.1) == single.parse('0.1')
    assert single.parse('-1.000000059604644775390625000001') == -single.parse(
        '1.000000059604644775390625000001'
    )


@pytest.mark.parametrize(
    'value',
    [
        True,
        None,
        '',
        'not a float',
        [],
        {},
        '1_0',
        '١',
        '0x1p2',
        '1.2.3',
        'nan(123)',
        '+INF',
        'inf',
        '-inf',
        '\u00a01\u00a0',
    ],
)
def test_invalid_single(value: object) -> None:
    with pytest.raises(ValueError):
        single.parse(value)


@pytest.mark.parametrize('text', ['nan', 'NAN', '+nan', '-nan'])
def test_dotnet_nan_spellings(text: str) -> None:
    assert math.isnan(single.parse(text))


@pytest.mark.parametrize('text', ['Infinity', 'infinity', '+Infinity', ' 1 ', '+1'])
def test_dotnet_noncanonical_spellings(text: str) -> None:
    expected = math.inf if 'infinity' in text.lower() else 1.0
    assert single.parse(text) == expected


def test_decimal_values_and_caller_context_are_preserved() -> None:
    with gmpy2.context(round=gmpy2.RoundUp, trap_inexact=True, precision=9):
        value = single.parse(Decimal('1.000000059604644775390625000001'))
        assert struct.pack('<f', value) == struct.pack('<I', 0x3F800001)
        assert gmpy2.get_context().round == gmpy2.RoundUp
        assert gmpy2.get_context().trap_inexact and gmpy2.get_context().precision == 9
