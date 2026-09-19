"""Translate Selene syntax used by Loenn into standard Lua.

Selene's own parser is a source-to-source preprocessor.  This module ports its
documented token transformations so that the regular Lua parser can be used
without running any third-party plugin code.
"""

import re
from dataclasses import dataclass


class SeleneSyntaxError(ValueError):
    """A Selene extension could not be translated safely."""


@dataclass(slots=True)
class _Token:
    value: str
    line: int
    parsed: bool = False


_IDENTIFIER = re.compile(r'^[A-Za-z_]\w*$')
_ESCAPABLE = frozenset({"'", '"'})
_SPECIAL = frozenset(
    {'(', ')', '[', ']', '{', '}', ':', '?', ',', '+', '-', '*', '%', '^', '&', '~', '|', '@'}
)
_DOUBLE = frozenset({'/', '<', '>', '$'})
_COMPOUND = frozenset(
    {'+=', '-=', '*=', '/=', '//=', '%=', '^=', '&=', '|=', '>>=', '<<=', '..=', ':='}
)


def preprocess(content: str) -> str:
    """Return standard Lua equivalent to *content* without evaluating it.

    The conversion intentionally retains references to ``_selene`` runtime
    helpers.  They are syntactically valid Lua but remain unknown to pist's
    static evaluator, which is precisely the safe behaviour for dynamic code.
    """
    tokens = _tokenize(content)
    changed = False
    index = 0
    while index < len(tokens):
        value = tokens[index].value
        handler = {
            '->': _lambda,
            '=>': _lambda,
            '<-': _foreach,
            '?': _ternary,
            ':': _self_call,
            '[': _array_index,
            '@': _broadcast,
            '$': _dollar,
            '$$': _dollar_assignment,
        }.get(value)
        if value in _COMPOUND:
            start = _assignment(tokens, index)
        elif handler is not None:
            start = handler(tokens, index)
        else:
            start = None
        if start is not None:
            changed = True
            _retokenize_at(tokens, start)
            index = start
        else:
            index += 1
    return content if not changed else _join(tokens)


def _tokenize(content: str) -> list[_Token]:
    """Tokenize the subset Selene's source transformer operates on."""
    tokens: list[_Token] = []
    line = 1
    index = 0
    plain: list[str] = []
    plain_line = line

    def flush() -> None:
        nonlocal plain
        if plain:
            tokens.append(_Token(''.join(plain), plain_line))
            plain = []

    while index < len(content):
        char = content[index]
        if char.isspace():
            flush()
            line += char == '\n'
            index += 1
            continue
        if char in _ESCAPABLE:
            flush()
            start = index
            quote = char
            index += 1
            escaped = False
            while index < len(content):
                current = content[index]
                if current == '\n':
                    line += 1
                index += 1
                if escaped:
                    escaped = False
                elif current == '\\':
                    escaped = True
                elif current == quote:
                    break
            else:
                raise SeleneSyntaxError(f'unclosed quote near line {line}')
            tokens.append(_Token(content[start:index], line))
            continue
        if content.startswith('--', index):
            flush()
            end = _comment_end(content, index, line)
            line += content[index:end].count('\n')
            index = end
            continue
        if char == '[' and (end := _long_bracket_end(content, index)) is not None:
            flush()
            raw = content[index:end]
            tokens.append(_Token(raw, line))
            line += raw.count('\n')
            index = end
            continue
        if content.startswith('..=', index):
            flush()
            tokens.append(_Token('..=', line))
            index += 3
            continue
        if char == '.':
            if content.startswith('...', index):
                flush()
                tokens.append(_Token('...', line))
                index += 3
            elif content.startswith('..', index):
                flush()
                tokens.append(_Token('..', line))
                index += 2
            else:
                # Selene keeps member access and decimal literals in their
                # surrounding token (``entity.field`` and ``0.5``).
                if not plain:
                    plain_line = line
                plain.append(char)
                index += 1
            continue
        if char in _SPECIAL or char in _DOUBLE:
            flush()
            candidate = content[index : index + 3]
            if candidate in _COMPOUND:
                tokens.append(_Token(candidate, line))
                index += 3
            elif content[index : index + 2] in _COMPOUND | {
                '->',
                '=>',
                '<-',
                '$$',
                '//',
                '<<',
                '>>',
            }:
                tokens.append(_Token(content[index : index + 2], line))
                index += 2
            else:
                tokens.append(_Token(char, line))
                index += 1
            continue
        if not plain:
            plain_line = line
        plain.append(char)
        index += 1
    flush()
    return tokens


