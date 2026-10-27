"""Language selection for any Dialog key and domain-specific name resolution."""

import re
from collections.abc import Mapping

from . import dialog
from .campaigns import Campaign, VanillaCampaign
from .content import MAPS_DIR, ContentPath
from .levels import Level, LevelSide
from .maps import split_map_side_suffix

type Translations = dict[str, str]


def select_translation(translations: Mapping[str, str], lang: str = 'en') -> str | None:
    """Select the requested language, falling back only to English."""
    if (text := translations.get(lang)) is not None:
        return text
    return translations.get('en') if lang != 'en' else None


def default_map_name(map_file: ContentPath) -> str:
    """Build a readable fallback from the complete Maps-relative file path."""
    if len(map_file.parts) < 2 or map_file.parts[0] != MAPS_DIR.name or map_file.suffix != '.bin':
        raise ValueError(f'Not a map file path: {map_file!r}')
    return _default_name(map_file.with_suffix('').parts[1:])


def default_campaign_name(campaign_dir: ContentPath) -> str:
    """Build a readable fallback from a Maps-relative campaign directory."""
    if not campaign_dir.parts or campaign_dir.parts[0] != MAPS_DIR.name:
        raise ValueError(f'Not a campaign directory: {campaign_dir!r}')
    return _default_name(campaign_dir.parts[1:])


def _default_name(parts: tuple[str, ...]) -> str:
    return '_'.join(
        re.sub(r'(?<=[a-z])([A-Z])', r' \1', re.sub(r'[^\w]', '_', part)) for part in parts
    )


class Localization:
    """Query any Dialog text using language preferences, with game-name conveniences.

    The supplied final Dialog mapping is borrowed, not copied or cached. Lookups
    therefore see subsequent mapping changes. Non-English queries fall back to English.
    Empty translations remain distinct from missing keys, while campaign
    prefixed-key lookup follows the existing non-empty fallback priority.
    """

    def __init__(self, dialogs: Mapping[str, Mapping[str, str]], lang: str = 'en') -> None:
        self.dialogs = dialogs
        self.lang = lang

    @property
    def langs(self) -> tuple[str, ...]:
        """Return the selected language followed by English, without duplicates."""
        return ('en',) if self.lang == 'en' else (self.lang, 'en')

    def select(self, translations: Mapping[str, str]) -> str | None:
        """Select a translation using this resolver's language and English fallback."""
        return select_translation(translations, self.lang)

    def translations(self, key: str) -> Translations:
        """Return all written translations of a Dialog key, including empty values."""
        return {
            lang: value
            for lang, entries in self.dialogs.items()
            if (value := entries.get(key)) is not None
        }

    def get(self, key: str, default: str | None = None) -> str | None:
        """Select any key's text; use default only when no translation exists.

        Query the selected language, then English if it is a different language.
        A written empty string is a translation and does not use the default.
        """
        for lang in self.langs:
            if (entries := self.dialogs.get(lang)) is not None and (
                text := entries.get(key)
            ) is not None:
                return text
        return default

    def level_names(self, level: Level) -> Translations:
        """Return a final Level's base names without side labels."""
        return self.translations(level.dialog_key)

    @staticmethod
    def level_fallback(level: Level) -> str:
        """Return the assembled Level's base-file fallback name."""
        return default_map_name(level[LevelSide.A].file_path)

    def level_name(self, level: Level) -> str:
        """Resolve a Level's base display name with a deterministic path fallback."""
        return self.get(level.dialog_key) or self.level_fallback(level)

    def _campaign_names(self, key: str) -> Translations:
        prefixed = self.translations(f'levelset_{key}')
        unprefixed = self.translations(key)
        return {
            lang: name
            for lang in self.dialogs
            if (name := prefixed.get(lang) or unprefixed.get(lang))
        }

    def collab_names(self, collab_id: str) -> Translations:
        """Resolve a collection title through its canonical lobby LevelSet keys."""
        key = dialog.dialog_key_for_campaign_dir(MAPS_DIR / collab_id / '0-Lobbies')
        return self._campaign_names(key)

    def campaign_names(self, campaign: Campaign) -> Translations:
        """Resolve LevelSet names, using the collection title for Collab hubs."""
        return self._campaign_names(campaign.dialog_key)

    @staticmethod
    def campaign_fallback(campaign: Campaign) -> str:
        """Return a neutral campaign fallback, independent of application UI labels."""
        if isinstance(campaign, VanillaCampaign):
            return 'Celeste'
        return default_campaign_name(campaign.directory) or campaign.directory.name

    def campaign_name(self, campaign: Campaign, *, default: str | None = None) -> str:
        """Resolve a campaign name, using a caller fallback or the neutral path name.

        For each selected language, prefer the non-empty LevelSet-prefixed key
        over the plain key before falling back to English.
        """
        key = campaign.dialog_key
        prefixed_key = f'levelset_{key}'
        for lang in self.langs:
            if (entries := self.dialogs.get(lang)) is not None and (
                name := entries.get(prefixed_key) or entries.get(key)
            ):
                return name
        return self.campaign_fallback(campaign) if default is None else default

    def map_names(self, map_file: ContentPath) -> Translations:
        """Return an isolated map's translations using its conventional side-free key."""
        base_file, _ = split_map_side_suffix(map_file)
        key = dialog.dialog_key_for_map_file(base_file)
        return self.translations(key)

    @staticmethod
    def map_fallback(map_file: ContentPath) -> str:
        """Return an isolated map's side-free path fallback."""
        base_file, _ = split_map_side_suffix(map_file)
        return default_map_name(base_file)

    def map_name(self, map_file: ContentPath) -> str:
        """Resolve an isolated map's base title using its conventional side-free path."""
        base_file, _ = split_map_side_suffix(map_file)
        key = dialog.dialog_key_for_map_file(base_file)
        return self.get(key) or default_map_name(base_file)
