from struct import pack

import pytest

from berries.game import binmap
from berries.game.binmap import BadMapBin, parse_map_bin, parse_map_meta


@pytest.fixture(autouse=True)
def isolated_depth_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep parser tests independent of the caller's depth configuration."""
    monkeypatch.delenv('BERRIES_MAX_MAP_DEPTH', raising=False)


def test_element_child_matches_only_first_exact_direct_child() -> None:
    nested = binmap.BinElement('target', {}, ())
    first = binmap.BinElement('target', {'id': 1}, ())
    last = binmap.BinElement('target', {'id': 2}, ())
    root = binmap.BinElement('root', {}, (binmap.BinElement('branch', {}, (nested,)), first, last))
    assert root.child('target') is first
    assert root.child('TARGET') is None
    assert root.child('missing') is None
    branch = root.child('branch')
    assert branch is not None
    assert branch.child('target') is nested


def test_binmap_iter_rooms_preserves_order_and_ignores_other_nodes() -> None:
    first = binmap.BinElement('level', {'name': 'first'}, ())
    last = binmap.BinElement('level', {'name': 'last'}, ())
    nested = binmap.BinElement('branch', {}, (binmap.BinElement('level', {}, ()),))
    rooms = binmap.BinElement('levels', {}, (first, nested, last))
    ignored = binmap.BinElement('levels', {}, (binmap.BinElement('level', {}, ()),))
    root = binmap.BinElement('Map', {}, (binmap.BinElement('level', {}, ()), rooms, ignored))
    assert tuple(binmap.BinMap('Example', root).iter_rooms()) == (first, last)
    assert tuple(binmap.BinMap('Example', binmap.BinElement('Map', {}, ())).iter_rooms()) == ()
    assert (
        tuple(
            binmap.BinMap(
                'Example', binmap.BinElement('Map', {}, (binmap.BinElement('levels', {}, ()),))
            ).iter_rooms()
        )
        == ()
    )


def _varlen(value: int) -> bytes:
    parts = bytearray()
    while value > 0x7F:
        parts.append(value % 0x80 + 0x80)
        value //= 0x80
    parts.append(value)
    return bytes(parts)


def _string(value: str) -> bytes:
    encoded = value.encode()
    return _varlen(len(encoded)) + encoded


def _nested_map_data(depth: int, *, meta_first: bool) -> bytes:
    """Build a chain reaching the given zero-based depth beside root metadata."""
    lookup = ('Map', 'node', 'meta', 'Icon', 'test-icon')
    header = _string('CELESTE MAP') + _string('Test') + pack('<H', len(lookup))
    header += b''.join(map(_string, lookup))
    chain = pack('<HBH', 1, 0, 1) * (depth - 1) + pack('<HBH', 1, 0, 0)
    meta = pack('<HBHBHH', 2, 1, 3, 5, 4, 0)
    children = meta + chain if meta_first else chain + meta
    return header + pack('<HBH', 0, 0, 2) + children


@pytest.mark.parametrize('depth', (400, 600, 999, 1000, 1001))
@pytest.mark.parametrize('meta_first', (False, True))
def test_nested_map_parsing_obeys_declared_depth_limit(depth: int, meta_first: bool) -> None:
    data = _nested_map_data(depth, meta_first=meta_first)
    if depth >= 1000:
        with pytest.raises(BadMapBin, match='Map element nesting exceeds'):
            parse_map_bin(data, max_depth=1000)
        with pytest.raises(BadMapBin, match='Map element nesting exceeds'):
            parse_map_meta(data, max_depth=1000)
    else:
        root = parse_map_bin(data, max_depth=1000).root
        elements = list(root.walk())
        assert len(elements) == depth + 2
        assert elements[0].name == 'Map'
        assert root.children[0].name == ('meta' if meta_first else 'node')
        assert [element.name for element in elements].count('node') == depth
        assert parse_map_meta(data, max_depth=1000) == {'Icon': 'test-icon'}


def test_metadata_early_return_does_not_validate_unread_payload() -> None:
    data = _nested_map_data(1001, meta_first=True)
    assert parse_map_meta(data, allow_trailing=True) == {'Icon': 'test-icon'}
    with pytest.raises(BadMapBin, match='Map element nesting exceeds'):
        parse_map_meta(_nested_map_data(1001, meta_first=False), allow_trailing=True)