def _long_bracket_end(content: str, start: int) -> int | None:
    match = re.match(r'\[(=*)\[', content[start:])
    if match is None:
        return None
    closing = ']' + match.group(1) + ']'
    end = content.find(closing, start + len(match.group(0)))
    if end < 0:
        raise SeleneSyntaxError('unclosed long string')
    return end + len(closing)


def _comment_end(content: str, start: int, line: int) -> int:
    bracket_start = start + 2
    if (end := _long_bracket_end(content, bracket_start)) is not None:
        return end
    newline = content.find('\n', start)
    return len(content) if newline < 0 else newline


def _retokenize_at(tokens: list[_Token], index: int) -> None:
    token = tokens[index]
    replacement = _tokenize(token.value)
    for part in replacement:
        part.line += token.line - 1
        part.parsed = True
    tokens[index : index + 1] = replacement


def _join(tokens: list[_Token]) -> str:
    """Join tokens without changing Lua's lexical meaning.

    In particular, ``0 . 5`` is not a Lua decimal literal.  The original
    Selene implementation can retain untouched source lines; this standalone
    converter instead recreates the minimal separators needed between tokens.
    """
    result: list[str] = []
    previous = ''
    for token in tokens:
        if result and _needs_separator(previous, token.value):
            result.append(' ')
        result.append(token.value)
        previous = token.value
    return ''.join(result)


def _needs_separator(previous: str, current: str) -> bool:
    if previous == '-' and current == '-':
        return True
    if previous == '..' or current == '..':
        return True
    return _is_word_end(previous) and _is_word_start(current)


def _is_word_start(value: str) -> bool:
    return bool(value) and (value[0].isalnum() or value[0] in '_\'"')


def _is_word_end(value: str) -> bool:
    return bool(value) and (value[-1].isalnum() or value[-1] in '_\'"')


def _bracket(
    tokens: list[_Token], index: int, opening: str, closing: str, step: int
) -> tuple[str, int]:
    depth = 1
    values: list[str] = []
    while True:
        if not 0 <= index < len(tokens):
            direction = 'closing' if step > 0 else 'opening'
            raise SeleneSyntaxError(f'missing {direction} bracket {closing!r}')
        value = tokens[index].value
        if value == opening:
            depth += 1
        elif value == closing:
            depth -= 1
        if depth == 0:
            return ' '.join(values), index
        if step > 0:
            values.append(value)
        else:
            values.insert(0, value)
        index += step


def _replace(tokens: list[_Token], start: int, stop: int, value: str) -> int:
    line = tokens[start].line
    tokens[start : stop + 1] = [_Token(value, line, True)]
    return start


def _lambda(tokens: list[_Token], index: int) -> int | None:
    params_start = index - 2 if tokens[index - 1].value == ')' else index - 1
    params, start = _bracket(tokens, params_start, ')', '(', -1)
    body_start = index + 2 if tokens[index + 1].value == '(' else index + 1
    body, stop = _bracket(tokens, body_start, '(', ')', 1)
    parameters = [part.strip() for part in params.split(',') if part.strip()]
    if any(part != '...' and _IDENTIFIER.fullmatch(part) is None for part in parameters):
        raise SeleneSyntaxError(f'invalid lambda parameters near line {tokens[index].line}')
    condition = 'nil'
    if '!' in params:
        params, condition_body = params.split('!', maxsplit=1)
        parameters = [part.strip() for part in params.split(',') if part.strip()]
        condition = f'function({", ".join(parameters)}) return {condition_body} end'
    return _replace(
        tokens,
        start,
        stop,
        f'(_selene._newFunc(function({", ".join(parameters)}) return {body} end, {len(parameters)}, {condition}))',
    )


