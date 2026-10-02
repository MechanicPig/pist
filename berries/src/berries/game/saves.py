"""Read per-map statistics from native save files."""

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml
from pydantic import Field, ValidationError

from berries.game.content import ContentPath
from berries.game.duration import Duration
from berries.game.levels import Level, LevelSide, local_sid_for_map_file
from berries.game.maps import split_map_side_suffix
from berries.map_entity_id import MapEntityID
from berries.models import FrozenExternalModel

SAVES_DIRNAME = 'Saves'
SAVE_EXT = '.celeste'
MOD_SAVE_DATA_SUFFIX = '-modsavedata'
SAVE_MAP_STATS_PATHS = (
    'Areas/AreaStats',
    'LevelSets/LevelSetStats/Areas/AreaStats',
    'LevelSetRecycleBin/LevelSetStats/Areas/AreaStats',
)
SIDE_INDEX = {LevelSide.A: 0, LevelSide.B: 1, LevelSide.C: 2}
SERIALIZED_SIDE_INDEX = {'Normal': 0, 'BSide': 1, 'CSide': 2}


def settings_dir(game_dir: Path) -> Path:
    """Return Everest's ``PathSettings`` directory for this Game installation.

    Everest lets ``EVEREST_SAVEPATH`` relocate saves. Otherwise it follows the
    platform-specific ``UserIO`` convention, with the Game directory as the
    Windows fallback.
    """
    if path := os.environ.get('EVEREST_SAVEPATH'):
        return Path(path) / SAVES_DIRNAME
    if sys.platform.startswith('linux'):
        if path := os.environ.get('XDG_DATA_HOME'):
            return Path(path) / 'Celeste' / SAVES_DIRNAME
        return Path.home() / '.local' / 'share' / 'Celeste' / SAVES_DIRNAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Celeste' / SAVES_DIRNAME
    return game_dir / SAVES_DIRNAME


def _save_path(saves_dir: Path, name: str) -> Path:
    """Return the on-disk path for one extensionless ``UserIO`` save name."""
    return saves_dir / f'{name}{SAVE_EXT}'


def _save_slot_name(number: int) -> str:
    """Mirror Everest's ``SaveData.GetFilename`` slot-name mapping."""
    return 'debug' if number == -1 else str(number)


def save_slot_path(saves_dir: Path, number: int) -> Path:
    """Return the native save path for one regular or debug slot."""
    return _save_path(saves_dir, _save_slot_name(number))


def mod_save_data_path(saves_dir: Path, number: int) -> Path:
    """Return Everest's backup path for modded data embedded in a save slot."""
    return _save_path(saves_dir, f'{_save_slot_name(number)}{MOD_SAVE_DATA_SUFFIX}')


def mod_save_path(saves_dir: Path, number: int, mod_name: str) -> Path:
    """Return the default per-module save path used by Everest."""
    return _save_path(saves_dir, f'{_save_slot_name(number)}-modsave-{mod_name}')


class CollabUtils2Save(FrozenExternalModel):
    """The CollabUtils2 save-data subset needed for resumable submissions."""

    sessions_per_level: dict[str, str] = Field(
        default_factory=dict,
        validation_alias='SessionsPerLevel',
    )


class MapProgress(IntEnum):
    """Sort order for one map's recorded progress."""

    SINGLE_RUN_COMPLETED = 0
    COMPLETED = 1
    ENTERED = 2
    UNRECORDED = 3


@dataclass(frozen=True, slots=True)
class MapStats:
    """Native accumulated statistics for one SID and map side."""

    time_played: Duration
    deaths: int
    completed: bool = False
    single_run_completed: bool = False
    cassette_collected: bool = False
    heart_collected: bool = False
    collected_strawberries: frozenset[MapEntityID] = frozenset()

    @property
    def is_recorded(self) -> bool:
        """Return whether the game has recorded non-zero play time for this map."""
        return self.time_played.total_milliseconds > 0

    @property
    def progress(self) -> MapProgress:
        """Return this map's progress rank for map-list ordering."""
        if not self.is_recorded:
            return MapProgress.UNRECORDED
        if self.single_run_completed:
            return MapProgress.SINGLE_RUN_COMPLETED
        if self.completed:
            return MapProgress.COMPLETED
        return MapProgress.ENTERED


