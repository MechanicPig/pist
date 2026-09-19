"""Read per-map statistics from native save files."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property
from pathlib import Path
from xml.etree import ElementTree

import yaml
from pydantic import Field, ValidationError

from pist.entities.map_entity_id import MapEntityID
from pist.game.dialog import map_base_file_and_side
from pist.game.duration import Duration
from pist.game.mods import LocalMap
from pist.models import FrozenExternalModel

SAVES_DIRNAME = 'Saves'
SAVE_EXT = '.celeste'
MOD_SAVE_DATA_SUFFIX = '-modsavedata'
COLLAB_UTILS_2_SAVE_FILENAME = '{number}-modsave-CollabUtils2.celeste'
AREA_STATS_PATHS = (
    'LevelSets/LevelSetStats/Areas/AreaStats',
    'LevelSetRecycleBin/LevelSetStats/Areas/AreaStats',
)
MODE_INDEX = {None: 0, 'B': 1, 'C': 2}
AREA_MODE_INDEX = {'Normal': 0, 'BSide': 1, 'CSide': 2}


class CollabUtils2Save(FrozenExternalModel):
    """The CollabUtils2 save-data subset needed for resumable submissions."""

    sessions_per_level: dict[str, str] = Field(
        default_factory=dict,
        validation_alias='SessionsPerLevel',
    )


class MapProgress(IntEnum):
    """Sort order for the best progress recorded for one map across save slots."""

    SINGLE_RUN_COMPLETED = 0
    COMPLETED = 1
    ENTERED = 2
    UNRECORDED = 3


def sid_for_map_file(map_file: str) -> str:
    """Return the save-file SID belonging to a ``Maps``-relative map file."""
    base_file, _ = map_base_file_and_side(map_file)
    parts = Path(base_file).with_suffix('').parts
    if not parts or parts[0].casefold() != 'maps':
        raise ValueError(f'Not a map file path: {map_file!r}')
    return '/'.join(parts[1:])


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


@dataclass(frozen=True, slots=True)
class SaveSlot:
    """Map statistics read from one save slot."""

    number: int
    _map_stats: dict[tuple[str, int], MapStats]
    _current_map: tuple[str, int] | None = None
    _collab_session_maps: frozenset[tuple[str, int]] = frozenset()

    def get_map_stats(self, map_info: LocalMap) -> MapStats | None:
        """Return the selected map's native stats, if the slot knows its SID."""
        return self._map_stats.get(
            (sid_for_map_file(map_info.file_path), MODE_INDEX[map_info.side])
        )

    def is_in_progress(self, map_info: LocalMap) -> bool:
        """Return whether the save retains an active session for this map.

        Native and Everest saves retain an active ``CurrentSession`` while the
        player is inside a map. CollabUtils2 additionally preserves an unfinished
        submission in ``SessionsPerLevel`` after returning the player to its lobby.
        """
        map_key = (sid_for_map_file(map_info.file_path), MODE_INDEX[map_info.side])
        return map_key == self._current_map or map_key in self._collab_session_maps


