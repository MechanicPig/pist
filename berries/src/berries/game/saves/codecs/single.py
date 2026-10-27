"""XML Single lexical rules with IEEE binary32 rounding delegated to MPFR."""

import math
import re
from decimal import Decimal

import gmpy2

_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


def parse(value: object) -> float:
    """Decode XmlConvert-compatible numbers without a binary64 intermediate.

    A fresh IEEE context isolates rounding, exponent limits and traps from the
    caller's gmpy2 context. Returning float is exact for a binary32 result.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('Expected a Single number.')  # noqa: TRY004 - Pydantic input validation
    if isinstance(value, Decimal):
        value = str(value)
    if isinstance(value, str):
        value = value.strip(' \t\r\n')
        if value in ('INF', '-INF'):
            return -math.inf if value.startswith('-') else math.inf
        special = value[1:] if value.startswith(('+', '-')) else value
        if special.lower() == 'nan':
            return math.nan
        if special.lower() == 'infinity':
            return -math.inf if value.startswith('-') else math.inf
        if _NUMBER.fullmatch(value) is None:
            raise ValueError('Expected a Single number.')
    try:
        with gmpy2.ieee(32):
            return float(gmpy2.mpfr(value))
    except (ValueError, OverflowError) as error:
        raise ValueError('Expected a Single number.') from error


def format_value(value: float) -> str:
    """Encode enough digits to round-trip binary32, not .NET's typography."""
    number = parse(value)
    if math.isnan(number):
        return 'NaN'
    if math.isinf(number):
        return '-INF' if number < 0 else 'INF'
    return format(number, '.9g')
