from struct import pack

import pytest

from berries.game import binmap
from berries.game.binmap import BadMapBin, parse_map_bin, parse_map_meta


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
