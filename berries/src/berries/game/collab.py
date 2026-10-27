"""Read CollabUtils2 lobby identities, journal references and map icon order."""

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Self

from .binmap import (
    MapStringDecodeWarning,
    map_string_warnings,
    parse_map_meta,
)
from .content import ContentPath
from .map_data import load_map_data
from .maps import Map, split_map_side_suffix
from .mods import InstalledMod

JOURNAL_TRIGGER = 'CollabUtils2/JournalTrigger'
COLLAB_JOURNAL_ICON_PATTERN = re.compile(r'.*/\d+-.*')


@dataclass(frozen=True, slots=True)
class JournalRefs:
    """Static Campaign references and invalid-trigger count read from one lobby map."""

    campaign_refs: tuple[str, ...]
    invalid_count: int = 0

    @classmethod
    def from_map(cls, map_file: Map) -> Self:
        """Read exact JournalTrigger level-set identities in source order."""
        map_data = load_map_data(map_file)
        result: list[str] = []
        seen: set[str] = set()
        invalid_count = 0
        for element in map_data.root.walk():
            if element.name != JOURNAL_TRIGGER:
                continue
            value = element.attrs.get('levelset')
            if not isinstance(value, str) or not value:
                invalid_count += 1
            elif value not in seen:
                seen.add(value)
                result.append(value)
        return cls(tuple(result), invalid_count)


def is_lobby_sid(sid: str, collab_ids: Iterable[str]) -> bool:
    """Apply CollabUtils2's exact lobby predicate to one assembled map SID."""
    return any(
        sid.startswith(f'{collab_id}/0-Lobbies/') and sid != f'{collab_id}/0-Lobbies/0-Prologue'
        for collab_id in collab_ids
    )


def collab_journal_map_order_from_icons(
    maps: Iterable[ContentPath], icons: Mapping[ContentPath, str | None]
) -> list[ContentPath]:
    """Order maps from cached Collab journal icon paths when they are all usable."""
    map_list = list(maps)
    if not all(
        (icon := icons.get(map_info)) is not None and COLLAB_JOURNAL_ICON_PATTERN.fullmatch(icon)
        for map_info in map_list
    ):
        return map_list
    return sorted(
        map_list,
        key=lambda map_info: (
            split_map_side_suffix(map_info)[0].stem == 'ZZ-HeartSide',
            str(icons[map_info]).casefold(),
            map_info.as_posix().casefold(),
        ),
    )


def iter_collab_journal_map_icons(
    mod: InstalledMod,
    maps: Iterable[ContentPath],
    *,
    on_warning: Callable[[ContentPath, MapStringDecodeWarning], None] | None = None,
) -> Iterator[tuple[ContentPath, str | None]]:
    """Yield Collab journal icon paths one map at a time without loading layouts."""
    with mod.open() as root:
        for map_info in maps:
            with map_string_warnings(None if on_warning is None else partial(on_warning, map_info)):
                icon = parse_map_meta(
                    root.joinpath(map_info).read_bytes(), allow_trailing=True
                ).get('Icon')
            yield map_info, icon if isinstance(icon, str) else None
