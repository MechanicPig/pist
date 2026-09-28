"""Assemble active map assets into community-facing levels and sides."""

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from berries.game import dialog
from berries.game.content import ContentEntry, ContentPath, ContentSource
from berries.game.maps import MapInfo

_VANILLA_MAP_PATTERN = re.compile(r'(?P<level>\d+)(?P<side>[HX])?-(?P<name>.+)')
_VANILLA_SIDE_BY_SUFFIX = {'H': 'B', 'X': 'C'}
_LOST_LEVELS_STEM = 'LostLevels'
_LOST_LEVELS_LEVEL = '10'


class LevelSide(StrEnum):
    """One playable side of a community-facing level."""

    A = 'A'
    B = 'B'
    C = 'C'

    @property
    def position(self) -> int:
        """Return this Side's position in a Level's ordered map tuple."""
        return _LEVEL_SIDES.index(self)


_LEVEL_SIDES = tuple(LevelSide)


@dataclass(frozen=True, slots=True)
class Map:
    """One active map file and the content tree that supplies it."""

    map_info: MapInfo
    content: ContentSource

    def open_content(self) -> ContentEntry:
        """Open the content tree that supplies this asset."""
        return self.content.open()


@dataclass(frozen=True, slots=True)
class Level:
    """One Campaign level containing consecutive A/B/C map assets."""

    sid: str
    dialog_key: str
    maps: tuple[Map, ...]
    is_lobby: bool = False

    def __post_init__(self) -> None:
        if not 1 <= len(self.maps) <= len(_LEVEL_SIDES):
            raise ValueError('A level must have between one and three Side maps.')

    @property
    def sides(self) -> tuple[LevelSide, ...]:
        """Return the consecutive Sides occupied by this Level."""
        return _LEVEL_SIDES[: len(self.maps)]

    def __getitem__(self, side: LevelSide) -> Map:
        return self.maps[side.position]

    def map(self, side: LevelSide) -> Map | None:
        """Return the active map occupying ``side``, if present."""
        return self.maps[side.position] if side.position < len(self.maps) else None

    def localized_names(
        self,
        side: LevelSide,
        dialogs: Mapping[str, Mapping[str, str]],
    ) -> dialog.LocalizedNames:
        """Return current localized names from the final merged Dialog mapping."""
        return {
            lang: _side_name(name, side)
            for lang, entries in dialogs.items()
            if (name := entries.get(self.dialog_key)) is not None
        }

    def dialog_texts(
        self,
        dialogs: Mapping[str, Mapping[str, str]],
        suffix: str,
    ) -> dialog.LocalizedNames:
        """Return localized auxiliary text for this final level identity."""
        key = f'{self.dialog_key}_{suffix}'
        return {
            lang: value
            for lang, entries in dialogs.items()
            if (value := entries.get(key)) is not None
        }

    def fallback_name(self, side: LevelSide) -> str:
        """Return the Game-style fallback name for one assembled map side."""
        name = dialog.default_map_name(self[LevelSide.A].map_info.file_path)
        return _side_name(name, side)

    def display_name(
        self,
        side: LevelSide,
        dialogs: Mapping[str, Mapping[str, str]],
        languages: Iterable[str],
    ) -> str:
        """Resolve one map name at its final Dialog-use boundary."""
        return dialog.localized_name(
            self.localized_names(side, dialogs), languages
        ) or self.fallback_name(side)


def assemble_mod_levels(maps: Collection[Map]) -> tuple[Level, ...]:
    """Assemble conventional Mod A/B/C files without dropping malformed combinations."""
    by_path = {map_file.map_info.file_path: map_file for map_file in maps}
    order = {map_file.map_info.file_path: i for i, map_file in enumerate(maps)}
    consumed: set[ContentPath] = set()
    ordered_levels: list[tuple[int, Level]] = []
    for map_file in maps:
        path = map_file.map_info.file_path
        base_file, side_suffix = dialog.split_map_side_suffix(path)
        if side_suffix is not None:
            continue
        b_path = _side_path(base_file, LevelSide.B)
        c_path = _side_path(base_file, LevelSide.C)
        if (b_map := by_path.get(b_path)) is not None:
            side_maps = [map_file, b_map]
            consumed.update((path, b_path))
            if (c_map := by_path.get(c_path)) is not None:
                side_maps.append(c_map)
                consumed.add(c_path)
            ordered_levels.append(
                (
                    min(order[item.map_info.file_path] for item in side_maps),
                    _mod_level(base_file, tuple(side_maps)),
                )
            )
    for map_file in maps:
        path = map_file.map_info.file_path
        if path in consumed:
            continue
        consumed.add(path)
        ordered_levels.append((order[path], _mod_level(path, (map_file,))))
    return tuple(level for _, level in sorted(ordered_levels, key=lambda item: item[0]))


def assemble_vanilla_levels(maps: Iterable[Map]) -> tuple[Level, ...]:
    """Assemble original Game maps using Celeste's numeric H/X naming convention."""
    grouped: dict[str, dict[LevelSide, Map]] = {}
    identities: dict[str, tuple[str, str]] = {}
    independent: list[Level] = []
    for map_file in maps:
        path = map_file.map_info.file_path
        stem = path.stem
        if stem == _LOST_LEVELS_STEM:
            level_id = _LOST_LEVELS_LEVEL
            sid_stem = stem
            side = LevelSide.A
        elif (match := _VANILLA_MAP_PATTERN.fullmatch(stem)) is not None:
            level_id = match['level']
            sid_stem = f'{level_id}-{match["name"]}'
            side = LevelSide(_VANILLA_SIDE_BY_SUFFIX.get(match['side'], 'A'))
        else:
            independent.append(
                Level(
                    sid=_sid(path),
                    dialog_key=dialog.dialog_key_for_map_file(path),
                    maps=(map_file,),
                )
            )
            continue
        grouped.setdefault(level_id, {})[side] = map_file
        identities[level_id] = (f'Celeste/{sid_stem}', f'AREA_{level_id}')
    levels: list[Level] = []
    for level_id, entries in grouped.items():
        a_map = entries.get(LevelSide.A)
        b_map = entries.get(LevelSide.B)
        consumed: set[LevelSide] = set()
        if a_map is not None:
            side_maps = [a_map]
            consumed.add(LevelSide.A)
            if b_map is not None:
                side_maps.append(b_map)
                consumed.add(LevelSide.B)
                if (c_map := entries.get(LevelSide.C)) is not None:
                    side_maps.append(c_map)
                    consumed.add(LevelSide.C)
            sid, dialog_key = identities[level_id]
            levels.append(Level(sid=sid, dialog_key=dialog_key, maps=tuple(side_maps)))
        independent.extend(
            Level(
                sid=f'Celeste/{map_file.map_info.file_path.stem}',
                dialog_key=identities[level_id][1],
                maps=(map_file,),
            )
            for side, map_file in entries.items()
            if side not in consumed
        )
    levels.extend(independent)
    return tuple(levels)


def _mod_level(path: ContentPath, maps: tuple[Map, ...]) -> Level:
    return Level(
        sid=_sid(path),
        dialog_key=dialog.dialog_key_for_map_file(path),
        maps=maps,
    )


def _sid(path: ContentPath) -> str:
    return '/'.join(path.with_suffix('').parts[1:])


def _side_path(base_path: ContentPath, side: LevelSide) -> ContentPath:
    return base_path.with_stem(f'{base_path.stem}-{side}')


def _side_name(name: str, side: LevelSide) -> str:
    return name if side is LevelSide.A else f'{name} {side.value}'
