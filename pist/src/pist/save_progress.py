"""Personal map-progress ranking and cross-slot indexes for browsing and auditing."""

from enum import IntEnum
from functools import cached_property

from berries.game.content import ContentPath
from berries.game.levels import LevelSide, MapKey, local_sid_for_map_file
from berries.game.maps import split_map_side_suffix

from .saves import MapStats, SaveProgressReader


class MapProgress(IntEnum):
    """Pist's map-list priority, not a native save-file state."""

    SINGLE_RUN_COMPLETED = 0
    COMPLETED = 1
    ENTERED = 2
    UNRECORDED = 3


def is_recorded(stats: MapStats) -> bool:
    """Treat positive accumulated time as a played map in Pist's workflows."""
    return stats.time_played.total_seconds > 0


def map_progress(stats: MapStats | None) -> MapProgress:
    """Rank a map's recorded statistics for personal list ordering."""
    if stats is None or not is_recorded(stats):
        return MapProgress.UNRECORDED
    if stats.single_run_completed:
        return MapProgress.SINGLE_RUN_COMPLETED
    if stats.completed:
        return MapProgress.COMPLETED
    return MapProgress.ENTERED


class SaveProgressIndex:
    """Lazily rank all save slots for one application workflow.

    The first query reads every available slot once. Results stay fixed until
    refresh(); loading through the borrowed reader does not invalidate this index.
    """

    def __init__(self, reader: SaveProgressReader) -> None:
        self._reader = reader

    def refresh(self) -> None:
        """Discard the index so the next query reads current files."""
        self.__dict__.pop('_progress', None)

    def map_progress(self, sid: str, side: LevelSide) -> MapProgress:
        """Return the best cross-slot rank for an exact final SID and side."""
        return self._progress.get((sid, side), MapProgress.UNRECORDED)

    def local_map_progress(self, map_file: ContentPath) -> MapProgress:
        """Rank an isolated map path using its candidate SID and side.

        This cannot resolve vanilla SIDs or orphaned B/C maps. Use map_progress()
        with the assembled Level's identity when it is available.
        """
        base_file, side_suffix = split_map_side_suffix(map_file)
        sid = local_sid_for_map_file(base_file)
        side = LevelSide.A if side_suffix is None else LevelSide[side_suffix]
        return self.map_progress(sid, side)

    @cached_property
    def _progress(self) -> dict[MapKey, MapProgress]:
        progress: dict[MapKey, MapProgress] = {}
        for number in self._reader.available_numbers():
            for sid, side, stats in self._reader.load(number).iter_map_stats():
                key = sid, side
                progress[key] = min(progress.get(key, MapProgress.UNRECORDED), map_progress(stats))
        return progress
