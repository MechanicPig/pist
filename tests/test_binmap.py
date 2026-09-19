from struct import pack

import pytest

from pist.game import binmap
from pist.game.binmap import BadMapBin, parse_map_bin, parse_map_meta


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
    lookup = ('Map', 'levels', 'level', 'entities', 'strawberry', 'moon', 'name', 'Map_1')
    indices = {value: index for index, value in enumerate(lookup)}

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
            ('name', 1, b'\xff'),
            ('name', 2, pack('<h', -2)),
            ('name', 3, pack('<i', -3)),
            ('name', 4, pack('<f', 1.5)),
            ('name', 5, pack('<H', indices['Map_1'])),
            ('name', 6, _string('text')),
            ('name', 7, pack('<H', 4) + b'\x03a\x02b'),
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
        'name': 'aaabb',
    }


def test_parse_map_meta_skips_full_map_contents() -> None:
    lookup = ('Map', 'meta', 'levels', 'level', 'Icon', 'name', 'areas/Test/2-medium', 'start')
    indices = {value: index for index, value in enumerate(lookup)}

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
