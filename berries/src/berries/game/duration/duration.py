"""Exact signed time intervals, explicit precision alignment and display components."""

import builtins
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction
from numbers import Rational
from typing import Protocol, Self, cast, overload

from ._format import format_duration
from ._parse import parse_duration

DOTNET_TICK = Fraction(1, 10_000_000)
TIMER_FRAME = Fraction(17, 1_000)
MICROSECOND = Fraction(1, 1_000_000)

type _Scalar = int | float | Fraction

GAME_TIMER_FORMAT = '[%Qh:%M|%-M]:%S.%3f'
GAME_TIMER_SECONDS_FORMAT = '[%Qh:]%M:%S'


class _SupportsRatio(Protocol):
    def as_integer_ratio(self) -> tuple[int, int]: ...


@dataclass(frozen=True, slots=True, order=True)
class Duration:
    """An exact signed interval in seconds, independent of its source precision.

    Addition, subtraction and comparison accept Duration only; scaling accepts numbers.
    Division by another Duration returns an exact ratio; timedelta conversion is explicit.
    Conversions reject precision loss; alignment is always an explicit operation.
    """

    total_seconds: Fraction = Fraction()

    def __post_init__(self) -> None:
        """Require the exact internal representation, without implicit conversion."""
        if not isinstance(self.total_seconds, Fraction):
            raise TypeError('total_seconds must be a Fraction; use from_seconds() for conversion.')

    @classmethod
    @overload
    def from_seconds(
        cls, numerator: int | Rational | _SupportsRatio | str, denominator: None = None
    ) -> Self: ...

    @classmethod
    @overload
    def from_seconds(cls, numerator: int | Rational, denominator: int | Rational) -> Self: ...

    @classmethod
    def from_seconds(
        cls,
        numerator: int | Rational | _SupportsRatio | str,
        denominator: int | Rational | None = None,
    ) -> Self:
        """Construct seconds using Fraction's conversion and ratio semantics."""
        # The two-argument overload guarantees a rational numerator; Fraction validates at runtime.
        seconds = (
            Fraction(numerator)
            if denominator is None
            else Fraction(cast(int | Rational, numerator), denominator)
        )
        return cls(seconds)

    @classmethod
    def from_units(
        cls,
        *,
        hours: _Scalar = 0,
        minutes: _Scalar = 0,
        seconds: _Scalar = 0,
        subseconds: _Scalar = 0,
        milliseconds: _Scalar = 0,
        microseconds: _Scalar = 0,
        nanoseconds: _Scalar = 0,
    ) -> Self:
        """Sum signed unit quantities exactly, without component ranges or alignment.

        All units are additive, including overlapping subsecond inputs. Floats
        retain their exact binary value, just as in from_seconds().
        """
        return cls(
            Fraction(hours) * 3_600
            + Fraction(minutes) * 60
            + Fraction(seconds)
            + Fraction(subseconds)
            + Fraction(milliseconds) / 1_000
            + Fraction(microseconds) / 1_000_000
            + Fraction(nanoseconds) / 1_000_000_000
        )

    @classmethod
    def from_format(cls, text: str, spec: str) -> Self:
        """Parse one unambiguous canonical representation using the format grammar.

        Missing components are zero; %H supplies only hours within a day.
        Fractions retain the precision present in the text, not the original
        interval's discarded digits. Optional branches, escapes and redundant
        fields must agree with formatting the result. Ambiguous, contradictory
        or nonmatching inputs raise ValueError. An empty spec uses GAME_TIMER_FORMAT.
        """
        return cls(parse_duration(text, spec or GAME_TIMER_FORMAT))

    @property
    def hours(self) -> int:
        """Return the absolute hour component without wrapping at 24 hours."""
        return abs(self.total_seconds) // 3_600

    @property
    def minutes(self) -> int:
        """Return the absolute minute component, from 0 to 59."""
        return (abs(self.total_seconds) // 60) % 60

    @property
    def seconds(self) -> int:
        """Return the absolute second component, from 0 to 59."""
        return (abs(self.total_seconds) // 1) % 60

    @property
    def subseconds(self) -> Fraction:
        """Return the exact absolute fractional second component, below one."""
        return abs(self.total_seconds) % 1

    @property
    def milliseconds(self) -> int:
        """Return the absolute millisecond component, excluding finer precision."""
        return (abs(self.total_seconds) * 1_000 // 1) % 1_000

    @classmethod
    def from_dotnet_ticks(cls, ticks: int) -> Self:
        """Create an interval from .NET TimeSpan ticks of 100 ns, without an epoch."""
        return cls(ticks * DOTNET_TICK)

    @classmethod
    def from_timer_frames(cls, frames: int) -> Self:
        """Create an interval from nominal 17 ms game timing increments."""
        return cls(frames * TIMER_FRAME)

    @classmethod
    def from_timedelta(cls, value: timedelta) -> Self:
        """Preserve every microsecond of a signed timedelta."""
        microseconds = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
        return cls(microseconds * MICROSECOND)

    def to_dotnet_ticks(self) -> int:
        """Return exact .NET TimeSpan ticks of 100 ns, rejecting a fractional tick."""
        return _exact_count(self.total_seconds, DOTNET_TICK)

    def to_timer_frames(self) -> int:
        """Return exact 17 ms increments, rejecting a fractional frame."""
        return _exact_count(self.total_seconds, TIMER_FRAME)

    def to_timedelta(self) -> timedelta:
        """Convert exactly, rejecting submicroseconds or timedelta range overflow."""
        return timedelta(microseconds=_exact_count(self.total_seconds, MICROSECOND))

    def _alignment_ratio(self, quantum: Fraction) -> Fraction:
        if quantum <= 0:
            raise ValueError('Alignment quantum must be positive.')
        return self.total_seconds / quantum

    def is_aligned(self, quantum: Fraction) -> bool:
        """Return whether the interval is an exact multiple of a positive quantum."""
        return self._alignment_ratio(quantum).denominator == 1

    def floor(self, quantum: Fraction) -> Duration:
        """Return the greatest quantum multiple not greater than the interval."""
        return Duration((self._alignment_ratio(quantum) // 1) * quantum)

    def ceil(self, quantum: Fraction) -> Duration:
        """Return the least quantum multiple not less than the interval."""
        return Duration(-(-self._alignment_ratio(quantum) // 1) * quantum)

    def trunc(self, quantum: Fraction) -> Duration:
        """Return the quantum multiple obtained by truncating toward zero."""
        return Duration(int(self._alignment_ratio(quantum)) * quantum)

    def round(self, quantum: Fraction) -> Duration:
        """Return the nearest quantum multiple, choosing an even multiple on ties."""
        return Duration(builtins.round(self._alignment_ratio(quantum)) * quantum)

    def __str__(self) -> str:
        """Display the game timer's millisecond format without changing precision."""
        return format_duration(self, GAME_TIMER_FORMAT)

    def __format__(self, spec: str) -> str:
        """Format exact components without modifying or aligning the interval.

        %H/%M/%S are two-digit components (hours wrap at 24); %-H/%-M/%-S
        omit padding. %Qh/%Qm/%Qs are unbounded absolute whole-unit counts.
        %f emits six fractional digits; %3f and other positive widths truncate
        at that precision, including for repeating fractions. %% emits a percent.

        [primary|alternative] selects primary when its first numeric directive
        would output a nonzero value, otherwise alternative (empty if omitted).
        Groups cannot nest; both branches must have valid syntax. Backslash
        escapes backslash, brackets and pipe. Other characters are literal.
        A negative sign precedes the first selected numeric directive only.
        An empty specification uses GAME_TIMER_FORMAT, as does str().
        """
        return format_duration(self, spec) if spec else str(self)

    def __add__(self, other: Duration) -> Duration:
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration(self.total_seconds + other.total_seconds)

    def __sub__(self, other: Duration) -> Duration:
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration(self.total_seconds - other.total_seconds)

    def __mul__(self, other: _Scalar) -> Duration:
        """Scale by a number, preserving even a float's exact binary value."""
        if not isinstance(other, (int, float, Fraction)):
            return NotImplemented
        return Duration(self.total_seconds * Fraction(other))

    def __rmul__(self, other: _Scalar) -> Duration:
        return self.__mul__(other)

    @overload
    def __truediv__(self, other: Duration) -> Fraction: ...

    @overload
    def __truediv__(self, other: _Scalar) -> Duration: ...

    def __truediv__(self, other: Duration | _Scalar) -> Duration | Fraction:
        """Return a duration when scaling, or an exact ratio between durations."""
        if isinstance(other, Duration):
            return self.total_seconds / other.total_seconds
        if isinstance(other, (int, float, Fraction)):
            return Duration(self.total_seconds / Fraction(other))
        return NotImplemented

    def __floordiv__(self, other: Duration) -> int:
        """Return the number of whole intervals, rounding toward negative infinity."""
        if not isinstance(other, Duration):
            return NotImplemented
        return self.total_seconds // other.total_seconds

    def __mod__(self, other: Duration) -> Duration:
        """Return the remainder with the divisor's sign, as for Python numbers."""
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration(self.total_seconds % other.total_seconds)

    def __divmod__(self, other: Duration) -> tuple[int, Duration]:
        """Return the floor quotient and remainder of two durations."""
        if not isinstance(other, Duration):
            return NotImplemented
        quot, rem = divmod(self.total_seconds, other.total_seconds)
        return quot, Duration(rem)

    def __neg__(self) -> Duration:
        return Duration(-self.total_seconds)

    def __pos__(self) -> Duration:
        return Duration(+self.total_seconds)

    def __bool__(self) -> bool:
        return bool(self.total_seconds)

    def __abs__(self) -> Duration:
        return Duration(abs(self.total_seconds))


def _exact_count(seconds: Fraction, quantum: Fraction) -> int:
    count = seconds / quantum
    if count.denominator != 1:
        raise ValueError(f'Duration is not exactly representable in {quantum} second increments.')
    return count.numerator
