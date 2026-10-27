"""CollabUtils2 journal icon reading, warnings and map ordering."""

from pathlib import Path
from struct import pack

import pytest

from berries.game.binmap import MapStringDecodeWarning
from berries.game.collab import (
    collab_journal_map_order_from_icons,
    iter_collab_journal_map_icons,
)
from berries.game.content import ContentPath
from test_support.mod_factory import make_installed_mod


def test_journal_icon_metadata_reports_decode_warning_with_source(tmp_path: Path) -> None:
    path = tmp_path / 'Example'
    maps_dir = path / 'Maps'
    maps_dir.mkdir(parents=True)
    (maps_dir / 'Test.bin').write_bytes(
        b'\x0bCELESTE MAP\x01\xff' + pack('<H', 1) + b'\x03Map' + pack('<HBH', 0, 0, 0)
    )
    info = ContentPath('Maps/Test.bin')
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=path,
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[info],
    )
    diagnostics: list[tuple[ContentPath, MapStringDecodeWarning]] = []

    def report(map_info: ContentPath, warning: MapStringDecodeWarning) -> None:
        diagnostics.append((map_info, warning))

    assert list(iter_collab_journal_map_icons(mod, (info,), on_warning=report)) == [(info, None)]
    assert len(diagnostics) == 1
    assert diagnostics[0][0] is info
    assert diagnostics[0][1].offset == 13 and diagnostics[0][1].raw == b'\xff'
    with pytest.warns(MapStringDecodeWarning):
        list(iter_collab_journal_map_icons(mod, (info,)))


def write_map_with_icon(path: Path, icon: str) -> None:
    """Write the smallest binary map that carries a top-level ``meta.Icon``."""
    lookup = ('Map', 'meta', 'Icon', icon)
    indices = {value: i for i, value in enumerate(lookup)}

    def element(name: str, attrs: list[bytes], children: list[bytes]) -> bytes:
        return b''.join(
            (
                pack('<H', indices[name]),
                pack('<B', len(attrs)),
                *attrs,
                pack('<H', len(children)),
                *children,
            )
        )

    meta = element('meta', [pack('<H', indices['Icon']) + b'\x05' + pack('<H', indices[icon])], [])
    root = element('Map', [], [meta])
    data = b'\x0bCELESTE MAP\x0bExample/Map' + pack('<H', len(lookup))
    data += b''.join(bytes((len(value),)) + value.encode() for value in lookup) + root
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_collab_journal_map_order_uses_numbered_map_icons(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Collab'
    maps = [
        ContentPath('Maps/Collab/Hard.bin'),
        ContentPath('Maps/Collab/Medium.bin'),
        ContentPath('Maps/Collab/ZZ-HeartSide.bin'),
    ]
    for map_info, icon in zip(
        maps,
        (
            'areas/Collab/meters/3-hard',
            'areas/Collab/meters/2-medium',
            'areas/Collab/meters/1-easy',
        ),
        strict=True,
    ):
        write_map_with_icon(mod_dir / map_info, icon)
    mod = make_installed_mod(
        source='directory',
        filename='Collab',
        path=str(mod_dir),
        metadata_name='Collab',
        metadata_version=None,
    )

    icons = dict(iter_collab_journal_map_icons(mod, maps))
    assert collab_journal_map_order_from_icons(maps, icons) == [maps[1], maps[0], maps[2]]


def test_collab_journal_order_requires_exact_heartside_name() -> None:
    heartside_named_in_lowercase = ContentPath('Maps/Collab/zz-heartside.bin')
    ordinary_map = ContentPath('Maps/Collab/Map.bin')

    ordered = collab_journal_map_order_from_icons(
        (heartside_named_in_lowercase, ordinary_map),
        {
            heartside_named_in_lowercase: 'Maps/Collab/1-HeartSide',
            ordinary_map: 'Maps/Collab/2-Map',
        },
    )

    assert ordered == [heartside_named_in_lowercase, ordinary_map]
