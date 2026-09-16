"""Dialog TXT parsing compatible with the game's language loader."""

import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Literal, cast

from pist.containers import CaseFoldDict

ENTRY_PATTERN = re.compile(r'^\w+=.*')
COMMAND_PATTERN = re.compile(r'\{.*?\}')
INSERT_PATTERN = re.compile(r'\{\+\s*(.*?)\}')
PORTRAIT_PATTERN = re.compile(r'\[(?P<content>[^\[\\]*(?:\\.[^\]\\]*)*)\]', re.IGNORECASE)
SPECIAL_KEYS = {'language', 'icon', 'order', 'font', 'split_regex', 'commas', 'periods'}
MAPS_DIR = PurePosixPath('Maps')
MAP_FILE_EXT = '.bin'
MAP_SIDE_PATTERN = re.compile(r'-(?P<side>[BC])$', re.IGNORECASE)

type MapSide = Literal['B', 'C']
type LocalizedNames = dict[str, str]

DIALOG_LANGUAGES = ('zh-cn', 'en')


def localized_name(names: Mapping[str, str], languages: Iterable[str]) -> str | None:
    """Return the first configured language available in a name mapping."""
    for lang in languages:
        if (name := names.get(lang)) is not None:
            return name
    return next(iter(names.values()), None)


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
            if new_key.casefold() in SPECIAL_KEYS:
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


def dialog_key_for_map_file(map_file: str) -> str:
    """Convert ``Maps/<path>.bin`` into Dialog key convention."""
    path = _map_file_path(map_file)
    return re.sub(r'\W', '_', '_'.join(path.with_suffix('').parts[1:]))


def campaign_dir_for_map_file(map_file: str) -> str:
    """Return the ``Maps``-relative campaign directory for one map file."""
    return _map_file_path(map_file).parent.as_posix()


def _map_file_path(map_file: str) -> PurePosixPath:
    """Validate and normalize a map file path."""
    path = PurePosixPath(map_file.replace('\\', '/'))
    if (
        len(path.parts) < 2
        or path.parts[0].casefold() != MAPS_DIR.name.casefold()
        or path.suffix.casefold() != MAP_FILE_EXT
    ):
        raise ValueError(f'Not a map file path: {map_file!r}')
    return path


def dialog_key_for_campaign_dir(campaign_dir: str) -> str:
    """Convert a ``Maps/<campaign>`` directory into its Dialog key."""
    path = _campaign_dir_path(campaign_dir)
    return re.sub(r'\W', '_', '_'.join(path.parts[1:]))


def _campaign_dir_path(campaign_dir: str) -> PurePosixPath:
    """Validate and normalize a campaign directory path."""
    path = PurePosixPath(campaign_dir.replace('\\', '/'))
    if len(path.parts) < 2 or path.parts[0].casefold() != MAPS_DIR.name.casefold():
        raise ValueError(f'Not a campaign directory: {campaign_dir!r}')
    return path


def map_base_file_and_side(map_file: str) -> tuple[str, MapSide | None]:
    """Split a ``-B`` or ``-C`` map suffix from its base map file."""
    path = PurePosixPath(map_file.replace('\\', '/'))
    match = MAP_SIDE_PATTERN.search(path.stem)
    if match is None:
        return path.as_posix(), None
    side = cast(MapSide, match['side'].upper())
    return path.with_stem(path.stem[: match.start()]).as_posix(), side


def default_map_name(map_file: str) -> str:
    """Build fallback map name from a map's parent directory."""
    path = _map_file_path(map_file)
    if len(path.parts) == 2:
        return _default_name((path.stem,))
    return default_campaign_name(path.parent.as_posix())


def default_campaign_name(campaign_dir: str) -> str:
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
