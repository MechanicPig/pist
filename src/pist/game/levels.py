"""Assemble active map assets into community-facing levels and sides."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pist.game import dialog
from pist.game.content import ContentEntry, ContentPath, GameContent
from pist.game.map_source import MapSource
from pist.game.maps import MapInfo
from pist.game.mods import InstalledMod

_VANILLA_MAP_PATTERN = re.compile(r'(?P<level>\d+)(?P<side>[HX])?-(?P<name>.+)')
_VANILLA_SIDE_BY_SUFFIX = {'H': 'B', 'X': 'C'}
_LOST_LEVELS_STEM = 'LostLevels'
_LOST_LEVELS_LEVEL = '10'


class LevelSide(StrEnum):
    """One playable side of a community-facing level."""

    A = 'A'
    B = 'B'
    C = 'C'


@dataclass(frozen=True, slots=True)
class LoadedMap(ABC):
    """One active map asset after Everest content override resolution."""

    info: MapInfo

    @property
    @abstractmethod
    def source(self) -> MapSource:
        """Return whether this asset comes from Game Content or a Mod."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the user-facing name of the concrete content source."""

    @property
    def map_info(self) -> MapInfo:
        """Return the scanned asset metadata."""
        return self.info

    @abstractmethod
    def open_content(self) -> ContentEntry:
        """Open the content tree that supplies this asset."""


@dataclass(frozen=True, slots=True)
class LoadedVanillaMap(LoadedMap):
    """One active map supplied by original Game Content."""

    content: GameContent

    @property
    def source(self) -> MapSource:
        return MapSource.VANILLA

    @property
    def source_name(self) -> str:
        return '原版'

    def open_content(self) -> ContentEntry:
        return self.content.open()


@dataclass(frozen=True, slots=True)
class LoadedModMap(LoadedMap):
    """One active map supplied by an installed Mod."""

    mod: InstalledMod

    @property
    def source(self) -> MapSource:
        return MapSource.MOD

    @property
    def source_name(self) -> str:
        return self.mod.metadata_name

    def open_content(self) -> ContentEntry:
        return self.mod.open()


@dataclass(frozen=True, slots=True)
class Level:
    """One Campaign level mapping each available side to its active map asset."""

    sid: str
    dialog_key: str
    maps_by_side: Mapping[LevelSide, LoadedMap]

    def __post_init__(self) -> None:
        maps = dict(self.maps_by_side)
        if LevelSide.A not in maps:
            raise ValueError('A level must have an A-side map.')
        object.__setattr__(self, 'maps_by_side', MappingProxyType(maps))

    def map(self, side: LevelSide) -> LoadedMap | None:
        """Return the active map occupying ``side``, if present."""
        return self.maps_by_side.get(side)


def map_names(
    level: Level,
    side: LevelSide,
    dialogs: Mapping[str, Mapping[str, str]],
) -> dialog.LocalizedNames:
    """Return current localized names from the final merged Dialog mapping."""
    return {
        language: _side_name(name, side)
        for language, entries in dialogs.items()
        if (name := entries.get(level.dialog_key)) is not None
    }


def map_dialog_texts(
    level: Level,
    dialogs: Mapping[str, Mapping[str, str]],
    suffix: str,
) -> dialog.LocalizedNames:
    """Return localized auxiliary text for one final level identity."""
    key = f'{level.dialog_key}_{suffix}'
    return {
        language: value
        for language, entries in dialogs.items()
        if (value := entries.get(key)) is not None
    }


def map_fallback_name(level: Level, side: LevelSide) -> str:
    """Return the Game-style fallback name for one assembled map side."""
    name = dialog.default_map_name(level.maps_by_side[LevelSide.A].info.file_path)
    return _side_name(name, side)


def map_display_name(
    level: Level,
    side: LevelSide,
    dialogs: Mapping[str, Mapping[str, str]],
    languages: Iterable[str],
) -> str:
    """Resolve one map name at its final Dialog-use boundary."""
    return dialog.localized_name(map_names(level, side, dialogs), languages) or map_fallback_name(
        level, side
    )


def assemble_mod_levels(maps: tuple[LoadedModMap, ...]) -> tuple[Level, ...]:
    """Assemble conventional Mod A/B/C files without dropping malformed combinations."""
    by_path = {loaded_map.info.file_path: loaded_map for loaded_map in maps}
    order = {loaded_map.info.file_path: index for index, loaded_map in enumerate(maps)}
    consumed: set[ContentPath] = set()
    ordered_levels: list[tuple[int, Level]] = []
    for loaded_map in maps:
        path = loaded_map.info.file_path
        base_file, side_suffix = dialog.split_map_side_suffix(path)
        if side_suffix is not None:
            continue
        b_path = _side_path(base_file, LevelSide.B)
        c_path = _side_path(base_file, LevelSide.C)
        if (b_map := by_path.get(b_path)) is not None:
            maps_by_side = {LevelSide.A: loaded_map, LevelSide.B: b_map}
            consumed.update((path, b_path))
            if (c_map := by_path.get(c_path)) is not None:
                maps_by_side[LevelSide.C] = c_map
                consumed.add(c_path)
            ordered_levels.append(
                (
                    min(order[item.info.file_path] for item in maps_by_side.values()),
                    _mod_level(base_file, maps_by_side),
                )
            )
    for loaded_map in maps:
        path = loaded_map.info.file_path
        if path in consumed:
            continue
        consumed.add(path)
        ordered_levels.append((order[path], _mod_level(path, {LevelSide.A: loaded_map})))
    return tuple(level for _, level in sorted(ordered_levels, key=lambda item: item[0]))


def assemble_vanilla_levels(maps: tuple[LoadedVanillaMap, ...]) -> tuple[Level, ...]:
    """Assemble original Game maps using Celeste's numeric H/X naming convention."""
    grouped: dict[str, dict[LevelSide, LoadedVanillaMap]] = {}
    identities: dict[str, tuple[str, str]] = {}
    independent: list[Level] = []
    for loaded_map in maps:
        path = loaded_map.info.file_path
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
                    maps_by_side={LevelSide.A: loaded_map},
                )
            )
            continue
        grouped.setdefault(level_id, {})[side] = loaded_map
        identities[level_id] = (f'Celeste/{sid_stem}', f'AREA_{level_id}')
    levels = [
        Level(
            sid=identities[level_id][0],
            dialog_key=identities[level_id][1],
            maps_by_side=dict(sorted(entries.items())),
        )
        for level_id, entries in grouped.items()
    ]
    levels.extend(independent)
    return tuple(levels)


def _mod_level(path: ContentPath, maps_by_side: Mapping[LevelSide, LoadedMap]) -> Level:
    return Level(
        sid=_sid(path),
        dialog_key=dialog.dialog_key_for_map_file(path),
        maps_by_side=maps_by_side,
    )


def _sid(path: ContentPath) -> str:
    return '/'.join(path.with_suffix('').parts[1:])


def _side_path(base_path: ContentPath, side: LevelSide) -> ContentPath:
    return base_path.with_stem(f'{base_path.stem}-{side}')


def _side_name(name: str, side: LevelSide) -> str:
    return name if side is LevelSide.A else f'{name} {side.value}'
