from pathlib import Path
from zipfile import ZipFile

from pist.game.mod_path import ModPath
from pist.loenn import LoennMod, load_loenn_registry


def test_registry_reads_localized_entity_placement_from_zip_mod(tmp_path: Path) -> None:
    mod_path = tmp_path / 'JungleHelper.zip'
    with ZipFile(mod_path, 'w') as archive:
        archive.writestr(
            'Loenn/entities/grablessGoldenBerry.lua',
            """local strawberry = {}
strawberry.name = "JungleHelper/TreeDepthController"
strawberry.placements = {
    { name = "golden", data = { alwaysSpawn = false, order = -1 } },
}
return strawberry
""",
        )
        archive.writestr(
            'Loenn/lang/en_gb.lang',
            'entities.JungleHelper/TreeDepthController.placements.name.golden='
            'Golden Strawberry (Grabless)\n',
        )

    with ModPath(mod_path) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Jungle Helper')])

    placements = registry.placements_for('JungleHelper/TreeDepthController')
    assert len(placements) == 1
    assert placements[0].name == 'golden'
    assert placements[0].attrs == {'alwaysSpawn': False, 'order': -1}
    assert placements[0].display_name == 'Golden Strawberry (Grabless)'
    assert placements[0].source == 'JungleHelper.zip/Loenn/entities/grablessGoldenBerry.lua'
    assert placements[0].mod_name == 'Jungle Helper'
    assert registry.display_names_for('JungleHelper/TreeDepthController') == (
        'Golden Strawberry (Grabless)',
    )


