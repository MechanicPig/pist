"""Dialog language files, parsing, merging and key conversion."""

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path, PurePath
from typing import Annotated
from zipfile import BadZipFile

from pydantic import PlainSerializer, ValidateAs

from berries.containers import CaseFoldDict, FrozenCaseFoldSet

from .content import DIALOG_DIR, MAPS_DIR, ContentEntry, ContentPath

ENTRY_PATTERN = re.compile(r'^\w+=.*')
COMMAND_PATTERN = re.compile(r'\{.*?\}')
INSERT_PATTERN = re.compile(r'\{\+\s*(.*?)\}')
PORTRAIT_PATTERN = re.compile(r'\[(?P<content>[^\[\\]*(?:\\.[^\]\\]*)*)\]', re.IGNORECASE)
SPECIAL_KEYS = FrozenCaseFoldSet(
    {'language', 'icon', 'order', 'font', 'split_regex', 'commas', 'periods'}
)
MAP_FILE_EXT = '.bin'

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


def to_dialog_key(value: str) -> str:
    """Apply Everest's ``Extensions.DialogKeyify`` transformation."""
    return value.translate(_DIALOG_KEY_TRANS)


class DialogReadError(Exception):
    """A Dialog directory or file could not be enumerated, read or parsed."""

    def __init__(self, root: Path, file_path: PurePath, cause: Exception) -> None:
        self.root = root
        self.file_path = file_path
        self.cause = cause
        super().__init__(f'Unable to read Dialog {root} / {file_path}: {cause}')


def _handle_read_error(
    entry: ContentEntry,
    cause: Exception,
    on_error: Callable[[DialogReadError], None] | None,
) -> None:
    error = DialogReadError(entry.root, entry.at, cause)
    if on_error is None:
        raise error from cause
    on_error(error)


def _iter_dialog_entries(
    dialog_dir: ContentEntry, on_error: Callable[[DialogReadError], None] | None
) -> Iterator[ContentEntry]:
    """Report expected enumeration failures without intercepting consumer errors."""
    try:
        yield from dialog_dir.iterdir()
    except (OSError, BadZipFile, KeyError) as error:
        _handle_read_error(dialog_dir, error, on_error)


def read_dialogs(
    dialog_dir: ContentEntry,
    *,
    encoding: str = 'utf-8-sig',
    strict: bool = False,
    on_error: Callable[[DialogReadError], None] | None = None,
) -> dict[str, CaseFoldDict[str]]:
    """Read supported language files directly inside one directory without closing its source.

    Missing directories return an empty mapping. Strict mode requires the exact
    directory name Dialog, independently of error handling. Expected enumeration,
    reading or parsing failures raise DialogReadError by default. With on_error,
    the callback receives the source location and original cause immediately;
    returning continues with successful files, while raising aborts the read.
    Invalid encoding configuration and programming errors propagate unchanged.
    """
    if strict and dialog_dir.name != DIALOG_DIR.name:
        raise ValueError(f'Expected a directory named {DIALOG_DIR.name!r}: {dialog_dir.at!r}')
    if not dialog_dir.is_dir():
        if dialog_dir.exists():
            raise NotADirectoryError(f'{dialog_dir.root} / {dialog_dir.at}')
        return {}
    dialogs: dict[str, CaseFoldDict[str]] = {}
    for path in _iter_dialog_entries(dialog_dir, on_error):
        if (lang := language_for_filename(path.name)) is None:
            continue
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding=encoding)
        except (OSError, UnicodeDecodeError, BadZipFile, KeyError) as error:
            _handle_read_error(path, error, on_error)
            continue
        try:
            parsed = parse_dialog(text)
        except ValueError as error:
            _handle_read_error(path, error, on_error)
            continue
        entries = dialogs.get(lang)
        if entries is None:
            entries = CaseFoldDict()
            dialogs[lang] = entries
        entries.update(parsed)
    return dialogs


def parse_dialog(content: str) -> CaseFoldDict[str]:
    """Parse one decoded Dialog TXT file into cleaned, display-ready strings.

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
    return to_dialog_key('_'.join(path.with_suffix('').parts[1:]))


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
    return to_dialog_key('_'.join(path.parts[1:]))


def _campaign_dir_path(campaign_dir: ContentPath) -> ContentPath:
    """Validate a campaign directory path."""
    if not campaign_dir.parts or campaign_dir.parts[0] != MAPS_DIR.name:
        raise ValueError(f'Not a campaign directory: {campaign_dir!r}')
    return campaign_dir


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