@dataclass(frozen=True, slots=True)
class SaveSlot:
    """Map statistics read from one save slot."""

    number: int
    _map_stats: dict[tuple[str, int], MapStats]
    _current_map: tuple[str, int] | None = None
    _collab_session_maps: frozenset[tuple[str, int]] = frozenset()

    def get_map_stats(self, level: Level, side: LevelSide) -> MapStats | None:
        """Return the selected map's native stats, if the slot knows its SID."""
        return self._map_stats.get((level.sid, SIDE_INDEX[side]))

    def map_progress(self, level: Level, side: LevelSide) -> MapProgress:
        """Return this slot's recorded progress for one map."""
        stats = self.get_map_stats(level, side)
        return MapProgress.UNRECORDED if stats is None else stats.progress

    def is_in_progress(self, level: Level, side: LevelSide) -> bool:
        """Return whether the save retains an active session for this map.

        Native and Everest saves retain an active ``CurrentSession`` while the
        player is inside a map. CollabUtils2 additionally preserves an unfinished
        submission in ``SessionsPerLevel`` after returning the player to its lobby.
        """
        map_key = (level.sid, SIDE_INDEX[side])
        return map_key == self._current_map or map_key in self._collab_session_maps


class SaveReader:
    """Read native ``.celeste`` saves without changing them."""

    def __init__(self, game_dir: Path) -> None:
        self._saves_dir = settings_dir(game_dir)

    def available_numbers(self) -> list[int]:
        """Return native save-slot numbers currently present on disk."""
        if not self._saves_dir.is_dir():
            return []
        numbers = {
            int(stem)
            for path in self._saves_dir.iterdir()
            if path.is_file()
            if path.suffix.casefold() == SAVE_EXT
            if (stem := path.stem.removesuffix(MOD_SAVE_DATA_SUFFIX)).isdigit()
        }
        return sorted(numbers)

    def load(self, number: int) -> SaveSlot:
        """Load the ordinary and Everest Mod save data for one slot.

        The ordinary save takes precedence if both files contain a statistic
        for the same SID and side; ``-modsavedata`` supplies missing entries.
        """
        if number < 0:
            raise ValueError(f'Save slot number must be non-negative: {number}')
        if not self._saves_dir.is_dir():
            raise ValueError(f'Game Saves directory does not exist: {self._saves_dir!r}')
        stats: dict[tuple[str, int], MapStats] = {}
        paths = self._paths(number)
        if not any(path.is_file() for path in paths):
            raise ValueError(f'Game save slot does not exist: {number}')
        current_map = None
        for i, path in enumerate(paths):
            if path.is_file():
                root = self._parse_path(path)
                session_name = 'CurrentSession' if i == 0 else 'CurrentSession_Safe'
                if (session_map := self._current_session_map(root, path, session_name)) is not None:
                    current_map = session_map
                for key, value in self._read_root(root, path):
                    stats.setdefault(key, value)
        return SaveSlot(number, stats, current_map, self._collab_session_maps(number))

    def map_progress(self, map_file: ContentPath) -> MapProgress:
        """Return the best known progress for one map across all save slots."""
        base_file, side_suffix = split_map_side_suffix(map_file)
        local_sid = local_sid_for_map_file(base_file)
        local_side = LevelSide.A if side_suffix is None else LevelSide(side_suffix)
        map_key = (local_sid, SIDE_INDEX[local_side])
        return self._map_progress.get(map_key, MapProgress.UNRECORDED)

    @cached_property
    def _map_progress(self) -> dict[tuple[str, int], MapProgress]:
        """Index every map once, avoiding repeated XML parsing during map sorting."""
        progress: dict[tuple[str, int], MapProgress] = {}
        for number in self.available_numbers():
            for map_key, stats in self.load(number)._map_stats.items():
                value = stats.progress
                progress[map_key] = min(progress.get(map_key, MapProgress.UNRECORDED), value)
        return progress

    def _paths(self, number: int) -> tuple[Path, Path]:
        return (
            save_slot_path(self._saves_dir, number),
            mod_save_data_path(self._saves_dir, number),
        )

    @staticmethod
    def _parse_path(path: Path) -> ET.Element:
        try:
            return ET.fromstring(path.read_bytes())
        except (OSError, ET.ParseError) as error:
            raise ValueError(f'Invalid Game save file: {path!r}') from error

    @staticmethod
    def _read_root(root: ET.Element, path: Path) -> Iterator[tuple[tuple[str, int], MapStats]]:
        for stats_path in SAVE_MAP_STATS_PATHS:
            for area_stats in root.findall(stats_path):
                sid = area_stats.get('SID')
                if sid is None:
                    continue
                sides = area_stats.findall('Modes/AreaModeStats')
                for i, side_stats in enumerate(sides[:3]):
                    yield (
                        (sid, i),
                        SaveReader._parse_map_stats(area_stats, side_stats, path),
                    )

    @staticmethod
    def _parse_map_stats(area: ET.Element, side: ET.Element, path: Path) -> MapStats:
        try:
            time_played = Duration(int(side.attrib['TimePlayed']))
            deaths = int(side.attrib['Deaths'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f'Invalid map statistics in Game save file: {path!r}') from error
        if deaths < 0:
            raise ValueError(f'Negative death count in Game save file: {path!r}')
        return MapStats(
            time_played=time_played,
            deaths=deaths,
            completed=SaveReader._bool_attr(side, 'Completed', path),
            single_run_completed=SaveReader._bool_attr(side, 'SingleRunCompleted', path),
            cassette_collected=SaveReader._bool_attr(area, 'Cassette', path),
            heart_collected=SaveReader._bool_attr(side, 'HeartGem', path),
            collected_strawberries=SaveReader._collected_strawberries(side, path),
        )

    @staticmethod
    def _collected_strawberries(side: ET.Element, path: Path) -> frozenset[MapEntityID]:
        """Read the per-instance strawberry IDs saved for one map side."""
        collected: set[MapEntityID] = set()
        for entity in side.findall('Strawberries/EntityID'):
            key = entity.get('Key')
            if key is None:
                raise ValueError(f'Missing collected entity ID in Game save file: {path!r}')
            try:
                collected.add(MapEntityID.parse(key))
            except ValueError as error:
                raise ValueError(
                    f'Invalid collected entity ID in Game save file: {path!r}'
                ) from error
        return frozenset(collected)

    @staticmethod
    def _current_session_map(
        root: ET.Element, path: Path, session_name: str
    ) -> tuple[str, int] | None:
        """Read the active map from a native or Everest session, if one is saved."""
        session = root.find(session_name)
        if session is None or not SaveReader._bool_attr(session, 'InArea', path):
            return None
        area = session.find('Area')
        if area is None:
            raise ValueError(f'Missing current session Level in Game save file: {path!r}')
        sid = area.get('SID')
        if sid is None:
            raise ValueError(f'Missing current session SID in Game save file: {path!r}')
        return sid, SaveReader._serialized_side_index(area.get('Mode'), path)

    def _collab_session_maps(self, number: int) -> frozenset[tuple[str, int]]:
        """Read CollabUtils2 maps saved through its return-to-lobby feature."""
        path = mod_save_path(self._saves_dir, number, 'CollabUtils2')
        if not path.is_file():
            return frozenset()
        try:
            save = CollabUtils2Save.model_validate(
                yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
            )
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValidationError) as error:
            raise ValueError(f'Invalid CollabUtils2 save file: {path!r}') from error
        maps: set[tuple[str, int]] = set()
        for sid, session in save.sessions_per_level.items():
            try:
                area = ET.fromstring(session).find('Area')
            except ET.ParseError as error:
                raise ValueError(f'Invalid CollabUtils2 session in save file: {path!r}') from error
            if area is None:
                raise ValueError(f'Missing CollabUtils2 session Level in save file: {path!r}')
            maps.add((sid, self._serialized_side_index(area.get('Mode'), path)))
        return frozenset(maps)

    @staticmethod
    def _serialized_side_index(value: str | None, path: Path) -> int:
        """Convert the save protocol's ``AreaMode`` name to a Level Side index."""
        if value is None:
            return 0
        try:
            return SERIALIZED_SIDE_INDEX[value]
        except KeyError as error:
            raise ValueError(f'Invalid Level Side in Game save file: {path!r}') from error

    @staticmethod
    def _bool_attr(element: ET.Element, name: str, path: Path) -> bool:
        value = element.get(name, 'false').casefold()
        if value == 'true':
            return True
        if value == 'false':
            return False
        raise ValueError(f'Invalid {name} value in Game save file: {path!r}')
