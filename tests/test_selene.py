import pytest
from luaparser import ast

from pist.loenn.selene import SeleneSyntaxError, preprocess


@pytest.mark.parametrize(
    'source',
    [
        'value += 1',
        'items[index] ..= value',
        'for key, value <- source do end',
        '(ready) ? (yes : no)',
        'local value = (ready ? yes : no)',
        'local value = (entity.placements[1].data["height"] ? "height" : "width")',
        'local callback = (value -> value + 1)',
        'local callback = (first, second -> first + second)',
    ],
)
def test_preprocess_extensions(source: str) -> None:
    ast.parse(preprocess(source))


def test_preprocess_rewrites_compound_assignment() -> None:
    assert preprocess('value += 1') == 'value=value+1'


def test_preprocess_keeps_strings_and_comments_out_of_transformations() -> None:
    processed = preprocess("local text = 'a += b' -- value += 1\nvalue += 1")
    assert "'a += b'" in processed
    assert '--' not in processed
    ast.parse(processed)


def test_preprocess_rejects_unclosed_string() -> None:
    with pytest.raises(SeleneSyntaxError, match='unclosed quote'):
        preprocess("local text = 'missing")
