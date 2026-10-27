from operator import index
from pathlib import Path

import pytest

from berries.game import collab
from berries.game.binmap import BinElement, BinMap
from berries.game.content import ContentPath
from berries.game.levels import (
    Level,
    LevelSide,
    assemble_mod_levels,
    assemble_vanilla_levels,
    local_sid_for_map_file,
)
from berries.game.localization import Localization
from berries.game.maps import Map, ModMap, VanillaMap
from test_support.mod_factory import make_installed_mod


def _mod_map(path: str) -> Map:
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    return ModMap(ContentPath(path), mod)


def _vanilla_map(path: str) -> Map:
    return VanillaMap(ContentPath(path), Path('Content'))


@pytest.mark.parametrize(
    ('side', 'i', 'name'),
    ((LevelSide.A, 0, 'A'), (LevelSide.B, 1, 'B'), (LevelSide.C, 2, 'C')),
)
def test_side_is_a_sequence_index_with_an_explicit_text_name(
    side: LevelSide, i: int, name: str
) -> None:
    assert index(side) == i
    assert LevelSide(i) is side and LevelSide[name] is side
    assert ('a', 'b', 'c')[side] == name.lower()
    assert not isinstance(side, str)


@pytest.mark.parametrize(
    ('path', 'expected'),
    [
        ('Maps/1-ForsakenCity.bin', 'AREA_1'),
        ('Maps/1H-ForsakenCity.bin', 'AREA_1'),
        ('Maps/1X-ForsakenCity.bin', 'AREA_1'),
        ('Maps/LostLevels.bin', 'AREA_10'),
    ],
)
def test_level_dialog_key_distinguishes_original_content_from_same_named_mod(
    path: str, expected: str
) -> None:
    original = assemble_vanilla_levels((_vanilla_map(path),))[0]
    mod = assemble_mod_levels((_mod_map(path),))[0]
    assert original.dialog_key == expected
    assert mod.dialog_key == mod.maps[0].dialog_key
    assert original.dialog_key != mod.dialog_key


def test_map_info_key_keeps_side_suffix_while_level_key_uses_assembled_base() -> None:
    a_map = _mod_map('Maps/Example/Map.bin')
    b_map = _mod_map('Maps/Example/Map-B.bin')
    assert b_map.dialog_key == 'Example_Map_B'
    assert assemble_mod_levels((a_map, b_map))[0].dialog_key == 'Example_Map'
    assert assemble_mod_levels((b_map,))[0].dialog_key == 'Example_Map_B'


def test_level_names_are_base_names_without_side_formatting() -> None:
    level = Level(
        'Example/Map',
        (
            _mod_map('Maps/Example/Map.bin'),
            _mod_map('Maps/Example/Map-B.bin'),
        ),
    )
    dialogs = {'en': {'Example_Map': 'English'}, 'zh-cn': {'Example_Map': '名称'}}
    resolver = Localization(dialogs, 'zh-cn')
    assert resolver.level_names(level) == {'en': 'English', 'zh-cn': '名称'}
    assert resolver.level_name(level) == '名称'
    assert resolver.level_fallback(level) == 'Example_Map'
    assert Localization({}, 'en').level_name(level) == resolver.level_fallback(level)


def test_level_owns_an_immutable_consecutive_side_tuple() -> None:
    a_map = _mod_map('Maps/Example/Map.bin')
    maps = [a_map]

    level = Level('Example/Map', tuple(maps))
    maps.append(_mod_map('Maps/Example/Map-B.bin'))

    assert level.maps == (a_map,)
    assert level.sides == (LevelSide.A,)
    assert level[LevelSide.A] is a_map
    assert level.map(LevelSide.B) is None
    assert tuple(int(side) for side in LevelSide) == (0, 1, 2)
    with pytest.raises(ValueError, match='between one and three'):
        Level('Example/Map', ())


def test_local_sid_for_map_file_keeps_the_exact_file_stem() -> None:
    b_side_file = ContentPath('Maps/Author/Pack/Map-B.bin')

    assert local_sid_for_map_file(b_side_file) == 'Author/Pack/Map-B'

    with pytest.raises(ValueError, match='Not a map file path'):
        local_sid_for_map_file(ContentPath('Content/Maps/0-Intro.bin'))
    with pytest.raises(ValueError, match='Not a map file path'):
        local_sid_for_map_file(ContentPath('maps/Author/Pack/Map.bin'))


