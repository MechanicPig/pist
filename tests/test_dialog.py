from dataclasses import dataclass

import pytest

from pist.game.content import ContentPath
from pist.game.dialog import (
    campaign_dir_for_map_file,
    default_campaign_name,
    default_map_name,
    dialog_key_for_campaign_dir,
    dialog_key_for_map_file,
    display_name,
    parse_dialog,
    split_map_side_suffix,
)


@dataclass(frozen=True)
class NamedValue:
    names: dict[str, str]

    @property
    def fallback_name(self) -> str:
        return 'Fallback'


def test_display_name_prefers_configured_languages_then_falls_back() -> None:
    assert display_name(NamedValue(names={'en': 'English'}), ('zh-cn', 'en')) == 'English'
    assert display_name(NamedValue(names={}), ('zh-cn', 'en')) == 'Fallback'


def test_dialog_parser_handles_comments_continuations_and_insertions() -> None:
    parsed = parse_dialog(
        '# ignored\nBASE=First\nSecond\nTITLE={+BASE} {#f00}Name{n}\n[portrait]Shown\n'
    )
    assert parsed['BASE'] == 'First\nSecond'
    assert parsed['base'] == 'First\nSecond'
    assert parsed['TITLE'] == 'First\nSecond Name\nShown'


def test_map_path_becomes_dialog_key() -> None:
    assert dialog_key_for_map_file(ContentPath('Maps/Author/Pack/Map.bin')) == 'Author_Pack_Map'
    assert (
        dialog_key_for_map_file(ContentPath('Maps/Author/Pack Name/Map-Name.bin'))
        == 'Author_Pack_Name_Map_Name'
    )
    assert split_map_side_suffix(ContentPath('Maps/Author/Pack/Map-B.bin')) == (
        ContentPath('Maps/Author/Pack/Map.bin'),
        'B',
    )
    assert split_map_side_suffix(ContentPath('Maps/Author/Pack/Map.bin')) == (
        ContentPath('Maps/Author/Pack/Map.bin'),
        None,
    )
    assert default_map_name(ContentPath('Maps/Author/Pack Name/Map.bin')) == 'Author_Pack_Name_Map'
    assert (
        default_map_name(ContentPath('Maps/Ferret/MicroMountain/map.bin'))
        == 'Ferret_Micro Mountain_map'
    )
    assert default_map_name(ContentPath('Maps/Ezel/7CC.bin')) == 'Ezel_7CC'
    assert default_map_name(ContentPath('Maps/RootMap.bin')) == 'Root Map'
    assert campaign_dir_for_map_file(ContentPath('Maps/Author/Pack/Map.bin')) == ContentPath(
        'Maps/Author/Pack'
    )
    assert dialog_key_for_campaign_dir(ContentPath('Maps/Author/Pack Name')) == 'Author_Pack_Name'


def test_dialog_key_uses_everests_exact_key_conversion() -> None:
    assert (
        dialog_key_for_map_file(ContentPath('Maps/Author/Pack.Name/Map+Name.bin'))
        == 'Author_Pack.Name_Map_Name'
    )
    assert dialog_key_for_campaign_dir(ContentPath('Maps/Author/Pack.Name')) == 'Author_Pack.Name'


@pytest.mark.parametrize(
    'map_file',
    ('maps/Author/Pack/Map.bin', 'Maps/Author/Pack/Map.BIN'),
)
def test_dialog_map_path_requires_everests_exact_virtual_path_spelling(map_file: str) -> None:
    with pytest.raises(ValueError, match='Not a map file path'):
        dialog_key_for_map_file(ContentPath(map_file))


def test_dialog_map_side_suffix_is_case_sensitive() -> None:
    assert split_map_side_suffix(ContentPath('Maps/Author/Pack/Map-b.bin')) == (
        ContentPath('Maps/Author/Pack/Map-b.bin'),
        None,
    )


def test_campaign_dir_rejects_invalid_map_file() -> None:
    with pytest.raises(ValueError, match='Not a map file path'):
        campaign_dir_for_map_file(ContentPath('Content/Maps/Author/Pack/Map.bin'))
    with pytest.raises(ValueError, match='Not a campaign directory'):
        default_campaign_name(ContentPath('Content/Maps/Author/Pack'))
