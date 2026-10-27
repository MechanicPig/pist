"""Final Dialog lookup, preference and fallback independent of domain model methods."""

import pytest

from berries.containers import CaseFoldDict
from berries.game.campaigns import (
    CollabHubCampaign,
    CollabSubCampaign,
    ModCampaign,
    VanillaCampaign,
)
from berries.game.content import ContentPath
from berries.game.localization import Localization, select_translation
from test_support.map_factory import make_level_side


@pytest.mark.parametrize('filename', ['Map.bin', 'Map-B.bin', 'Map-C.bin'])
def test_map_localization_only_requires_a_content_path(filename: str) -> None:
    map_file = ContentPath('Maps/Example') / filename
    resolver = Localization({'en': CaseFoldDict({'example_map': 'English'})})
    assert resolver.map_names(map_file) == {'en': 'English'}
    assert resolver.map_name(map_file) == 'English'
    assert resolver.map_fallback(map_file) == 'Example_Map'
    assert Localization({}).map_name(map_file) == 'Example_Map'


def test_default_language_is_english_with_no_other_language_fallback() -> None:
    resolver = Localization({'ja': {'TEXT': 'Japanese'}, 'en': {'EMPTY': ''}})
    assert resolver.lang == 'en'
    assert resolver.langs == ('en',)
    assert resolver.get('TEXT', 'Default') == 'Default'
    assert resolver.select({'ja': 'Japanese'}) is None
    assert resolver.get('EMPTY', 'Default') == ''
    japanese = Localization(resolver.dialogs, 'ja')
    assert japanese.langs == ('ja', 'en')
    assert japanese.get('TEXT') == 'Japanese'
    assert japanese.get('EMPTY', 'Default') == ''


def test_campaign_types_derive_their_dialog_keys() -> None:
    assert ModCampaign(ContentPath('Maps/Author/Pack'), ()).dialog_key == 'Author_Pack'
    assert VanillaCampaign(ContentPath('Maps'), ()).dialog_key == 'Celeste'
    assert (
        CollabHubCampaign(
            ContentPath('Maps/Author/Collab/0-Lobbies'), (), collab_id='Author/Collab'
        ).dialog_key
        == 'Author_Collab_0_Lobbies'
    )
    assert (
        CollabSubCampaign(
            ContentPath('Maps/Author/Collab/1-Beginner'), (), collab_id='Author/Collab'
        ).dialog_key
        == 'Author_Collab_1_Beginner'
    )


@pytest.mark.parametrize('lang', ['zh-cn', 'en', 'ja', 'missing'])
@pytest.mark.parametrize('key', ['TEXT', 'EMPTY', 'OTHER', 'ABSENT'])
def test_direct_lookup_matches_translation_selection(lang: str, key: str) -> None:
    resolver = Localization(
        {
            'ja': {'OTHER': 'Fallback', 'EMPTY': 'Not preferred'},
            'en': {'TEXT': 'English', 'EMPTY': ''},
            'zh-cn': {'TEXT': '中文'},
        },
        lang,
    )
    expected = resolver.select(resolver.translations(key))
    assert resolver.get(key) == expected
    assert resolver.get(key, 'Default') == ('Default' if expected is None else expected)


def test_arbitrary_dialog_keys_use_shared_language_selection() -> None:
    resolver = Localization(
        {
            'en': CaseFoldDict({'UI_PROMPT': 'Continue?', 'EMPTY': ''}),
            'zh-cn': CaseFoldDict({'UI_PROMPT': '继续？'}),
            'ja': CaseFoldDict({'OTHER_TEXT': '別の文章'}),
        },
        'zh-cn',
    )
    assert resolver.translations('ui_prompt') == {'en': 'Continue?', 'zh-cn': '继续？'}
    assert resolver.get('UI_PROMPT') == '继续？'
    assert resolver.get('OTHER_TEXT') is None
    assert resolver.translations('ABSENT') == {}
    assert resolver.get('ABSENT') is None
    assert resolver.get('ABSENT', 'Fallback') == 'Fallback'
    assert resolver.get('EMPTY', 'Fallback') == ''
    assert resolver.get('UI_PROMPT') == '继续？'


