"""Dialog language files, parsing, merging and localized display names."""

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Annotated, Protocol

from pydantic import PlainSerializer, ValidateAs

from berries.containers import CaseFoldDict, FrozenCaseFoldSet
from berries.game.content import MAPS_DIR, ContentEntry, ContentPath

ENTRY_PATTERN = re.compile(r'^\w+=.*')
COMMAND_PATTERN = re.compile(r'\{.*?\}')
INSERT_PATTERN = re.compile(r'\{\+\s*(.*?)\}')
PORTRAIT_PATTERN = re.compile(r'\[(?P<content>[^\[\\]*(?:\\.[^\]\\]*)*)\]', re.IGNORECASE)
SPECIAL_KEYS = FrozenCaseFoldSet(
    {'language', 'icon', 'order', 'font', 'split_regex', 'commas', 'periods'}
)
MAP_FILE_EXT = '.bin'

type LocalizedNames = dict[str, str]

type DialogEntries = Annotated[
    CaseFoldDict[str],
    ValidateAs(dict[str, str], CaseFoldDict),
    PlainSerializer(dict, return_type=dict[str, str]),
]


def merge_dialogs(
    sources: Iterable[Mapping[str, Mapping[str, str]]],
    *,
    base_dialogs: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, CaseFoldDict[str]]:
    """Copy the base entries and apply sources in caller-supplied override order."""
    merged = {lang: CaseFoldDict(entries) for lang, entries in (base_dialogs or {}).items()}
    for source in sources:
        for lang, entries in source.items():
            current = merged.get(lang)
            if current is None:
                current = CaseFoldDict()
                merged[lang] = current
            current.update(entries)
    return merged


def localize_campaign_key(
    dialog_key: str, *, dialogs: Mapping[str, Mapping[str, str]]
) -> LocalizedNames:
    """Look up an Everest LevelSet name with its prefixed-key priority."""
    return {
        lang: name
        for lang, entries in dialogs.items()
        if (name := entries.get(f'levelset_{dialog_key}') or entries.get(dialog_key))
    }


def localize_collab_names(
    collab_id: str | None, *, dialogs: Mapping[str, Mapping[str, str]]
) -> LocalizedNames:
    """Return a Collab title from its lobby LevelSet Dialog keys."""
    if collab_id is None:
        return {}
    dialog_key = dialog_key_for_campaign_dir(MAPS_DIR / collab_id / '0-Lobbies')
    return localize_campaign_key(dialog_key, dialogs=dialogs)


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
_DIALOG_LANG_BY_FILENAME = {
    filename.casefold(): lang for lang, filename in DIALOG_FILENAMES.items()
}

_DIALOG_KEY_TRANS = str.maketrans('/-+ ', '____')


def language_for_filename(filename: str) -> str | None:
    """Identify a supported Everest Dialog language filename.

    Everest requires the ``.txt`` extension to use its canonical lowercase spelling, while
    matching the complete language filename without regard to case.
    """
    if not filename.endswith('.txt'):
        return None
    return _DIALOG_LANG_BY_FILENAME.get(filename.casefold())


def _to_dialog_key(value: str) -> str:
    """Apply Everest's ``Extensions.DialogKeyify`` transformation."""
    return value.translate(_DIALOG_KEY_TRANS)


def localized_name(names: Mapping[str, str], languages: Iterable[str]) -> str | None:
    """Return the first configured language available in a name mapping."""
    for lang in languages:
        if (name := names.get(lang)) is not None:
            return name
    return next(iter(names.values()), None)


def display_name(value: DisplayNamed, languages: Iterable[str]) -> str:
    """Return an object's preferred localized name or its fallback name."""
    return localized_name(value.names, languages) or value.fallback_name


def read_dialogs(
    paths: Iterable[ContentEntry],
    *,
    read_text: Callable[[ContentEntry], str | None] = ContentEntry.read_text,
) -> dict[str, CaseFoldDict[str]]:
    """Read selected language files in order without taking ownership of their source.

    A caller-supplied reader may record diagnostics and return None to skip a file.
    Otherwise decoding and parsing errors propagate to the caller.
    """
    dialogs: dict[str, CaseFoldDict[str]] = {}
    for path in paths:
        if not path.is_file() or (lang := language_for_filename(path.name)) is None:
            continue
        text = read_text(path)
        if text is not None:
            entries = dialogs.get(lang)
            if entries is None:
                entries = CaseFoldDict()
                dialogs[lang] = entries
            entries.update(parse_dialog(text))
    return dialogs


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
    return _to_dialog_key('_'.join(path.with_suffix('').parts[1:]))


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
    return _to_dialog_key('_'.join(path.parts[1:]))


def _campaign_dir_path(campaign_dir: ContentPath) -> ContentPath:
    """Validate a campaign directory path."""
    if not campaign_dir.parts or campaign_dir.parts[0] != MAPS_DIR.name:
        raise ValueError(f'Not a campaign directory: {campaign_dir!r}')
    return campaign_dir


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