def test_registry_uses_placement_name_when_localization_is_missing(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'test.lua').write_text(
        """local entity = {}
entity.name = 'TestHelper/Entity'
entity.placements = {{name = 'test'}}
return entity
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.display_names_for('TestHelper/Entity') == ('test',)


def test_registry_reads_single_and_returned_static_placements(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'single.lua').write_text(
        """local entity = {
    name = 'TestHelper/Single',
    placements = {name = 'single', data = {moon = true}},
}
return entity
""",
        encoding='utf-8',
    )
    (entity_dir / 'returned.lua').write_text(
        """return {
    name = 'TestHelper/Returned',
    placements = {{name = 'returned', data = {winged = false}}},
}
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.placements_for('TestHelper/Single')[0].attrs == {'moon': True}
    assert registry.placements_for('TestHelper/Returned')[0].attrs == {'winged': False}


def test_registry_handles_selene_compound_assignment(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'entity.lua').write_text(
        """local entity = {}
entity.name = 'TestHelper/Entity'
entity.placements = {{name = 'normal', data = {moon = true}}}
entity.count += 1
return entity
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.placements_for('TestHelper/Entity')[0].attrs == {'moon': True}


def test_registry_resolves_static_data_variable_and_keeps_partial_data(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'entity.lua').write_text(
        """local attrs = {moon = true, skin = getCurrentSkin()}
return {
    name = 'TestHelper/Entity',
    placements = {{name = 'normal', data = attrs}},
}
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.placements_for('TestHelper/Entity')[0].attrs == {'moon': True}


def test_registry_reads_static_factory_placements(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'entity.lua').write_text(
        """local function create_handler(name)
    local entity = {}
    entity.name = name
    entity.placements = {name = 'normal', data = {order = -1}}
    return entity
end
return {create_handler('TestHelper/First'), create_handler('TestHelper/Second')}
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.display_names_for('TestHelper/First') == ('normal',)
    assert registry.display_names_for('TestHelper/Second') == ('normal',)


def test_registry_expands_static_loop_placements(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'entity.lua').write_text(
        """local entity = {name = 'TestHelper/Cassette'}
entity.placements = {}
for index = 0, 3 do
    table.insert(entity.placements, {
        name = string.format('cassette_%s', index),
        data = {index = index},
    })
end
return entity
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    placements = registry.placements_for('TestHelper/Cassette')
    assert tuple(placement.name for placement in placements) == (
        'cassette_0',
        'cassette_1',
        'cassette_2',
        'cassette_3',
    )
    assert tuple(placement.attrs['index'] for placement in placements) == (0, 1, 2, 3)


def test_registry_formats_static_associated_mods(tmp_path: Path) -> None:
    helping_hand_dir = tmp_path / 'MaxHelpingHand'
    entity_dir = helping_hand_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'gate.lua').write_text(
        """local gate = {
    name = 'MaxHelpingHand/SaveFileStrawberryGate',
    associatedMods = {'MaxHelpingHand', 'LunaticHelper'},
    placements = {name = 'door'},
}
return gate
""",
        encoding='utf-8',
    )
    lang_dir = helping_hand_dir / 'Loenn' / 'lang'
    lang_dir.mkdir()
    (lang_dir / 'en_gb.lang').write_text(
        "mods.MaxHelpingHand.name=Maddie's Helping Hand\n", encoding='utf-8'
    )
    lunatic_dir = tmp_path / 'LunaticHelper'
    lunatic_dir.mkdir()

    with ModPath(helping_hand_dir) as helping_hand, ModPath(lunatic_dir) as lunatic:
        registry = load_loenn_registry(
            [
                LoennMod(helping_hand, 'MaxHelpingHand'),
                LoennMod(lunatic, 'LunaticHelper'),
            ]
        )

    placement = registry.placements_for('MaxHelpingHand/SaveFileStrawberryGate')[0]
    assert placement.mod_name == "LunaticHelper + Maddie's Helping Hand"


def test_registry_resolves_static_builtin_strawberry_copy(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'troll_strawberry.lua').write_text(
        """local strawberry = require('utils').deepcopy(require('entities.strawberry'))
strawberry.name = 'TestHelper/TrollStrawberry'
for _, placement in pairs(strawberry.placements) do
    placement.data.reappear = false
    placement.name = 'Troll Strawberry (' .. placement.name .. ')'
end
return strawberry
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    placements = registry.placements_for('TestHelper/TrollStrawberry')
    assert tuple(placement.name for placement in placements) == (
        'Troll Strawberry (normal)',
        'Troll Strawberry (normal_winged)',
        'Troll Strawberry (moon)',
    )
    assert all(placement.attrs['reappear'] is False for placement in placements)
    assert registry.placements_for('strawberry')[0].attrs.get('reappear') is None


def test_registry_includes_builtin_collectible_templates() -> None:
    registry = load_loenn_registry(())

    assert registry.display_names_for('strawberry') == (
        'Strawberry',
        'Strawberry (Winged)',
        'Moonberry',
    )
    assert (
        registry.placements_for('goldenBerry')[0].source
        == 'Loenn#a935755/Loenn/entities/golden_strawberry.lua'
    )
    assert registry.display_names_for('cassette') == ('Cassette',)
    assert registry.display_names_for('cassetteBlock') == (
        'Cassette Block (0 - Blue)',
        'Cassette Block (1 - Rose)',
        'Cassette Block (2 - Bright Sun)',
        'Cassette Block (3 - Malachite)',
    )
    assert registry.display_names_for('blackGem') == ('Crystal Heart',)
    assert registry.display_names_for('dreamHeartGem') == ('Crystal Heart (Dream)',)
    assert registry.display_names_for('fakeHeart') == ('Crystal Heart (Fake)',)
    assert registry.display_names_for('blockField') == ('Strawberry Blockfield',)


def test_registry_ignores_dynamic_placements(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'test.lua').write_text(
        """local entity = {}
entity.name = 'TestHelper/DynamicEntity'
function entity.placements()
    return {name = 'dynamic', data = {skin = getCurrentSkin()}}
end
return entity
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.placements_for('TestHelper/DynamicEntity') == ()


def test_registry_reads_a_legacy_encoded_lua_file(tmp_path: Path) -> None:
    mod_path = tmp_path / 'Legacy.zip'
    with ZipFile(mod_path, 'w') as archive:
        archive.writestr(
            'Loenn/entities/test.lua',
            b'-- legacy comment: \xb1\nlocal entity = {}\n'
            b"entity.name = 'TestHelper/LegacyEntity'\n"
            b"entity.placements = {{name = 'default'}}\nreturn entity\n",
        )

    with ModPath(mod_path) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.display_names_for('TestHelper/LegacyEntity') == ('default',)


def test_registry_skips_lua_that_loenn_cannot_parse(tmp_path: Path) -> None:
    mod_dir = tmp_path / 'Mod'
    entity_dir = mod_dir / 'Loenn' / 'entities'
    entity_dir.mkdir(parents=True)
    (entity_dir / 'broken.lua').write_text(
        """return {
    name = 'TestHelper/Broken',
    placements = {name = 'broken', data = {"speed" = 220}},
}
""",
        encoding='utf-8',
    )

    with ModPath(mod_dir) as mod:
        registry = load_loenn_registry([LoennMod(mod, 'Test Helper')])

    assert registry.placements_for('TestHelper/Broken') == ()
    assert len(registry.warnings) == 1
    assert registry.warnings[0].source == 'Mod/Loenn/entities/broken.lua'
