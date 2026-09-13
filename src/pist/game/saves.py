"""Read per-map statistics from native save files."""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from xml.etree import ElementTree

from pist.game.dialog import map_base_file_and_side
from pist.models import LocalMap
from pist.time import Time

SAVES_DIRNAME = 'Saves'
SAVE_EXT = '.celeste'
MOD_SAVE_DATA_SUFFIX = '-modsavedata'
AREA_STATS_PATHS = (
    'LevelSets/LevelSetStats/Areas/AreaStats',
    'LevelSetRecycleBin/LevelSetStats/Areas/AreaStats',
)
MODE_INDEX = {None: 0, 'B': 1, 'C': 2}


def sid_for_map_file(map_file: str) -> str:
    """Return the save-file SID belonging to a Mod's map file."""
    base_file, _ = map_base_file_and_side(map_file)
    parts = Path(base_file).with_suffix('').parts
    if not parts or parts[0].casefold() != 'maps':
        raise ValueError(f'Not a map file path: {map_file!r}')
    return '/'.join(parts[1:])


@dataclass(frozen=True, slots=True)
class MapStats:
    """Native accumulated statistics for one SID and map side."""

    time_played: Time
    deaths: int
    completed: bool = False
    single_run_completed: bool = False

    @property
    def is_recorded(self) -> bool:
        """Return whether the game has recorded non-zero play time for this map."""
        return self.time_played.total_milliseconds > 0


@dataclass(frozen=True, slots=True)
class SaveSlot:
    """Map statistics read from one save slot."""

    number: int
    _map_stats: dict[tuple[str, int], MapStats]

    def get_map_stats(self, map_info: LocalMap) -> MapStats | None:
        """Return the selected map's native stats, if the slot knows its SID."""
        return self._map_stats.get(
            (sid_for_map_file(map_info.file_path), MODE_INDEX[map_info.side])
        )


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
        for path in paths:
            if path.is_file():
                for key, value in self._read_path(path):
                    stats.setdefault(key, value)
        return SaveSlot(number, stats)

    def map_progress(self, map_file: str) -> int:
        """Return 0 single-run complete, 1 complete, 2 entered, or 3 unrecorded."""
        base_file, side = map_base_file_and_side(map_file)
        map_key = (sid_for_map_file(base_file), MODE_INDEX[side])
        return self._map_progress.get(map_key, 3)

    @cached_property
    def _map_progress(self) -> dict[tuple[str, int], int]:
        """Index every map once, avoiding repeated XML parsing during map sorting."""
        progress: dict[tuple[str, int], int] = {}
        for number in self.available_numbers():
            for map_key, stats in self.load(number)._map_stats.items():
                value = (
                    0
                    if stats.single_run_completed
                    else 1
                    if stats.completed
                    else 2
                    if stats.is_recorded
                    else 3
                )
                progress[map_key] = min(progress.get(map_key, 3), value)
        return progress

    def _paths(self, number: int) -> tuple[Path, Path]:
        stem = str(number)
        return (
            self._saves_dir / f'{stem}{SAVE_EXT}',
            self._saves_dir / f'{stem}{MOD_SAVE_DATA_SUFFIX}{SAVE_EXT}',
        )

    @staticmethod
    def _read_path(path: Path) -> Iterable[tuple[tuple[str, int], MapStats]]:
        try:
            root = ElementTree.fromstring(path.read_bytes())
        except (OSError, ElementTree.ParseError) as error:
            raise ValueError(f'Invalid Game save file: {path!r}') from error
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
            time_played = Time.from_filetime(int(mode.attrib['TimePlayed']))
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
        )

    @staticmethod
    def _bool_attr(element: ElementTree.Element, name: str, path: Path) -> bool:
        value = element.get(name, 'false').casefold()
        if value == 'true':
            return True
        if value == 'false':
            return False
        raise ValueError(f'Invalid {name} value in Game save file: {path!r}')
