"""Pist's map, campaign and source labels for records, browsing and previews."""

from collections.abc import Mapping

from berries.game import dialog, localization
from berries.game.campaigns import Campaign, ModCampaign, VanillaCampaign
from berries.game.content import MAPS_DIR, ContentPath
from berries.game.levels import Level, LevelSide
from berries.game.localization import Localization
from berries.game.maps import Map, ModMap, split_map_side_suffix


def source_name(map_file: Map) -> str:
    """Return the Mod metadata name or Pist's original-game source label."""
    return map_file.mod.metadata_name if isinstance(map_file, ModMap) else '原版'


def side_name(name: str, side: LevelSide) -> str:
    """Keep A-side names unchanged and append the B/C side to application names."""
    return name if side is LevelSide.A else f'{name} {side.name}'


def localized_names(
    level: Level, side: LevelSide, dialogs: Mapping[str, Mapping[str, str]]
) -> localization.Translations:
    """Return localized map names with Pist's side suffix convention."""
    return {
        lang: side_name(name, side)
        for lang, name in Localization(dialogs).level_names(level).items()
    }


def fallback_name(level: Level, side: LevelSide) -> str:
    """Return a side-qualified fallback for an assembled map."""
    return side_name(Localization.level_fallback(level), side)


def display_name(
    level: Level,
    side: LevelSide,
    dialogs: Mapping[str, Mapping[str, str]],
    lang: str,
) -> str:
    """Resolve a side-qualified name using the caller's language preference."""
    name = Localization(dialogs, lang).get(level.dialog_key)
    return (side_name(name, side) if name is not None else '') or fallback_name(level, side)


def campaign_fallback(campaign: Campaign) -> str:
    """Return Pist's original-game and ungrouped labels, or the neutral path fallback."""
    if isinstance(campaign, VanillaCampaign):
        return '官图'
    if campaign.directory == MAPS_DIR:
        return '未分类'
    return Localization.campaign_fallback(campaign)


def campaign_name(campaign: Campaign, dialogs: Mapping[str, Mapping[str, str]], lang: str) -> str:
    """Resolve a campaign name with Pist's labels and reserved-LevelSet disambiguation."""
    resolver = Localization(dialogs, lang)
    name = resolver.campaign_name(campaign, default='') or campaign_fallback(campaign)
    return (
        f'{name}（Mod）'
        if isinstance(campaign, ModCampaign) and campaign.uses_vanilla_campaign
        else name
    )


def map_title(map_file: ContentPath, dialogs: Mapping[str, Mapping[str, str]]) -> str:
    """Resolve an isolated preview title with Pist's conventional side suffix."""
    base_file, side = split_map_side_suffix(map_file)
    level_side = LevelSide.A if side is None else LevelSide[side]
    resolver = Localization(dialogs, 'zh-cn')
    name = resolver.get(dialog.dialog_key_for_map_file(base_file))
    return (side_name(name, level_side) if name is not None else '') or side_name(
        localization.default_map_name(base_file), level_side
    )
