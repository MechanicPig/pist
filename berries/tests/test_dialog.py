from dataclasses import dataclass
from pathlib import Path

import pytest

from berries.containers import CaseFoldDict
from berries.game import dialog, vanilla
from berries.game.content import ContentPath, DirContentEntry
from berries.game.dialog import (
    default_campaign_name,
    default_map_name,
    dialog_key_for_campaign_dir,
    dialog_key_for_map_file,
    display_name,
    parse_dialog,
)
from berries.game.maps import campaign_dir_for_map_file, split_map_side_suffix


@dataclass(frozen=True)
class NamedValue:
    names: dict[str, str]

    @property
    def fallback_name(self) -> str:
        return 'Fallback'


def test_display_name_prefers_configured_languages_then_falls_back() -> None:
    assert display_name(NamedValue(names={'en': 'English'}), ('zh-cn', 'en')) == 'English'
    assert display_name(NamedValue(names={}), ('zh-cn', 'en')) == 'Fallback'


def test_merge_dialogs_preserves_sources_and_applies_case_insensitive_overrides() -> None:
    base = {'en': {'TITLE': 'Base', 'base_only': 'Kept'}}
    first = {'en': {'title': 'First'}, 'ja': {'TITLE': 'Japanese'}}
    last = {'en': {'Title': 'Last'}}
    merged = dialog.merge_dialogs(iter((first, last)), base_dialogs=base)
    assert merged['en']['TITLE'] == 'Last'
    assert merged['en']['base_only'] == 'Kept'
    assert merged['ja']['title'] == 'Japanese'
    merged['en']['base_only'] = 'Changed'
    assert base['en']['base_only'] == 'Kept'
    assert first['en']['title'] == 'First'
    assert last['en']['Title'] == 'Last'
    assert dialog.merge_dialogs(()) == {}


def test_read_dialogs_merges_selected_files_without_cross_file_insertion(tmp_path: Path) -> None:
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    ignored = tmp_path / 'ignored'
    first.mkdir()
    second.mkdir()
    ignored.mkdir()
    (first / 'English.txt').write_text('BASE=First\nTITLE={+BASE}\n', encoding='utf-8')
    (second / 'english.txt').write_text('BASE=Second\nOTHER={+TITLE}\n', encoding='utf-8')
    (ignored / 'English.TXT').write_text('BASE=Ignored\n', encoding='utf-8')
    (second / 'Unknown.txt').write_text('BASE=Ignored\n', encoding='utf-8')
    with DirContentEntry(tmp_path) as root:
        result = dialog.read_dialogs(
            root / name
            for name in (
                'first/English.txt',
                'second/english.txt',
                'ignored/English.TXT',
                'second/Unknown.txt',
                'missing.txt',
                'first',
            )
        )
    assert dict(result['en']) == {'base': 'Second', 'title': 'First', 'other': '[XXX]'}


def test_read_dialogs_leaves_decoding_recovery_to_caller(tmp_path: Path) -> None:
    (tmp_path / 'English.txt').write_bytes(bytes((255,)))
    with DirContentEntry(tmp_path) as root:
        with pytest.raises(UnicodeDecodeError):
            dialog.read_dialogs((root / 'English.txt',))
        assert dialog.read_dialogs((root / 'English.txt',), read_text=lambda _: None) == {}


def test_vanilla_dialogs_ignore_unknown_files_and_propagate_decoding_errors(tmp_path: Path) -> None:
    assert vanilla.load_dialogs(tmp_path) == {}
    directory = tmp_path / 'Content' / 'Dialog'
    directory.mkdir(parents=True)
    (directory / 'Unknown.txt').write_text('TITLE=Ignored\n', encoding='utf-8')
    (directory / 'English.txt').write_bytes(bytes((255,)))
    with pytest.raises(UnicodeDecodeError):
        vanilla.load_dialogs(tmp_path)


def test_campaign_name_queries_prefer_levelset_and_handle_missing_collab() -> None:
    entries = {
        'en': CaseFoldDict(
            {
                'Pack': 'Unprefixed',
                'LEVELSET_PACK': 'Prefixed',
                'levelset_Author_Collab_0_Lobbies': 'Collab',
            }
        )
    }
    assert dialog.localize_campaign_key('Pack', dialogs=entries) == {'en': 'Prefixed'}
    assert dialog.localize_campaign_key('Missing', dialogs=entries) == {}
    assert dialog.localize_collab_names('Author/Collab', dialogs=entries) == {'en': 'Collab'}
    assert dialog.localize_collab_names(None, dialogs=entries) == {}


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
    assert dialog_key_for_campaign_dir(ContentPath('Maps')) == ''
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
