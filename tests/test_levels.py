from pathlib import Path

import pytest
from berries.game import collab
from berries.game.binmap import BinElement, BinMap
from berries.game.content import GameContent
from berries.game.levels import (
    Level,
    LevelSide,
    Map,
    assemble_mod_levels,
    assemble_vanilla_levels,
)
from berries.game.maps import MapInfo

from tests.mod_factory import make_installed_mod


def _mod_map(path: str) -> Map:
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    return Map(MapInfo(file_path=path), mod)


def _vanilla_map(path: str) -> Map:
    return Map(MapInfo(file_path=path), GameContent(Path('Content')))


def test_level_owns_an_immutable_consecutive_side_tuple() -> None:
    a_map = _mod_map('Maps/Example/Map.bin')
    maps = [a_map]

    level = Level('Example/Map', 'Example_Map', tuple(maps))
    maps.append(_mod_map('Maps/Example/Map-B.bin'))

    assert level.maps == (a_map,)
    assert level.sides == (LevelSide.A,)
    assert level[LevelSide.A] is a_map
    assert level.map(LevelSide.B) is None
    assert tuple(side.position for side in LevelSide) == (0, 1, 2)
    with pytest.raises(ValueError, match='between one and three'):
        Level('Example/Map', 'Example_Map', ())


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
        'Example_0_Lobbies_1_Beginner',
        (_mod_map('Maps/Example/0-Lobbies/1-Beginner.bin'),),
    )

    assert collab.is_lobby_sid(level.sid, ('Example',))
    assert not collab.is_lobby_sid('Example/0-Lobbies/0-Prologue', ('Example',))


def test_journal_references_preserve_source_order_and_report_invalid_triggers(
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
    loaded_map = Map(MapInfo(file_path='Maps/Example/0-Lobbies/1-Beginner.bin'), mod)
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
        'berries.game.collab.binmap.parse_map_bin',
        lambda *_args, **_kwargs: BinMap('Example', BinElement('Map', {}, triggers)),
    )

    assert collab.journal_references(loaded_map) == collab.JournalReferences(
        ('Example/1-Beginner', 'Example/2-Advanced'),
        2,
    )
    assert collab.journal_references(loaded_map).diagnostics == (
        '发现 2 个没有有效 levelset 的日志入口。',
    )


def test_journal_references_normalize_invalid_content_source(tmp_path: Path) -> None:
    archive = tmp_path / 'Example.zip'
    archive.write_bytes(b'not a zip')
    mod = make_installed_mod(
        source='zip',
        filename=archive.name,
        path=archive,
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    loaded_map = Map(MapInfo(file_path='Maps/Example/0-Lobbies/1-Beginner.bin'), mod)

    with pytest.raises(ValueError, match='Invalid content source for lobby map'):
        collab.journal_references(loaded_map)