def test_mod_levels_merge_complete_a_b_c_sides() -> None:
    levels = assemble_mod_levels(
        tuple(
            _mod_map(path)
            for path in (
                'Maps/Example/Map.bin',
                'Maps/Example/Map-B.bin',
                'Maps/Example/Map-C.bin',
            )
        )
    )

    assert len(levels) == 1
    assert levels[0].sid == 'Example/Map'
    assert levels[0].dialog_key == 'Example_Map'
    assert levels[0].sides == (LevelSide.A, LevelSide.B, LevelSide.C)


def test_mod_levels_keep_incomplete_side_combinations_independent() -> None:
    levels = assemble_mod_levels(
        tuple(
            _mod_map(path)
            for path in (
                'Maps/Example/Map.bin',
                'Maps/Example/Map-C.bin',
                'Maps/Example/Other-B.bin',
                'Maps/Example/Other-C.bin',
            )
        )
    )

    assert [level.sid for level in levels] == [
        'Example/Map',
        'Example/Map-C',
        'Example/Other-B',
        'Example/Other-C',
    ]
    assert all(level.sides == (LevelSide.A,) for level in levels)


def test_mod_level_assembly_does_not_depend_on_a_side_preceding_b_side() -> None:
    levels = assemble_mod_levels(
        (_mod_map('Maps/Example/Map-B.bin'), _mod_map('Maps/Example/Map.bin'))
    )

    assert len(levels) == 1
    assert levels[0].sides == (LevelSide.A, LevelSide.B)


def test_vanilla_levels_merge_h_and_x_files_by_level() -> None:
    levels = assemble_vanilla_levels(
        tuple(
            _vanilla_map(path)
            for path in (
                'Maps/1-ForsakenCity.bin',
                'Maps/1H-ForsakenCity.bin',
                'Maps/1X-ForsakenCity.bin',
            )
        )
    )

    assert len(levels) == 1
    assert levels[0].sid == 'Celeste/1-ForsakenCity'
    assert levels[0].dialog_key == 'AREA_1'
    assert levels[0].sides == (LevelSide.A, LevelSide.B, LevelSide.C)


def test_vanilla_levels_keep_a_c_combination_independent() -> None:
    levels = assemble_vanilla_levels(
        (
            _vanilla_map('Maps/1-ForsakenCity.bin'),
            _vanilla_map('Maps/1X-ForsakenCity.bin'),
        )
    )

    assert [level.sid for level in levels] == [
        'Celeste/1-ForsakenCity',
        'Celeste/1X-ForsakenCity',
    ]
    assert all(level.sides == (LevelSide.A,) for level in levels)


def test_collab_lobby_identity_excludes_only_the_prologue() -> None:
    level = Level(
        'Example/0-Lobbies/1-Beginner',
        (_mod_map('Maps/Example/0-Lobbies/1-Beginner.bin'),),
    )

    assert collab.is_lobby_sid(level.sid, ('Example',))
    assert not collab.is_lobby_sid('Example/0-Lobbies/0-Prologue', ('Example',))


def test_journal_refs_preserve_source_order_and_report_invalid_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'Maps/Example/0-Lobbies/1-Beginner.bin'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'map')
    mod = make_installed_mod(
        source='directory',
        filename='Example',
        path=tmp_path,
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    loaded_map = ModMap(ContentPath('Maps/Example/0-Lobbies/1-Beginner.bin'), mod)
    triggers = (
        *(
            BinElement('CollabUtils2/JournalTrigger', {'levelset': value}, ())
            for value in (
                'Example/1-Beginner',
                '',
                'Example/1-Beginner',
                'Example/2-Advanced',
            )
        ),
        BinElement('CollabUtils2/JournalTrigger', {}, ()),
    )
    monkeypatch.setattr(
        'berries.game.map_data.parse_map_bin',
        lambda *_args, **_kwargs: BinMap('Example', BinElement('Map', {}, triggers)),
    )

    assert collab.JournalRefs.from_map(loaded_map) == collab.JournalRefs(
        ('Example/1-Beginner', 'Example/2-Advanced'),
        2,
    )


def test_journal_refs_normalize_invalid_content_source(tmp_path: Path) -> None:
    archive = tmp_path / 'Example.zip'
    archive.write_bytes(b'not a zip')
    mod = make_installed_mod(
        source='zip',
        filename=archive.name,
        path=archive,
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    loaded_map = ModMap(ContentPath('Maps/Example/0-Lobbies/1-Beginner.bin'), mod)

    with pytest.raises(ValueError, match='Invalid content source for map'):
        collab.JournalRefs.from_map(loaded_map)
