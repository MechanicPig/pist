import pytest

from pist.game.dialog import (
    campaign_dir_for_map_file,
    default_campaign_name,
    default_map_name,
    dialog_key_for_campaign_dir,
    dialog_key_for_map_file,
    map_base_file_and_side,
    parse_dialog,
)


def test_dialog_parser_handles_comments_continuations_and_insertions() -> None:
    parsed = parse_dialog(
        '# ignored\nBASE=First\nSecond\nTITLE={+BASE} {#f00}Name{n}\n[portrait]Shown\n'
    )
    assert parsed['BASE'] == 'First\nSecond'
    assert parsed['base'] == 'First\nSecond'
    assert parsed['TITLE'] == 'First\nSecond Name\nShown'


def test_map_path_becomes_dialog_key() -> None:
    assert dialog_key_for_map_file('Maps/Author/Pack/Map.bin') == 'Author_Pack_Map'
    assert (
        dialog_key_for_map_file('Maps/Author/Pack Name/Map-Name.bin') == 'Author_Pack_Name_Map_Name'
    )
    assert map_base_file_and_side('Maps/Author/Pack/Map-B.bin') == (
        'Maps/Author/Pack/Map.bin',
        'B',
    )
    assert map_base_file_and_side('Maps/Author/Pack/Map.bin') == (
        'Maps/Author/Pack/Map.bin',
        None,
    )
    assert default_map_name('Maps/Author/Pack Name/Map.bin') == 'Author_Pack_Name_Map'
    assert default_map_name('Maps/Ferret/MicroMountain/map.bin') == 'Ferret_Micro Mountain_map'
    assert default_map_name('Maps/Ezel/7CC.bin') == 'Ezel_7CC'
    assert default_map_name('Maps/RootMap.bin') == 'Root Map'
    assert campaign_dir_for_map_file('Maps/Author/Pack/Map.bin') == 'Maps/Author/Pack'
    assert dialog_key_for_campaign_dir('Maps/Author/Pack Name') == 'Author_Pack_Name'


def test_campaign_dir_rejects_invalid_map_file() -> None:
    with pytest.raises(ValueError, match='Not a map file path'):
        campaign_dir_for_map_file('Content/Maps/Author/Pack/Map.bin')
    with pytest.raises(ValueError, match='Not a campaign directory'):
        default_campaign_name('Content/Maps/Author/Pack')
