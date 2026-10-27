"""Application save projections and explicit recovery using Berries file APIs."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Self

from berries.game.duration import Duration
from berries.game.levels import LevelSide, MapKey
from berries.game.saves import operations
from berries.game.saves.directory import SavesDir
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.paths import mod_save_path, settings_dir
from berries.game.saves.progress.map import MapProgress
from berries.game.saves.slot import SaveSlot
from berries.map_entity_id import MapEntityID


@dataclass(frozen=True, slots=True)
class MapStats:
    """Pist's immutable browse projection, including the Level's cassette state."""

    time_played: Duration
    deaths: int
    completed: bool = False
    single_run_completed: bool = False
    cassette_collected: bool = False
    heart_collected: bool = False
    collected_strawberries: frozenset[MapEntityID] = frozenset()

    @classmethod
    def from_progress(cls, progress: MapProgress, *, cassette: bool) -> Self:
        return cls(
            progress.time_played,
            progress.deaths,
            progress.completed,
            progress.single_run_completed,
            cassette,
            progress.heart,
            frozenset(progress.strawberries),
        )


@dataclass(frozen=True, slots=True)
class SaveProgress:
    """An indexed browsing snapshot, not an editable file group or another parser."""

    number: int
    map_stats: Mapping[MapKey, MapStats]
    current_map: MapKey | None = None
    collab: CollabUtils2Save | None = field(default=None, repr=False)
    collab_path: Path | None = field(default=None, repr=False)
    _failed_sessions: set[str] = field(default_factory=set, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'map_stats', MappingProxyType(dict(self.map_stats)))

    def iter_map_stats(self) -> Iterator[tuple[str, LevelSide, MapStats]]:
        for (sid, side), stats in self.map_stats.items():
            yield sid, side, stats

    def get_map_stats(self, sid: str, side: LevelSide) -> MapStats | None:
        return self.map_stats.get((sid, side))

    def is_in_progress(
        self,
        sid: str,
        side: LevelSide,
        *,
        on_error: Callable[[ValueError], None] | None = None,
    ) -> bool:
        """Query lazily; a returning callback opts to skip a bad SID for this snapshot."""
        if self.current_map == (sid, side):
            return True
        if self.collab is None or sid in self._failed_sessions:
            return False
        try:
            pending = self.collab.sessions.get(sid)
            if pending is None:
                return False
            session = pending.session
            return session.in_level and session.side is side
        except ValueError as cause:
            error = ValueError(
                f'无法读取存档 {self.number} 的保留会话 {sid!r}（{self.collab_path}）：{cause}'
            )
            error.__cause__ = cause
            if on_error is None:
                raise error from cause
            on_error(error)
            self._failed_sessions.add(sid)
            return False


class SaveProgressReader:
    """Load browsing data with explicit recovery; never write the edited main back."""

    def __init__(self, saves_dir: Path) -> None:
        self.directory = SavesDir(saves_dir)

    @classmethod
    def from_game_dir(cls, game_dir: Path) -> Self:
        return cls(settings_dir(game_dir))

    def available_numbers(self) -> list[int]:
        """Only ordinary slots participate in Pist's personal record workflow."""
        try:
            self.directory.slots.invalidate_cache()
            return [slot.number for slot in self.directory.slots.iter(filter_empty=True)]
        except OSError as cause:
            raise ValueError(f'无法枚举存档目录（{self.directory.path}）：{cause}') from cause

    def load(self, number: int) -> SaveProgress:
        if number < 0:
            raise ValueError('Pist records require an ordinary non-negative save slot number.')
        collab_path = mod_save_path(self.directory.path, number, 'CollabUtils2')
        slot = SaveSlot(self.directory.path, number)
        try:
            main = slot.main()
            try:
                backup = slot.backup()
            except FileNotFoundError:
                backup = None
            if backup is not None:
                operations.restore_mod_progress(main, backup)
            try:
                collab = slot.mod(CollabUtils2Save)
            except FileNotFoundError:
                collab = None
            stats: dict[MapKey, MapStats] = {}
            # Browsing includes disabled history, but active progress wins when
            # both partitions legitimately retain the same SID.
            for levels in (main.flat_levels, main.recycled_flat_levels):
                for level in levels.values():
                    for side, progress in zip(LevelSide, level.maps, strict=False):
                        if progress is None:
                            continue
                        key = level.sid, side
                        if key not in stats:
                            stats[key] = MapStats.from_progress(progress, cassette=level.cassette)
            current = main.current_map
        except OSError as cause:
            raise ValueError(f'无法读取存档 {number}（{self.directory.path}）：{cause}') from cause
        except ValueError as cause:
            details = '\n'.join((str(cause), *getattr(cause, '__notes__', ())))
            raise ValueError(
                f'无法读取存档 {number} 的地图进度（{self.directory.path}）：{details}'
            ) from cause
        return SaveProgress(number, stats, current, collab, collab_path)

    def load_first_clear(self, number: int) -> EndersBlenderSave | None:
        """Read optional first-clear data only when opening a preview."""
        path = mod_save_path(self.directory.path, number, 'EndersBlender')
        try:
            return SaveSlot(self.directory.path, number).mod(EndersBlenderSave)
        except FileNotFoundError:
            return None
        except OSError as cause:
            raise ValueError(f'无法读取初见数据（{path}）：{cause}') from cause
