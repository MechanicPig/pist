"""Shared save scalar conversions and storage-range validation."""

from typing import Annotated

from annotated_types import Ge, Le
from pydantic import BeforeValidator, TypeAdapter

from berries.game.duration import TIMER_FRAME, Duration
from berries.game.saves.errors import BadProgressSave
from berries.types import DecimalInt

from . import single

MIN_SAVE_TICKS = -(2**63)
MAX_SAVE_TICKS = 2**63 - 1
type SaveCount = Annotated[DecimalInt, Ge(0), Le(2**31 - 1)]
type SaveInt = Annotated[DecimalInt, Ge(-(2**31)), Le(2**31 - 1)]
type SaveTicks = Annotated[DecimalInt, Ge(MIN_SAVE_TICKS), Le(MAX_SAVE_TICKS)]

_COUNT_ADAPTER = TypeAdapter(SaveCount)
_TICKS_ADAPTER = TypeAdapter(SaveTicks)


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip(' \t\r\n')
        if value in ('true', '1'):
            return True
        if value in ('false', '0'):
            return False
    raise ValueError('Expected an XML boolean.')


type SaveBool = Annotated[bool, BeforeValidator(parse_bool)]


def parse_ticks(value: object) -> int:
    """Parse decimal .NET ticks within the signed Int64 storage range."""
    return _TICKS_ADAPTER.validate_python(value)


def parse_duration(value: object) -> Duration:
    """Decode protocol ticks; internal Duration values retain their exact precision."""
    if isinstance(value, Duration):
        return value
    return Duration.from_dotnet_ticks(parse_ticks(value))


type SaveDuration = Annotated[Duration, BeforeValidator(parse_duration)]
"""Duration with shared save-input conversion; output conversion remains explicit."""

type SaveSingle = Annotated[float, BeforeValidator(single.parse)]


def format_duration(value: Duration) -> str:
    """Encode a duration as signed Int64 .NET ticks without losing precision."""
    return str(parse_ticks(value.to_dotnet_ticks()))


def validate_duration(value: Duration) -> None:
    """Preflight an affected output without formatting or replacing its Duration."""
    ticks = value.to_dotnet_ticks()
    if not MIN_SAVE_TICKS <= ticks <= MAX_SAVE_TICKS:
        raise ValueError('Duration exceeds the signed Int64 save-tick range.')


def validate_timer_duration(value: Duration, *, path: str) -> None:
    """Require a whole 17 ms timer increment when reading normal game output."""
    if (value.total_seconds / TIMER_FRAME).denominator != 1:
        raise BadProgressSave(f'{path} must be an integer multiple of 17 ms.')


def parse_count(value: object) -> int:
    """Parse a decimal count and require the nonnegative Int32 range."""
    return _COUNT_ADAPTER.validate_python(value)


def format_count(value: int) -> str:
    """Validate and encode a nonnegative Int32 count as decimal text."""
    return str(parse_count(value))