class SaveReader:
    """Read native ``.celeste`` saves without changing them."""

    def __init__(self, game_dir: Path) -> None:
        self._saves_dir = game_dir / SAVES_DIRNAME

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
        for index, path in enumerate(paths):
            if path.is_file():
                root = self._parse_path(path)
                session_name = 'CurrentSession' if index == 0 else 'CurrentSession_Safe'
                if (session_map := self._current_session_map(root, path, session_name)) is not None:
                    current_map = session_map
                for key, value in self._read_root(root, path):
                    stats.setdefault(key, value)
        return SaveSlot(number, stats, current_map, self._collab_session_maps(number))

    def map_progress(self, map_file: str) -> MapProgress:
        """Return the best known progress for one map across all save slots."""
        base_file, side = map_base_file_and_side(map_file)
        map_key = (sid_for_map_file(base_file), MODE_INDEX[side])
        return self._map_progress.get(map_key, MapProgress.UNRECORDED)

    @cached_property
    def _map_progress(self) -> dict[tuple[str, int], MapProgress]:
        """Index every map once, avoiding repeated XML parsing during map sorting."""
        progress: dict[tuple[str, int], MapProgress] = {}
        for number in self.available_numbers():
            for map_key, stats in self.load(number)._map_stats.items():
                value = (
                    MapProgress.SINGLE_RUN_COMPLETED
                    if stats.single_run_completed
                    else MapProgress.COMPLETED
                    if stats.completed
                    else MapProgress.ENTERED
                    if stats.is_recorded
                    else MapProgress.UNRECORDED
                )
                progress[map_key] = min(progress.get(map_key, MapProgress.UNRECORDED), value)
        return progress

    def _paths(self, number: int) -> tuple[Path, Path]:
        stem = str(number)
        return (
            self._saves_dir / f'{stem}{SAVE_EXT}',
            self._saves_dir / f'{stem}{MOD_SAVE_DATA_SUFFIX}{SAVE_EXT}',
        )

    @staticmethod
    def _parse_path(path: Path) -> ElementTree.Element:
        try:
            return ElementTree.fromstring(path.read_bytes())
        except (OSError, ElementTree.ParseError) as error:
            raise ValueError(f'Invalid Game save file: {path!r}') from error

    @staticmethod
    def _read_root(
        root: ElementTree.Element, path: Path
    ) -> Iterable[tuple[tuple[str, int], MapStats]]:
        for area_path in AREA_STATS_PATHS:
            for area_stats in root.findall(area_path):
                sid = area_stats.get('SID')
                if sid is None:
                    continue
                modes = area_stats.findall('Modes/AreaModeStats')
                for index, mode_stats in enumerate(modes[:3]):
                    yield (
                        (sid, index),
                        SaveReader._parse_map_stats(area_stats, mode_stats, path),
                    )

    @staticmethod
    def _parse_map_stats(
        area: ElementTree.Element, mode: ElementTree.Element, path: Path
    ) -> MapStats:
        try:
            time_played = Duration(int(mode.attrib['TimePlayed']))
            deaths = int(mode.attrib['Deaths'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f'Invalid map statistics in Game save file: {path!r}') from error
        if deaths < 0:
            raise ValueError(f'Negative death count in Game save file: {path!r}')
        return MapStats(
            time_played=time_played,
            deaths=deaths,
            completed=SaveReader._bool_attr(mode, 'Completed', path),
            single_run_completed=SaveReader._bool_attr(mode, 'SingleRunCompleted', path),
            cassette_collected=SaveReader._bool_attr(area, 'Cassette', path),
            heart_collected=SaveReader._bool_attr(mode, 'HeartGem', path),
            collected_strawberries=SaveReader._collected_strawberries(mode, path),
        )

    @staticmethod
    def _collected_strawberries(mode: ElementTree.Element, path: Path) -> frozenset[MapEntityID]:
        """Read the per-instance strawberry IDs saved for one map mode."""
        collected: set[MapEntityID] = set()
        for entity in mode.findall('Strawberries/EntityID'):
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
        root: ElementTree.Element, path: Path, session_name: str
    ) -> tuple[str, int] | None:
        """Read the active map from a native or Everest session, if one is saved."""
        session = root.find(session_name)
        if session is None or not SaveReader._bool_attr(session, 'InArea', path):
            return None
        area = session.find('Area')
        if area is None:
            raise ValueError(f'Missing current session area in Game save file: {path!r}')
        sid = area.get('SID')
        if sid is None:
            raise ValueError(f'Missing current session SID in Game save file: {path!r}')
        return sid, SaveReader._area_mode_index(area.get('Mode'), path)

    def _collab_session_maps(self, number: int) -> frozenset[tuple[str, int]]:
        """Read CollabUtils2 maps saved through its return-to-lobby feature."""
        path = self._saves_dir / COLLAB_UTILS_2_SAVE_FILENAME.format(number=number)
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
                area = ElementTree.fromstring(session).find('Area')
            except ElementTree.ParseError as error:
                raise ValueError(f'Invalid CollabUtils2 session in save file: {path!r}') from error
            if area is None:
                raise ValueError(f'Missing CollabUtils2 session area in save file: {path!r}')
            maps.add((sid, self._area_mode_index(area.get('Mode'), path)))
        return frozenset(maps)

    @staticmethod
    def _area_mode_index(value: str | None, path: Path) -> int:
        """Convert a serialized ``AreaMode`` name to the native mode index."""
        if value is None:
            return 0
        try:
            return AREA_MODE_INDEX[value]
        except KeyError as error:
            raise ValueError(f'Invalid area mode in Game save file: {path!r}') from error

    @staticmethod
    def _bool_attr(element: ElementTree.Element, name: str, path: Path) -> bool:
        value = element.get(name, 'false').casefold()
        if value == 'true':
            return True
        if value == 'false':
            return False
        raise ValueError(f'Invalid {name} value in Game save file: {path!r}')
