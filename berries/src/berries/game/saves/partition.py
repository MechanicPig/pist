"""Storage partitions supplied by save parsing and conflict contexts."""

from enum import StrEnum


class ProgressPartition(StrEnum):
    """The storage partition of a progress candidate, not its permanent owner."""

    VANILLA = 'vanilla'
    MODS = 'mods'
    RECYCLED = 'recycled'