@pytest.mark.parametrize('metadata_only', (False, True))
def test_default_depth_limit_and_explicit_override(metadata_only: bool) -> None:
    parser = parse_map_meta if metadata_only else parse_map_bin
    assert parser(_nested_map_data(binmap.DEFAULT_MAX_DEPTH - 1, meta_first=False))
    data = _nested_map_data(binmap.DEFAULT_MAX_DEPTH, meta_first=False)
    with pytest.raises(BadMapBin, match='Map element nesting exceeds'):
        parser(data)
    assert parser(data, max_depth=binmap.DEFAULT_MAX_DEPTH + 1)


@pytest.mark.parametrize('max_depth', (0, -1, True))
def test_depth_limit_must_be_a_positive_integer(max_depth: int) -> None:
    data = _nested_map_data(1, meta_first=False)
    with pytest.raises(ValueError, match='positive integer'):
        parse_map_bin(data, max_depth=max_depth)
    with pytest.raises(ValueError, match='positive integer'):
        parse_map_meta(data, max_depth=max_depth)


@pytest.mark.parametrize('metadata_only', (False, True))
def test_environment_depth_limit_and_explicit_precedence(
    monkeypatch: pytest.MonkeyPatch, metadata_only: bool
) -> None:
    parser = parse_map_meta if metadata_only else parse_map_bin
    monkeypatch.setenv('BERRIES_MAX_MAP_DEPTH', '2')
    data = _nested_map_data(2, meta_first=False)
    with pytest.raises(BadMapBin, match='Map element nesting exceeds 2 levels'):
        parser(data)
    assert parser(data, max_depth=3)
    monkeypatch.setenv('BERRIES_MAX_MAP_DEPTH', '3')
    assert parser(data)


@pytest.mark.parametrize('value', ('invalid', '0', '-1', '2.5'))
def test_invalid_environment_limit_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv('BERRIES_MAX_MAP_DEPTH', value)
    data = _nested_map_data(1, meta_first=False)
    for parser in (parse_map_bin, parse_map_meta):
        with pytest.raises(ValueError, match='positive integer') as caught:
            parser(data)
        assert not isinstance(caught.value, BadMapBin)
        assert parser(data, max_depth=2)


@pytest.mark.parametrize('metadata_only', (False, True))
def test_deep_truncated_map_has_diagnostic_error(metadata_only: bool) -> None:
    parser = parse_map_meta if metadata_only else parse_map_bin
    with pytest.raises(BadMapBin, match='Unexpected end'):
        parser(_nested_map_data(600, meta_first=False)[:-1], max_depth=1000)


@pytest.mark.parametrize('metadata_only', (False, True))
def test_element_count_limit_includes_skipped_descendants(
    monkeypatch: pytest.MonkeyPatch, metadata_only: bool
) -> None:
    monkeypatch.setattr(binmap, 'MAX_ELEMENT_COUNT', 4)
    parser = parse_map_meta if metadata_only else parse_map_bin
    with pytest.raises(BadMapBin, match='Map element count exceeds'):
        parser(_nested_map_data(3, meta_first=False))


def test_walk_preserves_depth_first_sibling_order() -> None:
    leaf = binmap.BinElement('leaf', {}, ())
    first = binmap.BinElement('first', {}, (leaf,))
    second = binmap.BinElement('second', {}, ())
    root = binmap.BinElement('root', {}, (first, second))
    assert [element.name for element in root.walk()] == ['root', 'first', 'leaf', 'second']


