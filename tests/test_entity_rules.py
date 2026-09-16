import asyncio
import tomllib
from pathlib import Path

import tomli_w
from textual.widgets import Collapsible, Input, Select

from pist.entities.rules import EntityConfigStore
from pist.loenn import LoennPlacement, LoennRegistry, LoennWarning
from pist.ui.entities.rules import (
    RULE_CONFLICTS_ID,
    EntityRuleApp,
    LoennPlacementItem,
    search_placements,
)


def _split_config(path: Path) -> Path:
    config = tomllib.loads(path.read_text(encoding='utf-8'))
    kinds_path = path.with_name('kinds.toml')
    kinds_path.write_text(tomli_w.dumps({'kinds': config.pop('kinds', {})}), encoding='utf-8')
    path.write_text(tomli_w.dumps(config), encoding='utf-8')
    return kinds_path


def test_placement_search_matches_editor_metadata() -> None:
    placement = LoennPlacement(
        entity_name='JungleHelper/TreeDepthController',
        name='golden',
        attrs={},
        display_name='Golden Strawberry (Grabless)',
        source='Loenn/entities/grablessGoldenBerry.lua',
    )

    assert search_placements([placement], 'grabless jungle') == (placement,)
    assert search_placements([placement], 'silver') == ()
    assert search_placements([placement], 'loenn') == ()


def test_rule_app_selects_a_template_and_writes_its_static_condition(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.silverberry]
label = 'Silver Berry'
parent = 'berry'
""",
        encoding='utf-8',
    )
    placement = LoennPlacement(
        entity_name='CollabUtils2/SilverBerry',
        name='default',
        attrs={'alwaysSpawn': False},
        display_name='Silver Berry',
        source='Loenn/entities/silverBerry.lua',
    )

    async def check() -> None:
        app = EntityRuleApp(
            LoennRegistry([placement]), EntityConfigStore(config_path, kinds_path=kinds_path)
        )
        async with app.run_test() as pilot:
            await pilot.click('#template-list > ListItem')
            app.query_one('#kind-input', Select).value = 'silverberry'
            await pilot.click('#save-rule')

    kinds_path = _split_config(config_path)
    asyncio.run(check())
    rules = EntityConfigStore(config_path, kinds_path=kinds_path).load()
    assert rules.entities['CollabUtils2/SilverBerry'].rules[0].when == {'alwaysSpawn': False}


def test_rule_app_shows_skipped_mod_warnings(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text("[kinds.berry]\nlabel = 'Berry'\n", encoding='utf-8')

    async def check() -> None:
        app = EntityRuleApp(
            LoennRegistry(warnings=[LoennWarning('Broken.zip', 'invalid encoding')]),
            EntityConfigStore(config_path, kinds_path=kinds_path),
        )
        async with app.run_test() as pilot:
            warning = app.query_one('#template-warnings', Collapsible)
            assert warning.collapsed
            await pilot.press('ctrl+r')

    kinds_path = _split_config(config_path)
    asyncio.run(check())


def test_rule_app_shows_local_rule_conflicts(tmp_path) -> None:
    shared_path = tmp_path / 'shared.toml'
    shared_path.write_text(
        """[kinds.berry]
label = 'Berry'

[kinds.strawberry]
label = 'Strawberry'
parent = 'berry'

[kinds.moonberry]
label = 'Moonberry'
parent = 'berry'

[entities."TestHelper/Berry"]
rules = [{ kind = 'strawberry' }]
""",
        encoding='utf-8',
    )
    local_path = tmp_path / 'local.toml'
    local_path.write_text(
        """[entities."TestHelper/Berry"]
rules = [{ kind = 'moonberry' }]
""",
        encoding='utf-8',
    )

    async def check() -> None:
        app = EntityRuleApp(
            LoennRegistry(),
            EntityConfigStore(
                shared_path=shared_path, kinds_path=kinds_path, local_path=local_path
            ),
        )
        async with app.run_test():
            warning = app.query_one(f'#{RULE_CONFLICTS_ID}', Collapsible)
            assert warning.collapsed

    kinds_path = _split_config(shared_path)
    asyncio.run(check())


def test_rule_app_keeps_current_stylesheet_when_refresh_fails(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text("[kinds.berry]\nlabel = 'Berry'\n", encoding='utf-8')
    stylesheet_path = tmp_path / 'entity_rules.tcss'
    stylesheet_path.write_text('#template-filter { border: round $primary; }', encoding='utf-8')

    class TestRuleApp(EntityRuleApp):
        CSS_PATH = stylesheet_path

    async def check() -> None:
        app = TestRuleApp(LoennRegistry(), EntityConfigStore(config_path, kinds_path=kinds_path))
        messages: list[tuple[str, str]] = []

        def notify(message: str, *, severity: str = 'information', **_: object) -> None:
            messages.append((message, severity))

        monkeypatch.setattr(app, 'notify', notify)
        async with app.run_test() as pilot:
            old_stylesheet = app.stylesheet
            stylesheet_path.write_text('#template-filter { invalid: syntax; }', encoding='utf-8')
            await pilot.press('ctrl+r')
            assert app.stylesheet is old_stylesheet

        assert messages[-1][1] == 'warning'

    kinds_path = _split_config(config_path)
    asyncio.run(check())


def test_rule_app_filters_template_list_after_text_changes(tmp_path) -> None:
    config_path = tmp_path / 'entities.toml'
    config_path.write_text("[kinds.berry]\nlabel = 'Berry'\n", encoding='utf-8')
    strawberry = LoennPlacement('Test/Strawberry', 'default', {}, 'Strawberry', 'one.lua')
    unrelated = LoennPlacement('Test/Controller', 'default', {}, 'Controller', 'two.lua')

    async def check() -> None:
        app = EntityRuleApp(
            LoennRegistry([strawberry, unrelated]),
            EntityConfigStore(config_path, kinds_path=kinds_path),
        )
        async with app.run_test() as pilot:
            text_filter = app.query_one('#template-filter', Input)
            for value in ('S', 'St', 'Stra', 'Strawberry'):
                text_filter.value = value
            await pilot.pause()
            assert [
                item.placement
                for item in app.query_one('#template-list').query(LoennPlacementItem)
                if item.display
            ] == [strawberry]

    kinds_path = _split_config(config_path)
    asyncio.run(check())