def test_campaign_name_queries_prefer_levelset_and_resolve_collab_hubs() -> None:
    entries = {
        'en': CaseFoldDict(
            {
                'Pack': 'Unprefixed',
                'LEVELSET_PACK': 'Prefixed',
                'levelset_Author_Collab_0_Lobbies': 'Collab',
            }
        )
    }
    resolver = Localization(entries)
    campaign = ModCampaign(ContentPath('Maps/Pack'), ())
    missing = ModCampaign(ContentPath('Maps/Missing'), ())
    hub = CollabHubCampaign(
        ContentPath('Maps/Author/Collab/0-Lobbies'), (), collab_id='Author/Collab'
    )
    assert resolver.campaign_names(campaign) == {'en': 'Prefixed'}
    assert resolver.campaign_names(missing) == {}
    assert resolver.collab_names('Author/Collab') == {'en': 'Collab'}
    assert resolver.campaign_names(hub) == {'en': 'Collab'}
    assert resolver.campaign_name(hub) == 'Collab'
    assert resolver.campaign_name(missing) == 'Missing'
    assert resolver.campaign_name(missing, default='Custom') == 'Custom'
    assert resolver.campaign_name(missing, default='') == ''


def test_language_preference_handles_missing_and_empty_translations() -> None:
    assert select_translation({'en': 'English'}, 'zh-cn') == 'English'
    assert select_translation({'ja': 'Japanese'}, 'zh-cn') is None
    assert select_translation({}, 'en') is None
    assert select_translation({'en': ''}, 'en') == ''
    level, _ = make_level_side(file_path='Maps/Pack/Map.bin')
    resolver = Localization({'en': {level.dialog_key: ''}}, 'en')
    assert resolver.level_names(level) == {'en': ''}
    assert resolver.level_name(level) == resolver.level_fallback(level)
    assert resolver.select({'en': 'First'}) == 'First'
    assert resolver.select({'en': 'Second'}) == 'Second'


def test_queries_observe_mapping_updates_and_preserve_auxiliary_text() -> None:
    level, _ = make_level_side(file_path='Maps/Pack/Map.bin')
    entries = CaseFoldDict({level.dialog_key: 'Before', f'{level.dialog_key}_author': 'Author'})
    resolver = Localization({'en': entries})
    assert resolver.level_name(level) == 'Before'
    assert resolver.translations(f'{level.dialog_key}_author') == {'en': 'Author'}
    entries[level.dialog_key] = 'After'
    assert resolver.level_name(level) == 'After'
    assert resolver.translations(f'{level.dialog_key}_missing') == {}


def test_campaign_empty_prefixed_entry_falls_back_to_unprefixed_entry() -> None:
    campaign = ModCampaign(ContentPath('Maps/Pack'), ())
    resolver = Localization({'en': {'levelset_Pack': '', 'Pack': 'Unprefixed'}})
    assert resolver.campaign_name(campaign) == 'Unprefixed'


@pytest.mark.parametrize(
    ('preferred', 'expected'),
    [
        ({'levelset_Pack': 'Preferred', 'Pack': 'Plain'}, 'Preferred'),
        ({'Pack': 'Plain'}, 'Plain'),
        ({'levelset_Pack': '', 'Pack': 'Plain'}, 'Plain'),
        ({'levelset_Pack': '', 'Pack': ''}, 'English'),
        ({}, 'English'),
    ],
)
def test_campaign_name_preserves_language_and_key_priority(
    preferred: dict[str, str], expected: str
) -> None:
    campaign = ModCampaign(ContentPath('Maps/Pack'), ())
    resolver = Localization(
        {'zh-cn': preferred, 'en': {'levelset_Pack': 'English'}, 'ja': {'Pack': 'Japanese'}},
        'zh-cn',
    )
    assert resolver.campaign_name(campaign) == expected
    assert resolver.campaign_name(campaign) == resolver.select(resolver.campaign_names(campaign))
