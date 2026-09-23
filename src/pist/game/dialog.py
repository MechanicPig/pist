"""Dialog TXT parsing compatible with the game's language loader."""

import re
from collections.abc import Iterable, Mapping
from typing import Literal, Protocol, cast

from pist.containers import CaseFoldDict, FrozenCaseFoldSet
from pist.game.content import ContentPath

ENTRY_PATTERN = re.compile(r'^\w+=.*')
COMMAND_PATTERN = re.compile(r'\{.*?\}')
INSERT_PATTERN = re.compile(r'\{\+\s*(.*?)\}')
PORTRAIT_PATTERN = re.compile(r'\[(?P<content>[^\[\\]*(?:\\.[^\]\\]*)*)\]', re.IGNORECASE)
SPECIAL_KEYS = FrozenCaseFoldSet(
    {'language', 'icon', 'order', 'font', 'split_regex', 'commas', 'periods'}
)
MAPS_DIR = ContentPath('Maps')
MAP_FILE_EXT = '.bin'
MAP_SIDE_SUFFIX_PATTERN = re.compile(r'-(?P<side>[BC])$')

type MapSideSuffix = Literal['B', 'C']
type LocalizedNames = dict[str, str]


class DisplayNamed(Protocol):
    """An object with localized names and a deterministic fallback name."""

    @property
    def names(self) -> Mapping[str, str]: ...

    @property
    def fallback_name(self) -> str: ...


DIALOG_LANGUAGES = ('zh-cn', 'en')
DIALOG_FILENAMES = {
    'pt-br': 'Brazilian Portuguese.txt',
    'en': 'English.txt',
    'fr': 'French.txt',
    'de': 'German.txt',
    'it': 'Italian.txt',
    'ja': 'Japanese.txt',
    'ko': 'Korean.txt',
    'ru': 'Russian.txt',
    'zh-cn': 'Simplified Chinese.txt',
    'es': 'Spanish.txt',
}


def localized_name(names: Mapping[str, str], languages: Iterable[str]) -> str | None:
    """Return the first configured language available in a name mapping."""
    for lang in languages:
        if (name := names.get(lang)) is not None:
            return name
    return next(iter(names.values()), None)


def display_name(value: DisplayNamed, languages: Iterable[str]) -> str:
    """Return an object's preferred localized name or its fallback name."""
    return localized_name(value.names, languages) or value.fallback_name


def parse_dialog(content: str) -> CaseFoldDict[str]:
    """Parse one UTF-8 Dialog TXT file into cleaned, display-ready strings.

    This follows the game's ``Language.FromTxt`` entry and continuation rules.
    """
    raw_entries: CaseFoldDict[str] = CaseFoldDict()
    key = ''
    value = ''
    previous_line = ''

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        line = PORTRAIT_PATTERN.sub(r'{portrait \g<content>}', line).replace(r'\#', '#')
        if ENTRY_PATTERN.fullmatch(line):
            if key:
                raw_entries[key] = value
            new_key, new_value = line.split('=', maxsplit=1)
            if new_key in SPECIAL_KEYS:
                continue
            key = new_key
            value = new_value.strip()
        elif key:
            if (
                value
                and not value.endswith(('{break}', '{n}'))
                and COMMAND_PATTERN.sub('', previous_line)
            ):
                value += '{break}'
            value += line
        previous_line = line

    if key:
        raw_entries[key] = value
    return _clean_entries(raw_entries)


def dialog_key_for_map_file(map_file: ContentPath) -> str:
    """Convert ``Maps/<path>.bin`` into Dialog key convention."""
    path = _map_file_path(map_file)
    return _dialog_keyify('_'.join(path.with_suffix('').parts[1:]))


def campaign_dir_for_map_file(map_file: ContentPath) -> ContentPath:
    """Return the ``Maps``-relative campaign directory for one map file."""
    return _map_file_path(map_file).parent


def _map_file_path(map_file: ContentPath) -> ContentPath:
    """Validate a map file path."""
    if (
        len(map_file.parts) < 2
        or map_file.parts[0] != MAPS_DIR.name
        or map_file.suffix != MAP_FILE_EXT
    ):
        raise ValueError(f'Not a map file path: {map_file!r}')
    return map_file


def dialog_key_for_campaign_dir(campaign_dir: ContentPath) -> str:
    """Convert a ``Maps/<campaign>`` directory into its Dialog key."""
    path = _campaign_dir_path(campaign_dir)
    return _dialog_keyify('_'.join(path.parts[1:]))


def _campaign_dir_path(campaign_dir: ContentPath) -> ContentPath:
    """Validate a campaign directory path."""
    if len(campaign_dir.parts) < 2 or campaign_dir.parts[0] != MAPS_DIR.name:
        raise ValueError(f'Not a campaign directory: {campaign_dir!r}')
    return campaign_dir


def split_map_side_suffix(map_file: ContentPath) -> tuple[ContentPath, MapSideSuffix | None]:
    """Split a ``-B`` or ``-C`` map suffix from its base map file."""
    match = MAP_SIDE_SUFFIX_PATTERN.search(map_file.stem)
    if match is None:
        return map_file, None
    suffix = cast(MapSideSuffix, match['side'])
    return map_file.with_stem(map_file.stem[: match.start()]), suffix


def default_map_name(map_file: ContentPath) -> str:
    """Build fallback map name from its complete ``Maps``-relative path."""
    path = _map_file_path(map_file)
    return _default_name(path.with_suffix('').parts[1:])


def default_campaign_name(campaign_dir: ContentPath) -> str:
    """Build fallback campaign name from its ``Maps`` directory."""
    path = _campaign_dir_path(campaign_dir)
    return _default_name(path.parts[1:])


def _default_name(parts: tuple[str, ...]) -> str:
    return '_'.join(
        re.sub(r'(?<=[a-z])([A-Z])', r' \1', re.sub(r'[^\w]', '_', part)) for part in parts
    )


def _dialog_keyify(value: str) -> str:
    """Apply Everest's Dialog key conversion without changing key case."""
    return value.replace('/', '_').replace('-', '_').replace('+', '_').replace(' ', '_')


def _clean_entries(entries: Mapping[str, str]) -> CaseFoldDict[str]:
    expanded = CaseFoldDict(entries)
    for key, value in expanded.items():
        for _ in range(len(entries) + 1):
            match = INSERT_PATTERN.search(value)
            if match is None:
                break
            replacement = expanded.get(match.group(1), '[XXX]')
            value = value.replace(match.group(0), replacement)
        else:
            raise ValueError(f'Circular Dialog insertion while expanding {key!r}.')
        expanded[key] = value
    return CaseFoldDict(
        (key, COMMAND_PATTERN.sub('', value.replace('{n}', '\n').replace('{break}', '\n')))
        for key, value in expanded.items()
    )