def _ternary(tokens: list[_Token], index: int) -> int | None:
    if index == 0 or tokens[index - 1].value != ')':
        return _inline_ternary(tokens, index)
    condition, start = _bracket(tokens, index - 2, ')', '(', -1)
    cases, stop = _bracket(tokens, index + 2, '(', ')', 1)
    if ':' not in cases:
        raise SeleneSyntaxError(f'missing colon in ternary near line {tokens[index].line}')
    yes, no = cases.split(':', maxsplit=1)
    return _replace(
        tokens,
        start,
        stop,
        f'(function() if {condition} then return {yes} else return {no} end end)()',
    )


def _inline_ternary(tokens: list[_Token], index: int) -> int | None:
    """Translate the unwrapped ternary spelling used by modern Loenn files."""
    colon = index + 1
    depth = 0
    while colon < len(tokens):
        value = tokens[colon].value
        if value in {'(', '[', '{'}:
            depth += 1
        elif value in {')', ']', '}'}:
            if depth == 0:
                break
            depth -= 1
        elif value == ':' and depth == 0:
            break
        colon += 1
    if colon == len(tokens) or tokens[colon].value != ':':
        raise SeleneSyntaxError(f'missing colon in ternary near line {tokens[index].line}')

    stop = colon + 1
    depth = 0
    while stop < len(tokens):
        value = tokens[stop].value
        if value in {'(', '[', '{'}:
            depth += 1
        elif value in {')', ']', '}'}:
            if depth == 0:
                break
            depth -= 1
        elif value == ',' and depth == 0:
            break
        stop += 1
    start = _expression_start(tokens, index)
    condition = _join(tokens[start:index])
    yes = _join(tokens[index + 1 : colon])
    no = _join(tokens[colon + 1 : stop])
    return _replace(
        tokens,
        start,
        stop - 1,
        f'(function() if {condition} then return {yes} else return {no} end end)()',
    )


def _expression_start(tokens: list[_Token], index: int) -> int:
    """Return the first token of the expression immediately before *index*."""
    depth = 0
    stop_tokens = frozenset({'(', '{', ',', '=', 'return', 'then', 'and', 'or'})
    for current in range(index - 1, -1, -1):
        value = tokens[current].value
        if value in {')', ']', '}'}:
            depth += 1
        elif value in {'(', '[', '{'}:
            if depth:
                depth -= 1
            else:
                return current + 1
        elif depth == 0 and value in stop_tokens:
            return current + 1
    return 0


def _foreach(tokens: list[_Token], index: int) -> int | None:
    start = index - 1
    params: list[str] = []
    while start >= 0 and tokens[start].value != 'for':
        params.insert(0, tokens[start].value)
        start -= 1
    if start < 0:
        return None
    stop = index + 1
    values: list[str] = []
    while stop < len(tokens) and tokens[stop].value != 'do':
        values.append(tokens[stop].value)
        stop += 1
    if stop == len(tokens):
        return None
    names = [part.strip() for part in ''.join(params).split(',')]
    if any(_IDENTIFIER.fullmatch(name) is None for name in names):
        return None
    return _replace(
        tokens, start + 1, stop - 1, f'{", ".join(names)} in lpairs({" ".join(values)})'
    )


def _self_call(tokens: list[_Token], index: int) -> int | None:
    if index + 1 >= len(tokens) or _IDENTIFIER.fullmatch(tokens[index + 1].value) is None:
        return None
    following = tokens[index + 2].value if index + 2 < len(tokens) else ''
    previous = tokens[index - 1].value if index else ''
    if following not in {'(', '{'} and not previous.startswith(':'):
        tokens[index + 1].value += '()'
        return index + 1
    return None


