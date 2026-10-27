"""Exact intervals, explicit timedelta conversion and precision alignment."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from operator import add, eq, floordiv, ge, gt, le, lt, mod, mul, sub, truediv

import pytest
from pydantic import TypeAdapter, ValidationError

from berries.game.duration import (
    DOTNET_TICK,
    GAME_TIMER_FORMAT,
    GAME_TIMER_SECONDS_FORMAT,
    MICROSECOND,
    TIMER_FRAME,
    Duration,
)


def test_frozen_dataclass_construction_and_repr() -> None:
    assert Duration().total_seconds == Fraction()
    assert not Duration()
    value = Duration(total_seconds=Fraction(1, 3))
    attribute = 'total_seconds'
    with pytest.raises(FrozenInstanceError):
        setattr(value, attribute, Fraction(2))
    assert value.total_seconds == Fraction(1, 3)
    assert repr(value) == 'Duration(total_seconds=Fraction(1, 3))'


@pytest.mark.parametrize(
    'seconds', (Fraction(0), Fraction(1, 3), Fraction(-1, 3), Fraction(10**40))
)
def test_truth_value_and_unary_plus(seconds: Fraction) -> None:
    value = Duration(seconds)
    assert bool(value) == bool(seconds)
    result = +value
    assert type(result) is Duration
    assert result == value
    assert value.total_seconds == seconds


@pytest.mark.parametrize('value', (0, -1, 0.1, Decimal('0.1'), Fraction(1, 3), '1/3', '0.1'))
def test_from_seconds_converts_seconds_to_fraction(
    value: float | Decimal | Fraction | str,
) -> None:
    interval = Duration.from_seconds(value)
    assert type(interval.total_seconds) is Fraction
    assert interval.total_seconds == Fraction(value)


def test_from_seconds_preserves_fraction_ratio_semantics() -> None:
    assert Duration.from_seconds(0) == Duration()
    assert Duration.from_seconds(numerator=3, denominator=-2).total_seconds == Fraction(-3, 2)
    assert Duration.from_seconds(Fraction(1, 7), Fraction(2, 3)).total_seconds == Fraction(3, 14)
    with pytest.raises(ZeroDivisionError):
        Duration.from_seconds(1, 0)
    with pytest.raises(ValueError):
        Duration.from_seconds('invalid')


@pytest.mark.parametrize('factory', (Duration.from_seconds,))
def test_from_seconds_requires_an_explicit_value(factory: Callable[..., Duration]) -> None:
    with pytest.raises(TypeError):
        factory()


def test_from_seconds_accepts_integer_ratio_protocol() -> None:
    class Ratio:
        def as_integer_ratio(self) -> tuple[int, int]:
            return 1, 3

    assert Duration.from_seconds(Ratio()).total_seconds == Fraction(1, 3)


@pytest.mark.parametrize('value', (0, True, 0.1, Decimal('0.1'), '1/3', None))
@pytest.mark.parametrize('constructor', (Duration,))
def test_direct_construction_rejects_non_fraction_values(
    constructor: Callable[..., Duration], value: object
) -> None:
    with pytest.raises(TypeError, match='total_seconds must be a Fraction'):
        constructor(total_seconds=value)


def test_dataclass_replace_and_pydantic_keep_the_same_field_contract() -> None:
    value = Duration(Fraction(1, 3))
    assert replace(value) == value
    assert replace(value, total_seconds=Fraction(2, 3)) == Duration(Fraction(2, 3))
    with pytest.raises(TypeError, match='total_seconds must be a Fraction'):
        replace(value, total_seconds=1)
    adapter = TypeAdapter(Duration)
    assert adapter.validate_python({'total_seconds': '1/3'}) == value
    assert adapter.dump_python(value, mode='json') == {'total_seconds': '1/3'}
    assert adapter.validate_json(adapter.dump_json(value)) == value
    with pytest.raises(ValidationError):
        adapter.validate_python({'total_seconds': 'invalid'})


@pytest.mark.parametrize('count', (0, 1, -1, 123_456, -123_456, 10**40))
def test_native_units_roundtrip_without_precision_loss(count: int) -> None:
    ticks = Duration.from_dotnet_ticks(count)
    frames = Duration.from_timer_frames(count)
    assert ticks.total_seconds == count * DOTNET_TICK
    assert ticks.to_dotnet_ticks() == count
    assert frames.total_seconds == count * TIMER_FRAME
    assert frames.to_timer_frames() == count
    assert frames.to_dotnet_ticks() == count * 170_000


@pytest.mark.parametrize(
    'value',
    (
        timedelta(),
        timedelta(microseconds=-1),
        timedelta(days=51, seconds=17, microseconds=123),
        timedelta.min,
        timedelta.max,
    ),
)
def test_timedelta_conversion_is_exact_but_does_not_imply_equality(value: timedelta) -> None:
    interval = Duration.from_timedelta(value)
    assert interval.to_timedelta() == value
    assert not eq(interval, value)
    assert not eq(value, interval)
    assert len({interval, value}) == 2
    assert hash(interval) == hash(Duration.from_timedelta(value))


def test_different_representations_compare_actual_elapsed_time() -> None:
    frame = Duration.from_timer_frames(1)
    ticks = Duration.from_dotnet_ticks(170_000)
    delta = timedelta(milliseconds=17)
    assert frame == ticks == Duration.from_timedelta(delta)
    assert len({frame, ticks, Duration.from_timedelta(delta)}) == 1
    assert Duration.from_dotnet_ticks(1) != timedelta()
    assert Duration.from_dotnet_ticks(-1) != timedelta()
    assert hash(Duration(Fraction(1, 3))) == hash(Duration(Fraction(2, 6)))
    huge = Duration(Fraction(10**40))
    assert hash(huge) == hash(Duration(Fraction(10**40)))
    with pytest.raises(OverflowError):
        huge.to_timedelta()


@pytest.mark.parametrize(
    ('first', 'second'),
    (
        (Duration.from_timer_frames(1), Duration.from_dotnet_ticks(1)),
        (Duration.from_dotnet_ticks(-1), Duration.from_timedelta(timedelta(microseconds=1))),
        (Duration.from_timedelta(timedelta(days=-1)), Duration(Fraction(1, 3))),
    ),
)
def test_arithmetic_is_exact_and_returns_duration(first: Duration, second: Duration) -> None:
    result = first + second
    assert type(result) is Duration
    assert result.total_seconds == first.total_seconds + second.total_seconds
    assert second + first == result
    difference = first - second
    assert type(difference) is Duration
    assert difference.total_seconds == first.total_seconds - second.total_seconds
    assert second - first == -difference
    assert abs(-Duration.from_dotnet_ticks(1)) == Duration.from_dotnet_ticks(1)


@pytest.mark.parametrize('factor', (0, -2, 3, Fraction(2, 3), 0.1, 10**40))
def test_scaling_preserves_exact_seconds(factor: float | Fraction) -> None:
    value = Duration.from_seconds(1, 3)
    expected = Duration(Fraction(1, 3) * Fraction(factor))
    assert value * factor == factor * value == expected
    if factor:
        assert value / factor == Duration(Fraction(1, 3) / Fraction(factor))
        assert (value * factor) / factor == value
    assert value == Duration.from_seconds(1, 3)


@pytest.mark.parametrize('sign', (1, -1))
@pytest.mark.parametrize('divisor_sign', (1, -1))
def test_duration_division_and_remainders_obey_python_numeric_semantics(
    sign: int, divisor_sign: int
) -> None:
    value = Duration.from_seconds(sign * 7, 3)
    divisor = Duration.from_seconds(divisor_sign * 2, 3)
    ratio = value / divisor
    assert type(ratio) is Fraction
    assert ratio == Fraction(sign * divisor_sign * 7, 2)
    quotient, remainder = divmod(value, divisor)
    assert type(quotient) is int and type(remainder) is Duration
    assert quotient == value // divisor == ratio // 1
    assert remainder == value % divisor
    assert value == divisor * quotient + remainder
    assert abs(remainder) < abs(divisor)
    assert (remainder.total_seconds < 0) == (divisor_sign < 0)


@pytest.mark.parametrize('operation', (truediv, floordiv, mod, divmod))
def test_duration_division_rejects_zero(
    operation: Callable[[Duration, Duration], object],
) -> None:
    with pytest.raises(ZeroDivisionError):
        operation(Duration.from_seconds(1), Duration())


@pytest.mark.parametrize('zero', (0, 0.0, Fraction(0)))
def test_scaling_division_rejects_zero(zero: float | Fraction) -> None:
    with pytest.raises(ZeroDivisionError):
        Duration.from_seconds(1) / zero  # pyright: ignore[reportUnusedExpression]


@pytest.mark.parametrize('other', ('2', Decimal(2), None, timedelta()))
@pytest.mark.parametrize('operation', (mul, truediv, floordiv, mod, divmod))
def test_multiplicative_operators_reject_unrelated_operands(
    other: object, operation: Callable[[object, object], object]
) -> None:
    with pytest.raises(TypeError):
        operation(Duration.from_seconds(1), other)
    with pytest.raises(TypeError):
        operation(other, Duration.from_seconds(1))


@pytest.mark.parametrize(
    'operation,first,second',
    (
        (mul, Duration.from_seconds(1), Duration.from_seconds(1)),
        (truediv, 2, Duration.from_seconds(1)),
        (floordiv, Duration.from_seconds(1), 2),
        (mod, Duration.from_seconds(1), 2),
        (divmod, Duration.from_seconds(1), 2),
    ),
)
def test_multiplicative_operators_reject_incompatible_dimensions(
    operation: Callable[[object, object], object], first: object, second: object
) -> None:
    with pytest.raises(TypeError):
        operation(first, second)


@pytest.mark.parametrize('operation', (lt, le, gt, ge))
def test_ordering_between_durations_in_both_directions(
    operation: Callable[[object, object], object],
) -> None:
    short = Duration.from_dotnet_ticks(9)
    long = Duration.from_timedelta(timedelta(microseconds=1))
    assert operation(short, long) == operation(Fraction(9, 10), Fraction(1))
    assert operation(long, short) == operation(Fraction(1), Fraction(9, 10))


@pytest.mark.parametrize(
    'other', (1, 1.0, Fraction(1), None, timedelta(), timedelta(microseconds=-1))
)
@pytest.mark.parametrize('operation', (add, sub, lt, le, gt, ge))
def test_operators_reject_non_duration_operands(
    other: object, operation: Callable[[object, object], object]
) -> None:
    with pytest.raises(TypeError):
        operation(Duration.from_dotnet_ticks(1), other)
    with pytest.raises(TypeError):
        operation(other, Duration.from_dotnet_ticks(1))
    assert eq(Duration.from_dotnet_ticks(1), other) is False


def test_dataclass_equality_distinguishes_subclass_but_inherits_arithmetic() -> None:
    class CustomDuration(Duration):
        pass

    value = CustomDuration(Fraction(17, 1_000))
    base = Duration.from_timer_frames(1)
    assert not eq(value, base)
    assert not eq(base, value)
    assert value == CustomDuration(Fraction(17, 1_000))
    assert len({value, CustomDuration(Fraction(17, 1_000)), base}) == 2
    assert type(value + Duration.from_dotnet_ticks(1)) is Duration
    assert type(+value) is Duration
    assert (+value).total_seconds == value.total_seconds
    assert value < CustomDuration(Fraction(18, 1_000))
    for operation in (lt, le, gt, ge):
        with pytest.raises(TypeError):
            operation(value, base)
        with pytest.raises(TypeError):
            operation(base, value)


@pytest.mark.parametrize(
    ('value', 'conversion'),
    (
        (Duration(Fraction(1, 3)), Duration.to_dotnet_ticks),
        (Duration.from_dotnet_ticks(-1), Duration.to_timer_frames),
        (Duration.from_dotnet_ticks(1), Duration.to_timedelta),
    ),
)
def test_exact_conversion_rejects_remainders(
    value: Duration, conversion: Callable[[Duration], object]
) -> None:
    with pytest.raises(ValueError, match='not exactly representable'):
        conversion(value)


@pytest.mark.parametrize(
    ('ticks', 'floor_frames', 'ceil_frames', 'round_frames'),
    (
        (0, 0, 0, 0),
        (1, 0, 1, 0),
        (85_000, 0, 1, 0),
        (85_001, 0, 1, 1),
        (170_000, 1, 1, 1),
        (255_000, 1, 2, 2),
        (425_000, 2, 3, 2),
        (-1, -1, 0, 0),
        (-85_000, -1, 0, 0),
        (-85_001, -1, 0, -1),
        (-170_000, -1, -1, -1),
        (-255_000, -2, -1, -2),
        (-425_000, -3, -2, -2),
        (170_000 * 10**30 + 1, 10**30, 10**30 + 1, 10**30),
    ),
)
def test_frame_alignment_is_exact_and_signed(
    ticks: int, floor_frames: int, ceil_frames: int, round_frames: int
) -> None:
    value = Duration.from_dotnet_ticks(ticks)
    assert value.floor(TIMER_FRAME).to_timer_frames() == floor_frames
    assert value.ceil(TIMER_FRAME).to_timer_frames() == ceil_frames
    assert value.round(TIMER_FRAME).to_timer_frames() == round_frames
    assert value.is_aligned(TIMER_FRAME) == (ticks % 170_000 == 0)
    assert value.to_dotnet_ticks() == ticks


@pytest.mark.parametrize('ticks,expected', ((15, 2), (25, 2), (-15, -2), (-25, -2)))
def test_microsecond_alignment_uses_the_same_tie_rule(ticks: int, expected: int) -> None:
    value = Duration.from_dotnet_ticks(ticks)
    assert value.round(MICROSECOND).to_timedelta() == timedelta(microseconds=expected)
    assert value.floor(MICROSECOND).to_timedelta() == timedelta(microseconds=ticks // 10)
    assert value.ceil(MICROSECOND).to_timedelta() == timedelta(microseconds=-(-ticks // 10))


def test_alignment_accepts_explicit_conversion_and_arbitrary_rational_quantum() -> None:
    value = Duration.from_timedelta(timedelta(milliseconds=25))
    assert value.floor(TIMER_FRAME) == Duration.from_timer_frames(1)
    assert value.ceil(TIMER_FRAME) == Duration.from_timer_frames(2)
    assert value.round(TIMER_FRAME) == Duration.from_timer_frames(1)
    third = Duration(Fraction(1, 3))
    assert not third.is_aligned(DOTNET_TICK)
    assert third.floor(DOTNET_TICK) == Duration.from_dotnet_ticks(3_333_333)
    assert third.ceil(DOTNET_TICK) == Duration.from_dotnet_ticks(3_333_334)
    assert third.is_aligned(Fraction(1, 9))
    assert Duration(Fraction(1, 2)).floor(Fraction(1, 3)) == third


@pytest.mark.parametrize('sign', (1, -1))
@pytest.mark.parametrize('frames,extra_ticks', ((0, 0), (0, 1), (1, 0), (1, 85_000), (10**30, 1)))
def test_trunc_alignment_is_exact_and_toward_zero(sign: int, frames: int, extra_ticks: int) -> None:
    ticks = sign * (frames * 170_000 + extra_ticks)
    value = Duration.from_dotnet_ticks(ticks)
    assert value.trunc(TIMER_FRAME) == Duration.from_timer_frames(sign * frames)
    assert value.to_dotnet_ticks() == ticks
    assert Duration(sign * Fraction(1, 2)).trunc(Fraction(1, 3)) == Duration(sign * Fraction(1, 3))


@pytest.mark.parametrize('quantum', (Fraction(0), Fraction(-1), -TIMER_FRAME))
@pytest.mark.parametrize(
    'operation',
    (Duration.floor, Duration.ceil, Duration.trunc, Duration.round, Duration.is_aligned),
)
def test_alignment_rejects_nonpositive_quantum(
    quantum: Fraction, operation: Callable[[Duration, Fraction], object]
) -> None:
    with pytest.raises(ValueError, match='positive'):
        operation(Duration.from_dotnet_ticks(1), quantum)


def test_dotnet_ticks_are_not_limited_by_save_file_range() -> None:
    ticks = 10**40
    assert Duration.from_dotnet_ticks(ticks).to_dotnet_ticks() == ticks


@pytest.mark.parametrize('sign', (1, -1))
@pytest.mark.parametrize(
    ('ticks', 'components', 'formatted', 'seconds'),
    (
        (1, (0, 0, 0, 0), '0:00.000', '00:00'),
        (9_999_999, (0, 0, 0, 999), '0:00.999', '00:00'),
        (10_000_000, (0, 0, 1, 0), '0:01.000', '00:01'),
        (599_999_999, (0, 0, 59, 999), '0:59.999', '00:59'),
        (600_000_000, (0, 1, 0, 0), '1:00.000', '01:00'),
        (35_999_999_999, (0, 59, 59, 999), '59:59.999', '59:59'),
        (36_000_000_000, (1, 0, 0, 0), '1:00:00.000', '1:00:00'),
        (37_234_560_001, (1, 2, 3, 456), '1:02:03.456', '1:02:03'),
        (1_838_450_060_000, (51, 4, 5, 6), '51:04:05.006', '51:04:05'),
    ),
)
def test_components_and_game_format_retain_precision_and_one_overall_sign(
    sign: int, ticks: int, components: tuple[int, int, int, int], formatted: str, seconds: str
) -> None:
    value = Duration.from_dotnet_ticks(sign * ticks)
    assert (value.hours, value.minutes, value.seconds, value.milliseconds) == components
    assert (value.total_seconds < 0) is (sign < 0)
    assert value.subseconds == Fraction(ticks % 10_000_000, 10_000_000)
    prefix = '-' if sign < 0 else ''
    assert format(value, GAME_TIMER_FORMAT) == prefix + formatted
    assert str(value) == format(value, '') == f'{value}' == prefix + formatted
    assert format(value, GAME_TIMER_SECONDS_FORMAT) == prefix + seconds
    assert value.to_dotnet_ticks() == sign * ticks


def test_components_and_game_format_accept_arbitrary_precision_and_zero() -> None:
    third = Duration(Fraction(-1, 3))
    assert third.subseconds == Fraction(1, 3)
    assert (third.hours, third.minutes, third.seconds, third.milliseconds) == (0, 0, 0, 333)
    zero = Duration.from_dotnet_ticks(0)
    assert zero.total_seconds == 0
    assert (zero.hours, zero.minutes, zero.seconds, zero.milliseconds) == (0, 0, 0, 0)
    assert format(third, GAME_TIMER_FORMAT) == '-0:00.333'
    assert str(third) == '-0:00.333'
    assert third.total_seconds == Fraction(-1, 3)
    assert format(zero, GAME_TIMER_FORMAT) == '0:00.000'
    assert str(zero) == '0:00.000'
    assert format(zero, GAME_TIMER_SECONDS_FORMAT) == '00:00'
    assert (
        format(Duration.from_timedelta(timedelta(milliseconds=17)), GAME_TIMER_FORMAT) == '0:00.017'
    )


@pytest.mark.parametrize('sign', (1, -1))
@pytest.mark.parametrize(
    ('spec', 'expected'),
    (
        ('%H:%M:%S', '03:04:05'),
        ('%-H:%-M:%-S', '3:4:5'),
        ('%Qh:%M:%S', '51:04:05'),
        ('%Qm', '3064'),
        ('%Qs', '183845'),
        ('%f', '006000'),
        ('%3f', '006'),
        ('%7f', '0060001'),
        ('%10f', '0060001000'),
        ('elapsed %Qh:%M:%S %%', 'elapsed 51:04:05 %'),
        ('[%Qh:]%M:%S', '51:04:05'),
        ('[%Qh:%M|%-M]:%S.%3f', '51:04:05.006'),
    ),
)
def test_format_directives_and_sign_on_first_selected_number(
    sign: int, spec: str, expected: str
) -> None:
    value = Duration.from_dotnet_ticks(sign * 1_838_450_060_001)
    if sign < 0:
        # The label is literal: the sign precedes the first numeric replacement.
        expected = (
            expected.replace('elapsed ', 'elapsed -', 1)
            if spec.startswith('elapsed')
            else '-' + expected
        )
    assert format(value, spec) == expected


@pytest.mark.parametrize(
    ('spec', 'expected'),
    (
        ('[%Qh:]%M:%S', '-01:23'),
        ('[%Qh:%M|%-M]:%S', '-1:23'),
        ('[%Qh:%M]literal', 'literal'),
        ('[%Qh|none] %M', 'none -01'),
        ('[%Qh|%M] [%Qh|%S]', '-01 23'),
        (r'\[%M\|%S\] \\ %%', '[-01|23] \\ %'),
        ('literal %%', 'literal %'),
    ),
)
def test_optional_formats_and_literal_escaping(spec: str, expected: str) -> None:
    assert format(Duration.from_dotnet_ticks(-834_560_000), spec) == expected


def test_format_fractional_digits_truncate_without_carry_or_precision_loss() -> None:
    value = Duration(Fraction(-1, 3))
    assert f'{value:%S.%f}' == '-00.333333'
    assert f'{value:%S.%12f}' == '-00.333333333333'
    assert value.total_seconds == Fraction(-1, 3)
    almost_minute = Duration(Fraction(59_999_999, 1_000_000))
    assert f'{almost_minute:%M:%S.%3f}' == '00:59.999'
    assert format(value, '[%Qh]') == ''
    assert format(value, '[%3f]') == '-333'
    assert format(Duration(Fraction(1, 10_000)), '[%3f|zero]') == 'zero'
    assert format(value, '') == str(value) == format(value, GAME_TIMER_FORMAT)


@pytest.mark.parametrize(
    'spec',
    (
        '%',
        '%-',
        '%Q',
        '%Qd',
        '%d',
        '%2H',
        '%0f',
        '%3',
        '%-f',
        '[%H',
        '[%H|',
        ']',
        '|',
        '[literal]',
        '[literal|%H]',
        '[%H|%M|%S]',
        '[[%H]]',
        '[%Qh|[%M]]',
        '[%Qh|%bad]',
        '\\',
        r'\x',
    ),
)
def test_invalid_formats_are_rejected_even_in_unselected_branches(spec: str) -> None:
    with pytest.raises(ValueError):
        format(Duration.from_timer_frames(0), spec)
