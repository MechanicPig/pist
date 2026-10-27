"""Resolve duration text against the shared format grammar without guessing."""

from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction

from ._format import (
    Directive,
    OptionalGroup,
    Token,
    format_seconds,
    parse_spec,
)


@dataclass(frozen=True, slots=True)
class _Match:
    position: int = 0
    fields: tuple[tuple[Directive, int], ...] = ()
    negative: bool = False
    sign_position: int | None = None


def parse_duration(text: str, spec: str) -> Fraction:
    """Recover represented seconds; reject mismatches and distinct valid interpretations."""
    tokens = parse_spec(spec)
    if not any(isinstance(token, (Directive, OptionalGroup)) for token in tokens):
        raise ValueError('A duration parsing format must contain a numeric directive.')
    candidates: set[Fraction] = set()
    for match in _matches(text, tokens):
        if match.position != len(text):
            continue
        seconds = _seconds(match.fields)
        if match.negative:
            seconds = -seconds
        rendered = format_seconds(seconds, tokens)
        # Truncated negative zero has no distinct rational representation.
        expected = (
            text[: match.sign_position] + text[match.sign_position + 1 :]
            if seconds == 0 and match.sign_position is not None
            else text
        )
        if rendered == expected:
            candidates.add(seconds)
            if len(candidates) > 1:
                raise ValueError('Duration text has multiple interpretations for this format.')
    if not candidates:
        raise ValueError('Duration text does not match its format or has contradictory fields.')
    return candidates.pop()


def _distinct(states: list[_Match]) -> list[_Match]:
    """Avoid hashing single candidates and compare just once for a pair."""
    if len(states) <= 1:
        return states
    if len(states) == 2:
        return states[:1] if states[0] == states[1] else states
    return list(set(states))


def _matches(text: str, tokens: tuple[Token, ...]) -> list[_Match]:
    """Advance directly on a single path; merge only after alternatives appear."""
    states = [_Match()]
    for token in tokens:
        if isinstance(token, OptionalGroup):
            primary = _branch_matches(text, token.primary, states, nonzero_guard=True)
            alternative = _branch_matches(text, token.alternative, states, nonzero_guard=False)
            states = _distinct(primary + alternative)
        else:
            states = _advance(text, token, states)
        if not states:
            break
    return states


def _branch_matches(
    text: str, tokens: tuple[Token, ...], states: list[_Match], *, nonzero_guard: bool
) -> list[_Match]:
    # The grammar rejects nested groups. Both the outer sequence and each branch
    # are iterative; equivalent paths merge before consuming any following token.
    for token in tokens:
        assert not isinstance(token, OptionalGroup)
        states = _advance(text, token, states, nonzero=nonzero_guard)
        if isinstance(token, Directive):
            nonzero_guard = False
        if not states:
            break
    return states


def _advance(
    text: str, token: str | Directive, states: list[_Match], *, nonzero: bool = False
) -> list[_Match]:
    if isinstance(token, str):
        return [
            _Match(state.position + len(token), state.fields, state.negative, state.sign_position)
            for state in states
            if text.startswith(token, state.position)
        ]
    # One source's numeric candidates consume different lengths, and literals
    # preserve distinct states. Only multiple numeric sources can converge.
    matched = [
        result
        for state in states
        for result in _numeric_matches(text, token, state, nonzero=nonzero)
    ]
    return _distinct(matched) if len(states) > 1 else matched


def _numeric_matches(
    text: str, directive: Directive, state: _Match, *, nonzero: bool = False
) -> Iterator[_Match]:
    start = state.position
    negative = state.negative
    sign_position = state.sign_position
    if not state.fields and start < len(text) and text[start] == '-':
        negative = True
        sign_position = start
        start += 1
    maximum = directive.width
    if maximum is None:
        maximum = len(text) - start if directive.code.startswith('Q') else 2
    end = start
    while end < min(len(text), start + maximum) and text[end] in '0123456789':
        end += 1
    if directive.width is not None:
        lengths = (directive.width,)
    else:
        maximum = end - start if directive.code.startswith('Q') else min(2, end - start)
        lengths = range(1, maximum + 1)
    for length in lengths:
        if start + length > end:
            continue
        digits = text[start : start + length]
        if directive.width is None and length > 1 and digits[0] == '0':
            continue
        value = int(digits)
        if nonzero and value == 0:
            continue
        if directive.code == 'H' and value >= 24:
            continue
        if directive.code in ('M', 'S') and value >= 60:
            continue
        # Padding is a textual constraint, not another numeric field. Retain the
        # width only for fractions, whose precision changes their numeric meaning.
        field = Directive(directive.code, directive.width if directive.code == 'f' else None)
        previous = next((count for key, count in state.fields if key == field), None)
        if previous is not None and previous != value:
            continue
        fields = state.fields
        if previous is None:
            fields = tuple(
                sorted(
                    (*fields, (field, value)), key=lambda item: (item[0].code, item[0].width or 0)
                )
            )
        yield _Match(start + length, fields, negative, sign_position)


def _seconds(fields: tuple[tuple[Directive, int], ...]) -> Fraction:
    """Use the most complete totals and finest fraction; rendering checks redundancy."""
    counts = {directive.code: value for directive, value in fields}
    if 'Qs' in counts:
        whole = counts['Qs']
    elif 'Qm' in counts:
        whole = counts['Qm'] * 60 + counts.get('S', 0)
    else:
        whole = counts.get('Qh', counts.get('H', 0)) * 3_600
        whole += counts.get('M', 0) * 60 + counts.get('S', 0)
    fractional = [(directive.width, value) for directive, value in fields if directive.code == 'f']
    if not fractional:
        return Fraction(whole)
    width, value = max(fractional)
    assert width is not None
    return whole + Fraction(value, 10**width)
