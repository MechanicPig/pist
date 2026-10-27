"""Shared duration format grammar and rendering, independent of time arithmetic."""

from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .duration import Duration


@dataclass(frozen=True, slots=True)
class Directive:
    code: str
    width: int | None = None


@dataclass(frozen=True, slots=True)
class OptionalGroup:
    primary: tuple[Token, ...]
    alternative: tuple[Token, ...]


type Token = str | Directive | OptionalGroup


@dataclass(frozen=True, slots=True)
class _Part:
    text: str
    numeric: bool = False
    nonzero: bool = False


def parse_spec(spec: str) -> tuple[Token, ...]:
    """Validate the entire grammar, including unselected optional branches."""
    parts, _, _ = _sequence(spec, 0, in_group=False)
    return parts


def format_duration(value: Duration, spec: str) -> str:
    """Render directives and optional branches, adding at most one sign."""
    return format_seconds(value.total_seconds, parse_spec(spec))


def format_seconds(seconds: Fraction, tokens: tuple[Token, ...]) -> str:
    """Render already validated tokens for an exact signed number of seconds."""
    output: list[str] = []
    needs_sign = seconds < 0
    for part in _render(abs(seconds), tokens):
        if needs_sign and part.numeric:
            output.append('-')
            needs_sign = False
        output.append(part.text)
    return ''.join(output)


def _render(seconds: Fraction, tokens: tuple[Token, ...]) -> list[_Part]:
    parts: list[_Part] = []
    for token in tokens:
        if isinstance(token, str):
            parts.append(_Part(token))
        elif isinstance(token, OptionalGroup):
            primary = _render(seconds, token.primary)
            guard = next(part for part in primary if part.numeric)
            parts.extend(primary if guard.nonzero else _render(seconds, token.alternative))
        else:
            count = _directive_count(seconds, token)
            text = str(count) if token.width is None else f'{count:0{token.width}}'
            parts.append(_Part(text, numeric=True, nonzero=count != 0))
    return parts


def _directive_count(seconds: Fraction, directive: Directive) -> int:
    match directive.code:
        case 'Qh':
            return seconds // 3_600
        case 'Qm':
            return seconds // 60
        case 'Qs':
            return seconds // 1
        case 'H':
            return (seconds // 3_600) % 24
        case 'M':
            return (seconds // 60) % 60
        case 'S':
            return (seconds // 1) % 60
        case _:
            assert directive.width is not None
            return (seconds % 1) * 10**directive.width // 1


def _sequence(spec: str, start: int, *, in_group: bool) -> tuple[tuple[Token, ...], int, str]:
    parts: list[Token] = []
    index = start
    while index < len(spec):
        char = spec[index]
        index += 1
        if char == '\\':
            if index == len(spec) or spec[index] not in '\\[]|':
                raise ValueError('Expected an escaped backslash, bracket or pipe.')
            token: Token = spec[index]
            index += 1
        elif char == '%':
            token, index = _directive(spec, index)
        elif char == '[':
            if in_group:
                raise ValueError('Nested duration format groups are not supported.')
            primary, index, delimiter = _sequence(spec, index, in_group=True)
            alternative: tuple[Token, ...] = ()
            if delimiter == '|':
                alternative, index, delimiter = _sequence(spec, index, in_group=True)
            if delimiter != ']':
                raise ValueError('Expected one alternative at most and a closing bracket.')
            if not any(isinstance(part, Directive) for part in primary):
                raise ValueError('An optional group needs a numeric directive in its first branch.')
            token = OptionalGroup(primary, alternative)
        elif char in ']|':
            if not in_group:
                raise ValueError(f'Unexpected duration format delimiter: {char!r}.')
            return tuple(parts), index, char
        else:
            token = char
        if isinstance(token, str) and parts and isinstance(parts[-1], str):
            previous = parts.pop()
            assert isinstance(previous, str)
            parts.append(previous + token)
        else:
            parts.append(token)
    return tuple(parts), index, ''


def _directive(spec: str, index: int) -> tuple[str | Directive, int]:
    if index == len(spec):
        raise ValueError('Incomplete duration format directive.')
    code = spec[index]
    index += 1
    if code == '%':
        return '%', index
    if code == 'Q':
        if index == len(spec) or spec[index] not in 'hms':
            raise ValueError('Expected h, m or s after %Q.')
        return Directive('Q' + spec[index]), index + 1
    if code == 'f' or code in '0123456789':
        digits = 6
        if code != 'f':
            start = index - 1
            while index < len(spec) and spec[index] in '0123456789':
                index += 1
            if index == len(spec) or spec[index] != 'f':
                raise ValueError('A fractional width must be followed by f.')
            digits = int(spec[start:index])
            index += 1
            if digits == 0:
                raise ValueError('Fractional width must be positive.')
        return Directive('f', digits), index
    unpadded = code == '-'
    if unpadded:
        if index == len(spec):
            raise ValueError('Expected H, M or S after %-.')
        code = spec[index]
        index += 1
    if code not in 'HMS':
        raise ValueError(f'Unknown duration format directive: {code!r}.')
    return Directive(code, None if unpadded else 2), index
