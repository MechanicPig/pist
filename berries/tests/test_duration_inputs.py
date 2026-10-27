"""Exact multi-unit construction and unambiguous format-based duration parsing."""

import subprocess
import sys
from collections.abc import Callable
from fractions import Fraction

import pytest

from berries.game.duration import GAME_TIMER_FORMAT, GAME_TIMER_SECONDS_FORMAT, Duration


def test_units_are_additive_signed_and_not_restricted_to_component_ranges() -> None:
    value = Duration.from_units(
        hours=1,
        minutes=-90,
        seconds=61,
        subseconds=Fraction(1, 3),
        milliseconds=1_500,
        microseconds=-2,
        nanoseconds=1,
    )
    assert value.total_seconds == (
        Fraction(-1_739)
        + Fraction(1, 3)
        + Fraction(3, 2)
        - Fraction(2, 1_000_000)
        + Fraction(1, 1_000_000_000)
    )
    assert Duration.from_units() == Duration()
    assert Duration.from_units(hours=Fraction(1, 2)) == Duration.from_units(minutes=30)
    assert Duration.from_units(seconds=0.1).total_seconds == Fraction(0.1)
    assert Duration.from_units(subseconds=Fraction(1, 3)) == Duration.from_seconds(1, 3)
    assert Duration.from_units(nanoseconds=1).total_seconds == Fraction(1, 1_000_000_000)


@pytest.mark.parametrize('factory', (Duration.from_units,))
def test_units_are_keyword_only(factory: Callable[..., Duration]) -> None:
    with pytest.raises(TypeError):
        factory(1, 2, 3)


