"""Assemble active map assets into community-facing levels and sides."""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import IntEnum

from .content import MAPS_DIR, ContentPath
from .maps import Map, VanillaMap, split_map_side_suffix

_VANILLA_MAP_PATTERN = re.compile(r'(?P<level>\d+)(?P<side>[HX])?-(?P<name>.+)')
_VANILLA_SIDE_BY_SUFFIX = {'H': 'B', 'X': 'C'}
_LOST_LEVELS_STEM = 'LostLevels'
_LOST_LEVELS_LEVEL = '10'


class LevelSide(IntEnum):
    """A playable side's zero-based position in a Level's ordered map sequence."""

    A = 0
    B = 1
    C = 2


type MapKey = tuple[str, LevelSide]


@dataclass(frozen=True, slots=True)
class Level:
    """One Campaign level containing consecutive A/B/C map assets."""

    sid: str
    maps: tuple[Map, ...]
    is_lobby: bool = False

    def __post_init__(self) -> None:
        if not 1 <= len(self.maps) <= len(LevelSide):
            raise ValueError('A level must have between one and three Side maps.')

    @property
    def dialog_key(self) -> str:
        """Return the assembled base map's key, or the original game's AREA key."""
        base_map = self.maps[0]
        if isinstance(base_map, VanillaMap):
            stem = base_map.file_path.stem
            if stem == _LOST_LEVELS_STEM:
                return f'AREA_{_LOST_LEVELS_LEVEL}'
            if (match := _VANILLA_MAP_PATTERN.fullmatch(stem)) is not None:
                return f'AREA_{match["level"]}'
        return base_map.dialog_key

    @property
    def sides(self) -> tuple[LevelSide, ...]:
        """Return the consecutive Sides occupied by this Level."""
        return tuple(side for side, _ in zip(LevelSide, self.maps))

    def __getitem__(self, side: LevelSide) -> Map:
        return self.maps[side]

    def map(self, side: LevelSide) -> Map | None:
        """Return the active map occupying ``side``, if present."""
        return self.maps[side] if side < len(self.maps) else None


def local_sid_for_map_file(map_file: ContentPath) -> str:
    """Infer an exact SID candidate from one isolated local map file."""
    parts = map_file.with_suffix('').parts
    if not parts or parts[0] != MAPS_DIR.name:
        raise ValueError(f'Not a map file path: {map_file!r}')
    return '/'.join(parts[1:])


def assemble_mod_levels(maps: Collection[Map]) -> tuple[Level, ...]:
    """Assemble conventional Mod A/B/C files without dropping malformed combinations."""
    by_path = {map_file.file_path: map_file for map_file in maps}
    order = {map_file.file_path: i for i, map_file in enumerate(maps)}
    consumed: set[ContentPath] = set()
    ordered_levels: list[tuple[int, Level]] = []
    for map_file in maps:
        path = map_file.file_path
        base_file, side_suffix = split_map_side_suffix(path)
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
                    min(order[item.file_path] for item in side_maps),
                    _mod_level(base_file, tuple(side_maps)),
                )
            )
    for map_file in maps:
        path = map_file.file_path
        if path in consumed:
            continue
        consumed.add(path)
        ordered_levels.append((order[path], _mod_level(path, (map_file,))))
    return tuple(level for _, level in sorted(ordered_levels, key=lambda item: item[0]))


def assemble_vanilla_levels(maps: Iterable[Map]) -> tuple[Level, ...]:
    """Assemble original Game maps using Celeste's numeric H/X naming convention."""
    grouped: dict[str, dict[LevelSide, Map]] = {}
    level_keys: dict[str, str] = {}
    independent: list[Level] = []
    for map_file in maps:
        path = map_file.file_path
        stem = path.stem
        if stem == _LOST_LEVELS_STEM:
            level_id = _LOST_LEVELS_LEVEL
            sid_stem = stem
            side = LevelSide.A
        elif (match := _VANILLA_MAP_PATTERN.fullmatch(stem)) is not None:
            level_id = match['level']
            sid_stem = f'{level_id}-{match["name"]}'
            side = LevelSide[_VANILLA_SIDE_BY_SUFFIX.get(match['side'], 'A')]
        else:
            independent.append(
                Level(
                    sid=local_sid_for_map_file(path),
                    maps=(map_file,),
                )
            )
            continue
        grouped.setdefault(level_id, {})[side] = map_file
        level_keys[level_id] = f'Celeste/{sid_stem}'
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
            levels.append(Level(sid=level_keys[level_id], maps=tuple(side_maps)))
        independent.extend(
            Level(
                sid=f'Celeste/{map_file.file_path.stem}',
                maps=(map_file,),
            )
            for side, map_file in entries.items()
            if side not in consumed
        )
    levels.extend(independent)
    return tuple(levels)


def _mod_level(path: ContentPath, maps: tuple[Map, ...]) -> Level:
    return Level(
        sid=local_sid_for_map_file(path),
        maps=maps,
    )


def _side_path(base_path: ContentPath, side: LevelSide) -> ContentPath:
    return base_path.with_stem(f'{base_path.stem}-{side.name}')