def _array_index(tokens: list[_Token], index: int) -> int | None:
    content, stop = _bracket(tokens, index + 1, '[', ']', 1)
    # Selene only changes a bracket expression whose top level contains commas.
    depth = 0
    comma = False
    for token in tokens[index + 1 : stop]:
        if token.value in {'(', '[', '{'}:
            depth += 1
        elif token.value in {')', ']', '}'}:
            depth -= 1
        elif token.value == ',' and depth == 0:
            comma = True
    return _replace(tokens, index, stop, f'[{{{content}}}]') if comma else None


def _get_var(tokens: list[_Token], index: int) -> tuple[str, str, int] | None:
    if index < 0:
        return None
    tail = ''
    if tokens[index].value == ']':
        subscript, start = _bracket(tokens, index - 1, ']', '[', -1)
        base = _get_var(tokens, start - 1)
        if base is None:
            return None
        value, previous_tail, start = base
        return value, previous_tail + f'[{subscript}]', start
    value = tokens[index].value
    if not all(_IDENTIFIER.fullmatch(part) is not None for part in value.split('.')):
        return None
    return value, tail, index


def _assignment(tokens: list[_Token], index: int) -> int | None:
    variable = _get_var(tokens, index - 1)
    if variable is None:
        return None
    name, tail, _ = variable
    operator = tokens[index].value[:-1]
    tokens[index].value = f' = {name}{tail} {operator}'
    return index


def _dollar_assignment(tokens: list[_Token], index: int) -> int | None:
    variable = _get_var(tokens, index - 1)
    if variable is None:
        raise SeleneSyntaxError(f'invalid $$ near line {tokens[index].line}')
    name, tail, _ = variable
    tokens[index].value = f' = _selene._new({name}{tail})'
    return index


def _dollar(tokens: list[_Token], index: int) -> int | None:
    following = tokens[index + 1].value if index + 1 < len(tokens) else ''
    previous = tokens[index - 1].value if index else ''
    if previous in {':', '.'}:
        tokens[index - 1].value = ''
        tokens[index].value = ':unwrap()'
    elif following.startswith(('(', '{', '"', "'", '[')):
        tokens[index].value = '_selene._new'
    elif following.startswith('l'):
        tokens[index].value = '_selene._newList'
        del tokens[index + 1]
    elif following.startswith('a'):
        tokens[index].value = '_selene._newArray'
        del tokens[index + 1]
    elif following.startswith('f'):
        tokens[index].value = '_selene._newFunc'
        del tokens[index + 1]
    elif following.startswith('s'):
        tokens[index].value = '_selene._newString'
        del tokens[index + 1]
    elif following.startswith('o'):
        tokens[index].value = '_selene._newOptional'
        del tokens[index + 1]
    elif following.startswith('i'):
        tokens[index].value = '_selene._newIterable'
        del tokens[index + 1]
    else:
        raise SeleneSyntaxError(f'invalid $ near line {tokens[index].line}')
    return max(0, index - 1)


def _broadcast(tokens: list[_Token], index: int) -> int | None:
    if index + 1 >= len(tokens) or tokens[index + 1].value != '(':
        raise SeleneSyntaxError(
            f'broadcast must precede parentheses near line {tokens[index].line}'
        )
    variable = _get_var(tokens, index - 1)
    if variable is None:
        if index < 2 or tokens[index - 1].value != ')':
            raise SeleneSyntaxError(
                f'broadcast must follow a variable near line {tokens[index].line}'
            )
        value, start = _bracket(tokens, index - 2, ')', '(', -1)
        variable = f'({value})', '', start
    name, tail, start = variable
    tokens[start : index + 2] = [
        _Token(f'_selene._broadcast({name}{tail},', tokens[start].line, True)
    ]
    return start