@pytest.mark.parametrize('sign', (1, -1))
@pytest.mark.parametrize(
    'seconds', (Fraction(0), Fraction(1, 3), Fraction(834_560_001, 10_000_000), Fraction(10**30))
)
@pytest.mark.parametrize(
    'spec,digits',
    (
        (GAME_TIMER_FORMAT, 3),
        (GAME_TIMER_SECONDS_FORMAT, 0),
        ('%Qh:%M:%S.%7f', 7),
        ('%Qs.%12f', 12),
        ('', 3),
    ),
)
def test_parsing_recovers_exactly_the_precision_that_was_formatted(
    sign: int, seconds: Fraction, spec: str, digits: int
) -> None:
    value = Duration(sign * seconds)
    text = format(value, spec)
    parsed = Duration.from_format(text, spec)
    expected = Fraction(abs(seconds) * 10**digits // 1, 10**digits) * sign
    assert parsed.total_seconds == expected
    assert value.total_seconds == sign * seconds


@pytest.mark.parametrize(
    'text,spec,seconds',
    (
        ('03:04:05', '%H:%M:%S', Fraction(11_045)),
        ('3:4:5', '%-H:%-M:%-S', Fraction(11_045)),
        ('51:04:05', '%Qh:%M:%S', Fraction(183_845)),
        ('3064', '%Qm', Fraction(183_840)),
        ('-123', '%Qs', Fraction(-123)),
        ('006', '%3f', Fraction(6, 1_000)),
        ('elapsed -51:04:05 %', 'elapsed %Qh:%M:%S %%', Fraction(-183_845)),
        ('[-01|23] \\ %', r'\[%M\|%S\] \\ %%', Fraction(-83)),
        ('none -01', '[%Qh|none] %M', Fraction(-60)),
        ('-01 23', '[%Qh|%M] [%Qh|%S]', Fraction(-83)),
        ('literal', '[%Qh:%M]literal', Fraction(0)),
        ('', '[%Qh]', Fraction(0)),
        ('1:01/61/3661/01', '%Qh:%M/%Qm/%Qs/%H', Fraction(3_661)),
        ('123', '%Qh%M', Fraction(4_980)),
        ('1/1', '%Qs/%Qs', Fraction(1)),
        ('01/1', '%S/%-S', Fraction(1)),
        ('1/01', '%-S/%S', Fraction(1)),
        ('1/60', '[%Qh|%Qm]/%Qs', Fraction(60)),
        ('1/3600', '[%Qh|%Qm]/%Qs', Fraction(3600)),
        ('123/1234567', '%3f/%7f', Fraction(1_234_567, 10_000_000)),
        ('-0:00.000', GAME_TIMER_FORMAT, Fraction(0)),
    ),
)
def test_parsing_totals_components_literals_and_optional_branches(
    text: str, spec: str, seconds: Fraction
) -> None:
    assert Duration.from_format(text, spec).total_seconds == seconds


@pytest.mark.parametrize(
    'text,spec',
    (
        ('1:60:00', '%Qh:%M:%S'),
        ('24:00:00', '%H:%M:%S'),
        ('1:00:60', '%Qh:%M:%S'),
        ('1:2:03', '%Qh:%M:%S'),
        ('01', '%-M'),
        ('01', '%Qh'),
        ('+1', '%Qs'),
        ('--1', '%Qs'),
        ('01:-23', '%M:%S'),
        (' 1', '%Qs'),
        ('1 ', '%Qs'),
        ('1 trailing', '%Qs'),
        ('１２', '%Qs'),
        ('1234', '%3f'),
        ('0:01:23', '[%Qh:]%M:%S'),
        ('1/2', '%Qs/%Qs'),
        ('01/2', '%S/%-S'),
        ('1/00', '%Qh/%H'),
        ('123/124', '%3f/%3f'),
        ('123/1240000', '%3f/%7f'),
        ('literal', 'literal'),
    ),
)
def test_invalid_noncanonical_and_contradictory_text_is_rejected(text: str, spec: str) -> None:
    with pytest.raises(ValueError):
        Duration.from_format(text, spec)


@pytest.mark.parametrize('text,spec', (('123', '%-M%-S'), ('1', '[%Qh|%Qm]')))
def test_multiple_distinct_interpretations_are_rejected(text: str, spec: str) -> None:
    with pytest.raises(ValueError, match='multiple interpretations'):
        Duration.from_format(text, spec)


@pytest.mark.parametrize('spec', ('%', '%Qd', '%0f', '[%Qh|%bad]', '[[%H]]', '[literal]'))
def test_invalid_format_is_rejected_even_when_a_branch_would_be_unused(spec: str) -> None:
    with pytest.raises(ValueError):
        Duration.from_format('0', spec)


def test_factories_preserve_the_requested_subclass() -> None:
    class CustomDuration(Duration):
        pass

    assert type(CustomDuration.from_units(minutes=1)) is CustomDuration
    assert type(CustomDuration.from_format('01:00', GAME_TIMER_SECONDS_FORMAT)) is CustomDuration


@pytest.mark.parametrize('seconds', (0, 59, -59))
def test_long_flat_formats_do_not_depend_on_python_recursion_depth(seconds: int) -> None:
    n = 2_000
    text = ('-' if seconds < 0 else '') + f'{abs(seconds):02}:' * n
    assert Duration.from_format(text, '%S:' * n) == Duration.from_seconds(seconds)


def test_equivalent_optional_branches_complete_without_path_explosion() -> None:
    # A subprocess gives the old exponential search a bounded failure mode.
    # The timeout is deliberately broad, not a machine-specific speed assertion.
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            """
from berries.game.duration import Duration
for n in (16, 64, 256):
    spec = '[%S|%S]' * n
    for seconds in (0, 1, -1, 59, -59):
        text = ('-' if seconds < 0 else '') + f'{abs(seconds):02}' * n
        assert Duration.from_format(text, spec) == Duration.from_seconds(seconds)
    assert Duration.from_format('-' + '00' * n, spec) == Duration()
    for text, spec in (
        ('01' * (n - 1) + '02', spec),
        ('1 ' * n, '[%Qh|%Qm] ' * n),
    ):
        try:
            Duration.from_format(text, spec)
        except ValueError:
            pass
        else:
            raise AssertionError('Contradictory or ambiguous text must be rejected')
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