def test_parse_map_bin_decodes_nested_elements_and_all_value_types() -> None:
    lookup = (
        'Map',
        'levels',
        'level',
        'strawberry',
        'moon',
        'byte',
        'short',
        'int',
        'float',
        'lookup',
        'text',
        'rle',
        'Map_1',
    )
    indices = {value: i for i, value in enumerate(lookup)}

    def element(
        name: str,
        attrs: list[tuple[str, int, bytes]],
        children: list[bytes],
    ) -> bytes:
        return b''.join(
            (
                pack('<H', indices[name]),
                pack('<B', len(attrs)),
                *(
                    pack('<H', indices[key]) + pack('<B', type_id) + value
                    for key, type_id, value in attrs
                ),
                pack('<H', len(children)),
                *children,
            )
        )

    strawberry = element(
        'strawberry',
        [
            ('moon', 0, b'\x01'),
            ('byte', 1, b'\xff'),
            ('short', 2, pack('<h', -2)),
            ('int', 3, pack('<i', -3)),
            ('float', 4, pack('<f', 1.5)),
            ('lookup', 5, pack('<H', indices['Map_1'])),
            ('text', 6, _string('text')),
            ('rle', 7, pack('<H', 4) + b'\x03a\x02b'),
        ],
        [],
    )
    root = element('Map', [], [element('levels', [], [element('level', [], [strawberry])])])
    data = b''.join((_string('CELESTE MAP'), _string('Example/Map'), pack('<H', len(lookup))))
    data += b''.join(map(_string, lookup)) + root

    map_data = parse_map_bin(data)
    elements = list(map_data.root.walk())

    assert map_data.package == 'Example/Map'
    assert [element.name for element in elements] == ['Map', 'levels', 'level', 'strawberry']
    assert elements[-1].attrs == {
        'moon': True,
        'byte': 255,
        'short': -2,
        'int': -3,
        'float': 1.5,
        'lookup': 'Map_1',
        'text': 'text',
        'rle': 'aaabb',
    }
    assert type(elements[-1].attrs['moon']) is bool
    for key in ('byte', 'short', 'int'):
        assert type(elements[-1].attrs[key]) is int
    assert type(elements[-1].attrs['float']) is float


def test_parse_map_meta_skips_full_map_contents() -> None:
    lookup = ('Map', 'meta', 'levels', 'level', 'Icon', 'name', 'areas/Test/2-medium', 'start')
    indices = {value: i for i, value in enumerate(lookup)}

    def element(name: str, attrs: list[tuple[str, int, bytes]], children: list[bytes]) -> bytes:
        return b''.join(
            (
                pack('<H', indices[name]),
                pack('<B', len(attrs)),
                *(
                    pack('<H', indices[key]) + pack('<B', type_id) + value
                    for key, type_id, value in attrs
                ),
                pack('<H', len(children)),
                *children,
            )
        )

    meta = element('meta', [('Icon', 5, pack('<H', indices['areas/Test/2-medium']))], [])
    level = element('level', [('name', 5, pack('<H', indices['start']))], [])
    root = element('Map', [], [meta, element('levels', [], [level])])
    data = b''.join((_string('CELESTE MAP'), _string('Example/Map'), pack('<H', len(lookup))))
    data += b''.join(map(_string, lookup)) + root

    assert parse_map_meta(data) == {'Icon': 'areas/Test/2-medium'}


@pytest.mark.parametrize(
    ('data', 'message'),
    [
        (b'', 'Unexpected end'),
        (_string('WRONG'), 'Invalid map header'),
    ],
)
def test_parse_map_bin_rejects_invalid_data(data: bytes, message: str) -> None:
    with pytest.raises(BadMapBin, match=message):
        parse_map_bin(data)


def test_parse_map_bin_can_explicitly_allow_trailing_payload_data() -> None:
    lookup = ('Map',)
    data = b''.join((_string('CELESTE MAP'), _string('Example/Map'), pack('<H', len(lookup))))
    data += _string('Map') + pack('<H', 0) + pack('<B', 0) + pack('<H', 0) + b'extra'

    with pytest.raises(BadMapBin, match='Unexpected trailing data'):
        parse_map_bin(data)
    assert parse_map_bin(data, allow_trailing=True).package == 'Example/Map'


def test_parse_map_bin_limits_total_decoded_rle_text(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = ('Map', 'text')
    root = b''.join(
        (
            pack('<H', 0),
            pack('<B', 1),
            pack('<H', 1),
            b'\x07',
            pack('<H', 2),
            b'\x0aA',
            pack('<H', 0),
        )
    )
    data = b''.join((_string('CELESTE MAP'), _string('Example/Map'), pack('<H', len(lookup))))
    data += b''.join(map(_string, lookup)) + root
    monkeypatch.setattr(binmap, 'MAX_DECODED_TEXT_SIZE', 30)

    with pytest.raises(BadMapBin, match='Decoded text exceeds'):
        parse_map_bin(data)
