"""Explicit application projections for synthetic UI fixtures, without file parsing."""

from collections.abc import Callable, Mapping

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.rooms import RoomStats, room_key
from pist.saves import MapStats, SaveProgress, SaveProgressReader


class MemorySaveReader(SaveProgressReader):
    """Supply synthetic snapshots through the same reader interface used by the UI."""

    def __init__(self, *slots: SaveProgress) -> None:
        self.slots = {slot.number: slot for slot in slots}

    def available_numbers(self) -> list[int]:
        return sorted(self.slots)

    def load(self, number: int) -> SaveProgress:
        return self.slots[number]

    def load_first_clear(self, number: int) -> None:
        return None


def make_save_progress(
    number: int,
    stats: Mapping[tuple[str, int], MapStats],
    current: tuple[str, int] | None = None,
) -> SaveProgress:
    sides = tuple(LevelSide)
    return SaveProgress(
        number,
        {(sid, sides[i]): value for (sid, i), value in stats.items()},
        None if current is None else (current[0], sides[current[1]]),
    )


def make_first_clear_lookup(
    orders: Mapping[str, tuple[str, ...]],
    deaths: Mapping[str, Mapping[str, int]] | None = None,
    times: Mapping[str, Mapping[str, Duration]] | None = None,
) -> Callable[[str, LevelSide], RoomStats | None]:
    deaths = {} if deaths is None else deaths
    times = {} if times is None else times
    keys = orders.keys() | deaths.keys() | times.keys()
    records = {
        key: RoomStats(
            list(orders.get(key, ())), dict(deaths.get(key, {})), dict(times.get(key, {}))
        )
        for key in keys
    }
    return lambda sid, side: records.get(room_key(sid, side))
