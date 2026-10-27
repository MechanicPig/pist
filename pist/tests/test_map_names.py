"""Side-qualified names are application policy rather than Level localization."""

import asyncio

import pytest

from berries.game.campaigns import ModCampaign, VanillaCampaign
from berries.game.content import ContentPath
from berries.game.levels import LevelSide
from berries.game.localization import Localization
from berries.map_layout import MapEntrance, MapLayout
from berries.map_preview import MapPreview as PublicMapPreview
from pist import map_names
from pist.map_preview.server import MapPreview
from test_support.map_factory import make_level_side
from test_support.mod_factory import make_installed_mod
from test_support.preview import preview_request


def test_application_source_labels_use_mod_metadata_or_original_game_label() -> None:
    original, side = make_level_side(file_path='Maps/1-ForsakenCity.bin')
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
    )
    custom, custom_side = make_level_side(file_path='Maps/Example/Map.bin', mod=mod)
    assert map_names.source_name(original[side]) == '原版'
    assert map_names.source_name(custom[custom_side]) == 'Example'


@pytest.mark.parametrize('side', tuple(LevelSide))
def test_application_names_add_side_suffix_without_changing_dialog_values(side: LevelSide) -> None:
    level, _ = make_level_side(file_path='Maps/Example/Map.bin')
    dialogs = {'en': {'Example_Map': 'English'}, 'zh-cn': {'Example_Map': '名称'}}
    suffix = '' if side is LevelSide.A else f' {side.name}'
    assert map_names.localized_names(level, side, dialogs) == {
        'en': f'English{suffix}',
        'zh-cn': f'名称{suffix}',
    }
    assert map_names.display_name(level, side, dialogs, 'zh-cn') == f'名称{suffix}'
    assert map_names.display_name(level, side, {}, 'en') == f'Example_Map{suffix}'
    assert map_names.fallback_name(level, side) == f'Example_Map{suffix}'
    assert dialogs['en']['Example_Map'] == 'English'
    assert Localization(dialogs).level_names(level)['en'] == 'English'


def test_application_campaign_labels_are_separate_from_public_localization() -> None:
    original = VanillaCampaign(ContentPath('Maps/Celeste'), ())
    ungrouped = ModCampaign(ContentPath('Maps'), ())
    collision = ModCampaign(ContentPath('Maps/Celeste'), ())
    resolver = Localization({})
    assert resolver.campaign_name(original) == resolver.campaign_name(collision) == 'Celeste'
    assert resolver.campaign_name(ungrouped) == 'Maps'
    assert map_names.campaign_name(original, {}, 'en') == '官图'
    assert map_names.campaign_name(ungrouped, {}, 'en') == '未分类'
    assert map_names.campaign_name(collision, {}, 'en') == 'Celeste（Mod）'


@pytest.mark.parametrize('side', tuple(LevelSide))
@pytest.mark.parametrize('name', ['', 'English'])
def test_single_name_lookup_preserves_empty_side_formatting(side: LevelSide, name: str) -> None:
    level, _ = make_level_side(file_path='Maps/Example/Map.bin')
    dialogs = {'en': {level.dialog_key: name}}
    expected = map_names.side_name(name, side) or map_names.fallback_name(level, side)
    assert map_names.display_name(level, side, dialogs, 'zh-cn') == expected


def test_isolated_preview_title_preserves_application_side_formatting() -> None:
    level, side = make_level_side(file_path='Maps/Example/Target-B.bin')
    map_file = level[side].file_path
    resolver = Localization({'en': {level.dialog_key: 'Target'}})
    assert resolver.map_name(map_file) == 'Target'
    assert map_names.map_title(map_file, resolver.dialogs) == 'Target B'
    assert map_names.map_title(map_file, {}) == 'Example_Target B'
    assert map_names.map_title(map_file, {'en': {level.dialog_key: ''}}) == ' B'


def test_application_preview_qualifies_side_labels_without_changing_public_preview() -> None:
    source, _ = make_level_side(file_path='Maps/Example/Source.bin')
    target, side = make_level_side(file_path='Maps/Example/Target-B.bin')
    layout = MapLayout((), (MapEntrance('start', target.sid, 8, 16, 32, 24),))
    dialogs = {'en': {target.dialog_key: 'Target'}}
    maps = {target.sid: (target, side)}
    public = PublicMapPreview(source[LevelSide.A], layout, dialogs=dialogs, routable_maps=maps)
    personal = MapPreview(source[LevelSide.A], layout, dialogs=dialogs, routable_maps=maps)

    async def entrances() -> None:
        public_response = await preview_request(public, 'GET', 'state')
        personal_response = await preview_request(personal, 'GET', 'state')
        public_entrance = (await public_response.json())['entrances'][0]
        personal_entrance = (await personal_response.json())['entrances'][0]
        assert public_entrance['targetTitle'] == 'Target'
        assert personal_entrance['targetTitle'] == 'Target B'
        assert public_entrance['targetSid'] == personal_entrance['targetSid'] == target.sid
        assert public_entrance.get('attrs') == personal_entrance.get('attrs')

    asyncio.run(entrances())
